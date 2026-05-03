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
from app.schemas import DiagnosticsPageView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class TakTunnelRepairRouteCheckFailure(RuntimeError):
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
            raise TakTunnelRepairRouteCheckFailure("missing admin/tic/tak seed data")
        db.expunge(admin)
        return admin, tic.id, tak.id


def stub_page() -> DiagnosticsPageView:
    return DiagnosticsPageView(
        has_report=True,
        overall_status="ok",
        summary="stub diagnostics",
        problem_nodes=[],
        checks=[],
        recommendations=[],
        recent_incidents=[],
        run_history=[],
    )


def run() -> None:
    admin, tic_id, tak_id = load_fixture()
    captured: list[tuple[int, int]] = []

    def fake_repair(db, actor, *, tic_server_id: int, tak_server_id: int) -> None:
        captured.append((tic_server_id, tak_server_id))

    with patch("app.web.repair_tak_tunnel_pair", side_effect=fake_repair), patch(
        "app.web.run_panel_diagnostics",
        return_value=stub_page(),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/admin/diagnostics/tak-tunnels/repair",
                data={
                    "focused_tic_server_id": str(tic_id),
                    "focused_tak_server_id": str(tak_id),
                },
                headers=auth_headers(admin),
            )

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise TakTunnelRepairRouteCheckFailure(
            f"repair route returned {response.status_code}: {detail}"
        )
    if captured != [(tic_id, tak_id)]:
        raise TakTunnelRepairRouteCheckFailure(
            f"repair route called service with unexpected args: {captured!r}"
        )
    if "stub diagnostics" not in response.text:
        raise TakTunnelRepairRouteCheckFailure("repair route did not render diagnostics response")
    print("OK: tak tunnel repair route check passed")


if __name__ == "__main__":
    try:
        run()
    except TakTunnelRepairRouteCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
