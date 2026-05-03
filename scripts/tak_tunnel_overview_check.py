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
from app.models import User, UserRole
from app.schemas import DiagnosticsCheckView, DiagnosticsPageView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class TakTunnelOverviewCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise TakTunnelOverviewCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_page() -> DiagnosticsPageView:
    return DiagnosticsPageView(
        has_report=True,
        overall_status="warning",
        summary="tak tunnel overview stub",
        problem_nodes=["Межсерверные туннели Tic ↔ Tak"],
        checks=[
            DiagnosticsCheckView(
                key="tak_tunnels",
                title="Межсерверные туннели Tic ↔ Tak",
                status="warning",
                message="Есть проблемы с общими туннелями Tic/Tak или их не удалось проверить.",
                details=[
                    "Проверено пар Tic/Tak: 2",
                    "Проблемные туннели: tic-a → tak-a (cooldown) · интерфейсы: wg-a",
                    "Автовосстановлены: tic-b → tak-b · интерфейсы: wg-b",
                    "Последние ротации артефактов: tic-b → tak-b · rev 4 · 2026-05-03T12:34:56+00:00",
                    "В cooldown: tic-a → tak-a · retry after 2026-05-03T13:00:00+00:00",
                    "Требуют ручного вмешательства: tic-c → tak-c · неудачных попыток: 5",
                ],
                source_label="Открыть серверы",
                source_url="/admin/servers",
                action_links=[
                    DiagnosticsCheckView.ActionLinkView(
                        label="Логи автовосстановления",
                        url="/admin/logs?event_type=tak_tunnels.auto_recovered",
                    ),
                    DiagnosticsCheckView.ActionLinkView(
                        label="Логи cooldown",
                        url="/admin/logs?event_type=tak_tunnels.cooldown",
                    ),
                    DiagnosticsCheckView.ActionLinkView(
                        label="Логи ручного внимания",
                        url="/admin/logs?event_type=tak_tunnels.manual_attention_required",
                    ),
                    DiagnosticsCheckView.ActionLinkView(
                        label="Логи ручного восстановления",
                        url="/admin/logs?event_type=tak_tunnels.manual_repaired",
                    ),
                ],
            )
        ],
        recommendations=[],
        recent_incidents=[],
        run_history=[],
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.run_panel_diagnostics", return_value=stub_page()):
        with TestClient(app) as client:
            response = client.get("/admin/diagnostics", headers=auth_headers(admin))

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise TakTunnelOverviewCheckFailure(f"/admin/diagnostics returned {response.status_code}: {detail}")

    body = response.text
    required_tokens = [
        "Межсерверные туннели Tic ↔ Tak",
        "Проверено пар Tic/Tak: 2",
        "Проблемные туннели: tic-a → tak-a (cooldown) · интерфейсы: wg-a",
        "Автовосстановлены: tic-b → tak-b · интерфейсы: wg-b",
        "Последние ротации артефактов: tic-b → tak-b · rev 4 · 2026-05-03T12:34:56+00:00",
        "В cooldown: tic-a → tak-a · retry after 2026-05-03T13:00:00+00:00",
        "Требуют ручного вмешательства: tic-c → tak-c · неудачных попыток: 5",
        "/admin/servers",
        "/admin/logs?event_type=tak_tunnels.auto_recovered",
        "/admin/logs?event_type=tak_tunnels.cooldown",
        "/admin/logs?event_type=tak_tunnels.manual_attention_required",
        "/admin/logs?event_type=tak_tunnels.manual_repaired",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise TakTunnelOverviewCheckFailure("tak_tunnels overview misses tokens: " + ", ".join(missing))
    print("OK: tak tunnel overview check passed")


if __name__ == "__main__":
    try:
        run()
    except TakTunnelOverviewCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
