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
from app.schemas import AdminPageView, BasicSettingsView, BetaReadinessSummaryView, ServerCardView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelBetaOverviewCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelBetaOverviewCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_server_card(*, key: str, name: str, host: str) -> ServerCardView:
    return ServerCardView(
        key=key,
        name=name,
        host=host,
        available=True,
        status="online",
        metrics_note="ok",
        cpu_percent=10.0,
        ram_percent=12.0,
        disk_used_gb=5.0,
        disk_total_gb=20.0,
        disk_percent=25.0,
        traffic_mbps=1.0,
        selected_id=1,
        options=[],
    )


def stub_page() -> AdminPageView:
    return AdminPageView(
        panel_server=stub_server_card(key="panel", name="Panel Server", host="panel.local"),
        tic_server=stub_server_card(key="tic", name="Tic One", host="144.31.109.224"),
        tak_server=stub_server_card(key="tak", name="Tak One", host="194.87.197.51"),
        beta_readiness=BetaReadinessSummaryView(
            status="warning",
            message="Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap'ы.",
            details=[
                "Runbook: docs/panel_beta_runbook.md",
                "Не задан production SECRET_KEY.",
            ],
            settings_url="/admin?tab=settings&settings_view=basic",
            backups_url="/admin?tab=settings&settings_view=backups",
            servers_url="/admin/servers",
        ),
        interfaces=[],
        settings=BasicSettingsView(
            dns_server="1.1.1.1",
            mtu=1280,
            keepalive=25,
            admin_telegram_url="",
            admin_vk_url="",
            admin_email_url="",
            admin_group_url="",
        ),
        filters=[],
        clients=[],
        client_interface_options=[],
        available_tic_servers=[],
        available_tak_servers=[],
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_admin_page_data", return_value=stub_page()):
        with TestClient(app) as client:
            response = client.get("/admin?tab=overview", headers=auth_headers(admin))

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        raise PanelBetaOverviewCheckFailure(f"/admin?tab=overview returned {response.status_code}: {detail}")

    body = response.text
    required_tokens = [
        "Готовность к тестированию",
        "Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap&#39;ы.",
        "Runbook: docs/panel_beta_runbook.md",
        "Не задан production SECRET_KEY.",
        "/admin/diagnostics",
        "/admin?tab=settings&amp;settings_view=basic",
        "/admin?tab=settings&amp;settings_view=backups",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelBetaOverviewCheckFailure("beta overview misses tokens: " + ", ".join(missing))
    print("OK: panel beta overview check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelBetaOverviewCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
