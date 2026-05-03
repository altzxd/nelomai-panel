from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class TakTunnelLogsBacklinkCheckFailure(RuntimeError):
    pass


EVENT_TYPES = [
    "tak_tunnels.cooldown",
    "tak_tunnels.manual_attention_required",
    "tak_tunnels.manual_repaired",
]


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_fixture() -> tuple[User, Server, Server]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        tic = db.execute(select(Server).where(Server.server_type == ServerType.TIC).order_by(Server.id.asc())).scalars().first()
        tak = db.execute(select(Server).where(Server.server_type == ServerType.TAK).order_by(Server.id.asc())).scalars().first()
        if admin is None or tic is None or tak is None:
            raise TakTunnelLogsBacklinkCheckFailure("missing admin/tic/tak seed data")
        db.expunge(admin)
        db.expunge(tic)
        db.expunge(tak)
        return admin, tic, tak


def ensure_audit_events(tic: Server, tak: Server) -> None:
    with SessionLocal() as db:
        for event_type in EVENT_TYPES:
            message = f"Test event for {event_type}"
            exists = (
                db.execute(
                    select(AuditLog)
                    .where(AuditLog.event_type == event_type, AuditLog.server_id == tic.id, AuditLog.message == message)
                    .order_by(AuditLog.id.desc())
                )
                .scalars()
                .first()
            )
            if exists is not None:
                continue
            db.add(
                AuditLog(
                    event_type=event_type,
                    severity="warning",
                    message=message,
                    message_ru=message,
                    server_id=tic.id,
                    created_at=datetime.now(UTC),
                    details=json.dumps(
                        {
                            "tic_server_id": tic.id,
                            "tic_server_name": tic.name,
                            "tak_server_id": tak.id,
                            "tak_server_name": tak.name,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        db.commit()


def run() -> None:
    admin, tic, tak = load_fixture()
    ensure_audit_events(tic, tak)
    expected_link = (
        f"/admin/diagnostics?focused_tic_server_id={tic.id}"
        f"&amp;focused_tak_server_id={tak.id}#check-tak_tunnels"
    )
    with TestClient(app) as client:
        response = client.get(
            f"/admin/logs?server_id={tic.id}&event_type=tak_tunnels.cooldown",
            headers=auth_headers(admin),
        )
    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise TakTunnelLogsBacklinkCheckFailure(f"/admin/logs returned {response.status_code}: {detail}")
    body = response.text
    required_tokens = [
        expected_link,
        f"Открыть диагностику пары {tic.name} → {tak.name}",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise TakTunnelLogsBacklinkCheckFailure("logs page misses backlink tokens: " + ", ".join(missing))
    print("OK: tak tunnel logs backlink check passed")


if __name__ == "__main__":
    try:
        run()
    except TakTunnelLogsBacklinkCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
