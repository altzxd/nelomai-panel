from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
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
from app.schemas import (
    AccessUserView,
    AdminPageView,
    BasicSettingsView,
    ServerCardView,
    ServersPageView,
    TakTunnelPairStateView,
)
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelAccessAndTunnelsViewsCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelAccessAndTunnelsViewsCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_server_card(*, key: str) -> ServerCardView:
    return ServerCardView(
        key=key,
        name=key,
        host=f"{key}.local",
        available=True,
        status="online",
        metrics_note="ok",
        cpu_percent=10.0,
        ram_percent=10.0,
        disk_used_gb=5.0,
        disk_total_gb=20.0,
        disk_percent=25.0,
        traffic_mbps=1.0,
        selected_id=1,
        options=[],
    )


def stub_admin_page() -> AdminPageView:
    return AdminPageView(
        panel_server=stub_server_card(key="panel"),
        tic_server=stub_server_card(key="tic"),
        tak_server=stub_server_card(key="tak"),
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
        access_users=[
            AccessUserView(
                id=11,
                login="expired-user",
                display_name="Expired User",
                expires_at=datetime.now(UTC) - timedelta(days=1),
                is_expired=True,
                communication_channel="https://t.me/expired",
            ),
            AccessUserView(
                id=13,
                login="active-user",
                display_name="Active User",
                expires_at=datetime.now(UTC) + timedelta(days=10),
                is_expired=False,
                communication_channel="https://t.me/active",
            ),
        ],
        access_users_without_expiry=[
            AccessUserView(
                id=12,
                login="no-date-user",
                display_name="No Date User",
                expires_at=None,
                is_expired=False,
                communication_channel="https://t.me/nodate",
            )
        ],
        client_interface_options=[],
        available_tic_servers=[],
        available_tak_servers=[],
    )


def stub_servers_page() -> ServersPageView:
    return ServersPageView(
        servers=[],
        excluded_servers=[],
        pending_bootstrap_tasks=[],
        tak_tunnel_pairs=[
            TakTunnelPairStateView(
                tic_server_id=1,
                tic_server_name="Tic One",
                tak_server_id=2,
                tak_server_name="Tak One",
                pair_label="Tic One → Tak One",
                status="cooldown",
                status_label="ожидает повторную попытку",
                fallback_interface_count=2,
                recovered_interface_count=0,
                failure_count=3,
                cooldown_until=datetime.now(UTC) + timedelta(minutes=5),
                manual_attention_required=False,
                diagnostics_url="/admin/diagnostics?focused_tic_server_id=1&focused_tak_server_id=2#check-tak_tunnels",
            )
        ],
        selected_view="tunnels",
        selected_bucket="active",
        selected_type="all",
        selected_sort="load_desc",
        selected_server=None,
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_admin_page_data", return_value=stub_admin_page()):
        with TestClient(app) as client:
            dated = client.get("/admin?tab=access&access_view=dated", headers=auth_headers(admin))
            no_expiry = client.get("/admin?tab=access&access_view=no_expiry", headers=auth_headers(admin))
    with patch("app.web.get_servers_page_data", return_value=stub_servers_page()):
        with TestClient(app) as client:
            tunnels = client.get("/admin/servers?view=tunnels", headers=auth_headers(admin))

    if dated.status_code != 200 or no_expiry.status_code != 200 or tunnels.status_code != 200:
        raise PanelAccessAndTunnelsViewsCheckFailure("one of access/tunnels pages did not render")

    dated_required = [
        "Истекшие доступы",
        "Активные доступы",
        "Пользователей:",
        "Expired User",
        "Active User",
        "истекло",
        "активен",
        "Связаться",
    ]
    no_expiry_required = [
        "Пользователи без даты окончания",
        "Пользователей:",
        "No Date User",
        "Не задан",
    ]
    tunnels_required = [
        "Туннели",
        "Tic One → Tak One",
        "ожидает повторную попытку",
        "Открыть диагностику пары",
    ]
    for label, body, required in [
        ("dated access", dated.text, dated_required),
        ("no-expiry access", no_expiry.text, no_expiry_required),
        ("tunnels", tunnels.text, tunnels_required),
    ]:
        missing = [token for token in required if token not in body]
        if missing:
            raise PanelAccessAndTunnelsViewsCheckFailure(f"{label} misses tokens: {', '.join(missing)}")
    if dated.text.index("Истекшие доступы") > dated.text.index("Активные доступы"):
        raise PanelAccessAndTunnelsViewsCheckFailure("dated access sections are ordered incorrectly")
    if dated.text.index("Expired User") > dated.text.index("Active User"):
        raise PanelAccessAndTunnelsViewsCheckFailure("expired users must be rendered before active users")
    print("OK: panel access and tunnels views check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelAccessAndTunnelsViewsCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
