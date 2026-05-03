from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.main import app
from app.models import Server, ServerType, User, UserRole
from app.schemas import DiagnosticsFocusedTakTunnelView, DiagnosticsPageView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class TakTunnelFocusedActionsFormCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_fixture() -> tuple[User, int, int]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        tic = db.execute(select(Server).where(Server.server_type == ServerType.TIC).order_by(Server.id.asc())).scalars().first()
        tak = db.execute(select(Server).where(Server.server_type == ServerType.TAK).order_by(Server.id.asc())).scalars().first()
        if admin is None or tic is None or tak is None:
            raise TakTunnelFocusedActionsFormCheckFailure("missing admin/tic/tak seed data")
        db.expunge(admin)
        return admin, tic.id, tak.id


def stub_page(tic_id: int) -> DiagnosticsPageView:
    return DiagnosticsPageView(
        has_report=True,
        overall_status="warning",
        summary="focused action form stub",
        problem_nodes=["Межсерверные туннели Tic ↔ Tak"],
        focused_tak_tunnel=DiagnosticsFocusedTakTunnelView(
            pair_label="tic-alpha → tak-beta",
            status="warning",
            message="Пара требует внимания.",
            details=["Статус агента: cooldown"],
            server_url=f"/admin/servers?bucket=active&selected_server_id={tic_id}",
            auto_recovered_logs_url=f"/admin/logs?event_type=tak_tunnels.auto_recovered&server_id={tic_id}",
            cooldown_logs_url=f"/admin/logs?event_type=tak_tunnels.cooldown&server_id={tic_id}",
            manual_attention_logs_url=f"/admin/logs?event_type=tak_tunnels.manual_attention_required&server_id={tic_id}",
            manual_repair_logs_url=f"/admin/logs?event_type=tak_tunnels.manual_repaired&server_id={tic_id}",
        ),
        checks=[],
        recommendations=[],
        recent_incidents=[],
        run_history=[],
    )


def run() -> None:
    admin, tic_id, tak_id = load_fixture()
    with patch("app.web.run_panel_diagnostics", return_value=stub_page(tic_id)):
        with TestClient(app) as client:
            response = client.get(
                f"/admin/diagnostics?focused_tic_server_id={tic_id}&focused_tak_server_id={tak_id}",
                headers=auth_headers(admin),
            )

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise TakTunnelFocusedActionsFormCheckFailure(
            f"focused diagnostics returned {response.status_code}: {detail}"
        )

    body = response.text
    required_tokens = [
        'action="/admin/diagnostics/tak-tunnels/rotate"',
        'action="/admin/diagnostics/tak-tunnels/clear-backoff"',
        'action="/admin/diagnostics/tak-tunnels/repair"',
        f'name="focused_tic_server_id" value="{tic_id}"',
        f'name="focused_tak_server_id" value="{tak_id}"',
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise TakTunnelFocusedActionsFormCheckFailure(
            "focused diagnostics action forms miss tokens: " + ", ".join(missing)
        )
    print("OK: tak tunnel focused actions form check passed")


if __name__ == "__main__":
    try:
        run()
    except TakTunnelFocusedActionsFormCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
