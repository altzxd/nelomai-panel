from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class CleanStartFailure(RuntimeError):
    pass


def run() -> None:
    tmp_dir = ROOT_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    db_path = tmp_dir / "clean-start-check.db"
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
        raise CleanStartFailure(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")

    os.environ["DATABASE_URL"] = database_url

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.main import app
    from app.models import AppSetting, Server, User, UserRole
    from app.services import ensure_default_settings, ensure_seed_data

    with TestClient(app) as client:
        response = client.get("/")
    if response.status_code != 200:
        raise CleanStartFailure(f"startup check failed: expected 200 from /, got {response.status_code}")

    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        db.commit()

        user_count = db.execute(select(User)).scalars().all()
        if len(user_count) < 2:
            raise CleanStartFailure("seed data did not create the minimum users set")

        admin = db.execute(select(User).where(User.role == UserRole.ADMIN)).scalars().first()
        regular_user = db.execute(select(User).where(User.role == UserRole.USER)).scalars().first()
        if admin is None or regular_user is None:
            raise CleanStartFailure("seed data did not create admin and regular user")

        server_count = db.execute(select(Server)).scalars().all()
        if len(server_count) < 2:
            raise CleanStartFailure("seed data did not create baseline Tic/Tak servers")

        setting_keys = set(db.execute(select(AppSetting.key)).scalars().all())
        for required in {
            "dns_server",
            "mtu",
            "keepalive",
            "exclusion_filters_enabled",
            "block_filters_enabled",
            "backups_enabled",
            "backup_storage_path",
        }:
            if required not in setting_keys:
                raise CleanStartFailure(f"default settings miss key {required}")

    print("OK: clean start check passed")


if __name__ == "__main__":
    try:
        run()
    except CleanStartFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
