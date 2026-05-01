from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import Base
from app import models  # noqa: F401
from app.runtime_schema import apply_legacy_runtime_schema_updates


class MigrationFailure(RuntimeError):
    pass


REQUIRED_INDEXES = {
    "users": {("login",): True},
    "audit_logs": {("event_type",): False, ("severity",): False, ("created_at",): False},
    "peer_download_links": {("token_id",): True},
    "panel_jobs": {("job_type",): False, ("status",): False, ("created_at",): False},
}

REQUIRED_UNIQUES = {
    "servers": {("name",)},
    "peers": {("interface_id", "slot")},
    "user_resources": {("user_id",)},
    "user_contact_links": {("user_id",)},
}

REQUIRED_FOREIGN_KEYS = {
    "interfaces": {
        ("user_id", "users", "CASCADE"),
        ("tic_server_id", "servers", None),
        ("tak_server_id", "servers", None),
    },
    "peers": {("interface_id", "interfaces", "CASCADE")},
    "user_resources": {("user_id", "users", "CASCADE")},
    "user_contact_links": {("user_id", "users", "CASCADE")},
    "resource_filters": {
        ("user_id", "users", "CASCADE"),
        ("peer_id", "peers", "CASCADE"),
    },
    "peer_download_links": {
        ("peer_id", "peers", "CASCADE"),
        ("created_by_user_id", "users", "SET NULL"),
    },
    "audit_logs": {
        ("actor_user_id", "users", "SET NULL"),
        ("target_user_id", "users", "SET NULL"),
        ("server_id", "servers", "SET NULL"),
    },
    "backup_records": {("created_by_user_id", "users", "SET NULL")},
    "server_bootstrap_tasks": {("server_id", "servers", "SET NULL")},
    "panel_jobs": {("created_by_user_id", "users", "SET NULL")},
}


def _column_tuple(columns: list[str]) -> tuple[str, ...]:
    return tuple(str(column) for column in columns)


def _normalize_ondelete(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value).upper()


def _schema_snapshot(inspector) -> dict[str, object]:
    tables = set(inspector.get_table_names())
    return {
        "tables": tables,
        "columns": {
            table_name: tuple(column["name"] for column in inspector.get_columns(table_name))
            for table_name in tables
        },
        "indexes": {
            table_name: tuple(
                sorted(
                    (
                        index.get("name"),
                        _column_tuple(index.get("column_names", [])),
                        bool(index.get("unique")),
                    )
                    for index in inspector.get_indexes(table_name)
                )
            )
            for table_name in tables
        },
        "uniques": {
            table_name: tuple(
                sorted(
                    _column_tuple(item.get("column_names", []))
                    for item in inspector.get_unique_constraints(table_name)
                )
            )
            for table_name in tables
        },
        "foreign_keys": {
            table_name: tuple(
                sorted(
                    (
                        _column_tuple(item.get("constrained_columns", [])),
                        item.get("referred_table"),
                        _column_tuple(item.get("referred_columns", [])),
                        _normalize_ondelete(item.get("options", {}).get("ondelete")),
                    )
                    for item in inspector.get_foreign_keys(table_name)
                )
            )
            for table_name in tables
        },
    }


def run() -> None:
    tmp_dir = ROOT_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    db_path = tmp_dir / "migration-check.db"
    if db_path.exists():
        db_path.unlink()

    database_url = f"sqlite+pysqlite:///{db_path.as_posix()}"
    env = {**os.environ, "DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise MigrationFailure(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")

    engine = create_engine(database_url, future=True)
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)

    missing_tables = expected_tables - actual_tables
    extra_tables = actual_tables - expected_tables - {"alembic_version"}
    if missing_tables or extra_tables:
        raise MigrationFailure(f"table mismatch: missing={sorted(missing_tables)} extra={sorted(extra_tables)}")

    for table_name, table in Base.metadata.tables.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        expected_columns = {column.name for column in table.columns}
        missing_columns = expected_columns - actual_columns
        extra_columns = actual_columns - expected_columns
        if missing_columns or extra_columns:
            raise MigrationFailure(
                f"column mismatch for {table_name}: missing={sorted(missing_columns)} extra={sorted(extra_columns)}"
            )

    for table_name, required in REQUIRED_INDEXES.items():
        indexes = {
            _column_tuple(index.get("column_names", [])): bool(index.get("unique"))
            for index in inspector.get_indexes(table_name)
        }
        for columns, must_be_unique in required.items():
            if columns not in indexes:
                raise MigrationFailure(f"missing index for {table_name}.{columns}")
            if indexes[columns] != must_be_unique:
                raise MigrationFailure(f"index uniqueness mismatch for {table_name}.{columns}")

    for table_name, required in REQUIRED_UNIQUES.items():
        unique_columns = {
            _column_tuple(item.get("column_names", []))
            for item in inspector.get_unique_constraints(table_name)
        }
        unique_columns.update(
            _column_tuple(index.get("column_names", []))
            for index in inspector.get_indexes(table_name)
            if index.get("unique")
        )
        missing_uniques = required - unique_columns
        if missing_uniques:
            raise MigrationFailure(f"missing unique constraints for {table_name}: {sorted(missing_uniques)}")

    for table_name, required in REQUIRED_FOREIGN_KEYS.items():
        foreign_keys = {
            (
                _column_tuple(item.get("constrained_columns", []))[0],
                str(item.get("referred_table")),
                _normalize_ondelete(item.get("options", {}).get("ondelete")),
            )
            for item in inspector.get_foreign_keys(table_name)
            if item.get("constrained_columns")
        }
        missing_foreign_keys = required - foreign_keys
        if missing_foreign_keys:
            raise MigrationFailure(f"missing foreign keys for {table_name}: {sorted(missing_foreign_keys)}")

    before_runtime_updates = _schema_snapshot(inspect(engine))
    applied_runtime_updates = apply_legacy_runtime_schema_updates(engine)
    after_runtime_updates = _schema_snapshot(inspect(engine))
    if applied_runtime_updates:
        raise MigrationFailure(f"runtime schema updates mutated clean migration: {applied_runtime_updates}")
    if before_runtime_updates != after_runtime_updates:
        raise MigrationFailure("runtime schema updates changed clean migration schema")

    print("OK: migration check passed")


if __name__ == "__main__":
    try:
        run()
    except MigrationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
