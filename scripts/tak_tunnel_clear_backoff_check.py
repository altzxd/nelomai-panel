from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.main import app
from app.models import AppSetting, AuditLog, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import (
    TAK_TUNNEL_REPAIR_STATE_KEY,
    _tak_tunnel_pair_key,
    ensure_default_settings,
    ensure_seed_data,
)


class ClearBackoffCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def ensure_server_pair() -> tuple[int, int, int]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        tic = db.execute(select(Server).where(Server.server_type == ServerType.TIC).order_by(Server.id.asc())).scalars().first()
        tak = db.execute(select(Server).where(Server.server_type == ServerType.TAK).order_by(Server.id.asc())).scalars().first()
        if admin is None or tic is None or tak is None:
            raise ClearBackoffCheckFailure("missing admin/tic/tak seed data")
        pair_key = _tak_tunnel_pair_key(tic.id, tak.id)
        row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
        payload = {}
        if row is not None and row.value:
            try:
                payload = json.loads(row.value)
            except json.JSONDecodeError:
                payload = {}
        payload[pair_key] = {
            "failure_count": 4,
            "last_failure_at": datetime.now(UTC).isoformat(),
            "last_attempt_at": datetime.now(UTC).isoformat(),
            "cooldown_until": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "manual_attention_required": True,
            "last_recovered_at": "",
        }
        if row is None:
            row = AppSetting(key=TAK_TUNNEL_REPAIR_STATE_KEY, value=json.dumps(payload, ensure_ascii=False))
        else:
            row.value = json.dumps(payload, ensure_ascii=False)
        db.add(row)
        db.commit()
        return admin.id, tic.id, tak.id


def load_admin(admin_id: int) -> User:
    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        if admin is None:
            raise ClearBackoffCheckFailure("admin user disappeared")
        db.expunge(admin)
        return admin


def assert_state_cleared(tic_id: int, tak_id: int) -> None:
    pair_key = _tak_tunnel_pair_key(tic_id, tak_id)
    with SessionLocal() as db:
        row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
        payload = {}
        if row is not None and row.value:
            try:
                payload = json.loads(row.value)
            except json.JSONDecodeError:
                payload = {}
        if pair_key in payload:
            raise ClearBackoffCheckFailure("repair state for pair was not cleared")
        log = db.execute(
            select(AuditLog)
            .where(AuditLog.event_type == "tak_tunnels.backoff_cleared", AuditLog.server_id == tic_id)
            .order_by(AuditLog.id.desc())
        ).scalars().first()
        if log is None:
            raise ClearBackoffCheckFailure("tak_tunnels.backoff_cleared audit event missing")
        details = json.loads(log.details or "{}")
        if int(details.get("tic_server_id") or 0) != tic_id or int(details.get("tak_server_id") or 0) != tak_id:
            raise ClearBackoffCheckFailure("backoff_cleared audit event points to wrong pair")


def run() -> None:
    admin_id, tic_id, tak_id = ensure_server_pair()
    admin = load_admin(admin_id)
    with TestClient(app) as client:
        response = client.post(
            "/admin/diagnostics/tak-tunnels/clear-backoff",
            data={
                "focused_tic_server_id": str(tic_id),
                "focused_tak_server_id": str(tak_id),
            },
            headers=auth_headers(admin),
        )
        if response.status_code != 200:
            raise ClearBackoffCheckFailure(
                f"clear-backoff route returned {response.status_code}: {response.text[:300]}"
            )
    assert_state_cleared(tic_id, tak_id)
    print("OK: tak tunnel clear backoff check passed")


if __name__ == "__main__":
    try:
        run()
    except ClearBackoffCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
