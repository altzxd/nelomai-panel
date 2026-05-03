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
from app.schemas import AdminPageView, BasicSettingsView, ClientView, ServerCardView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelClientsWithoutInterfacesCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelClientsWithoutInterfacesCheckFailure("missing admin seed data")
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


def stub_page() -> AdminPageView:
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
        clients=[
            ClientView(
                id=10,
                login="noif",
                display_name="No Interface",
                role=UserRole.USER,
                interface_count=0,
                communication_channel=None,
                can_delete=True,
            )
        ],
        client_interface_options=[],
        available_tic_servers=[],
        available_tak_servers=[],
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_admin_page_data", return_value=stub_page()) as mock_get_admin_page:
        with TestClient(app) as client:
            response = client.get("/admin?tab=clients&client_scope=without_interfaces", headers=auth_headers(admin))

    if response.status_code != 200:
        raise PanelClientsWithoutInterfacesCheckFailure(f"/admin?tab=clients returned {response.status_code}")
    called = mock_get_admin_page.call_args.kwargs
    if called.get("client_scope") != "without_interfaces":
        raise PanelClientsWithoutInterfacesCheckFailure("client_scope did not reach get_admin_page_data")
    body = response.text
    required_tokens = [
        "без интерфейсов",
        "No Interface",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelClientsWithoutInterfacesCheckFailure("clients filter page misses tokens: " + ", ".join(missing))
    print("OK: panel clients without interfaces check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelClientsWithoutInterfacesCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
