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
from app.schemas import BetaReadinessSummaryView, ServersPageView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelBetaServersSummaryCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelBetaServersSummaryCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_page() -> ServersPageView:
    return ServersPageView(
        servers=[],
        excluded_servers=[],
        pending_bootstrap_tasks=[],
        beta_readiness=BetaReadinessSummaryView(
            status="warning",
            message="Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap’ы.",
            details=[
                "Runbook: docs/panel_beta_runbook.md",
                "Не задан production SECRET_KEY.",
            ],
            settings_url="/admin?tab=settings&settings_view=basic",
            backups_url="/admin?tab=settings&settings_view=backups",
            servers_url="/admin/servers",
        ),
        selected_bucket="active",
        selected_type="all",
        selected_sort="load_desc",
        selected_server=None,
        selected_bootstrap_task_id=None,
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_servers_page_data", return_value=stub_page()):
        with TestClient(app) as client:
            response = client.get("/admin/servers", headers=auth_headers(admin))

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise PanelBetaServersSummaryCheckFailure(f"/admin/servers returned {response.status_code}: {detail}")

    body = response.text
    required_tokens = [
        "Готовность к beta rollout",
        "Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap’ы.",
        "Runbook: docs/panel_beta_runbook.md",
        "Не задан production SECRET_KEY.",
        "/admin/diagnostics",
        "/admin?tab=settings&settings_view=basic",
        "/admin?tab=settings&amp;settings_view=backups",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelBetaServersSummaryCheckFailure("servers beta summary misses tokens: " + ", ".join(missing))
    print("OK: panel beta servers summary check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelBetaServersSummaryCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
