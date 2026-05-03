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
from app.schemas import (
    AdminPageView,
    BasicSettingsView,
    BetaReadinessSummaryView,
    PanelUpdateCheckView,
    ServerAgentUpdateView,
    ServerCardView,
    ServerDetailView,
    ServerListItemView,
    ServersPageView,
)
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelVersionsSummaryCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelVersionsSummaryCheckFailure("missing admin seed data")
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


def stub_admin_page() -> AdminPageView:
    return AdminPageView(
        panel_server=stub_server_card(key="panel", name="Panel Server", host="panel.local"),
        tic_server=stub_server_card(key="tic", name="Tic One", host="144.31.109.224"),
        tak_server=stub_server_card(key="tak", name="Tak One", host="194.87.197.51"),
        beta_readiness=BetaReadinessSummaryView(status="ok", message="ok"),
        panel_update_summary=PanelUpdateCheckView(
            current_version="0.2.0",
            latest_version="0.2.1",
            update_available=True,
            repo_url="https://github.com/altzxd/nelomai-panel.git",
            release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.1",
            message="Update is available",
        ),
        agent_update_summaries=[
            ServerAgentUpdateView(
                server_id=1,
                name="Tic One",
                server_type="tic",
                repository_url="https://github.com/altzxd/nelomai-panel.git",
                status="checked",
                agent_version="0.2.0",
                contract_version="1.0",
                current_version="0.2.0",
                latest_version="0.2.1",
                update_available=True,
                message="Agent update check completed",
            ),
            ServerAgentUpdateView(
                server_id=2,
                name="Tak One",
                server_type="tak",
                repository_url="https://github.com/altzxd/nelomai-panel.git",
                status="checked",
                agent_version="0.2.1",
                contract_version="1.0",
                current_version="0.2.1",
                latest_version="0.2.1",
                update_available=False,
                message="Agent update check completed",
            ),
        ],
        interfaces=[],
        settings=BasicSettingsView(
            current_version="0.2.0",
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


def stub_servers_page() -> ServersPageView:
    return ServersPageView(
        servers=[
            ServerListItemView(
                id=1,
                name="Tic One",
                host="144.31.109.224",
                server_type="tic",
                available=True,
                status="online",
                last_seen_at=None,
                metrics_note="ok",
                ssh_port=22,
                cpu_percent=10.0,
                ram_percent=12.0,
                disk_used_gb=5.0,
                disk_total_gb=20.0,
                disk_percent=25.0,
                traffic_mbps=1.0,
                interface_count=1,
                endpoint_count=0,
                peer_count=1,
                agent_update_status="checked",
                agent_version="0.2.0",
                contract_version="1.0",
                current_version="0.2.0",
                latest_version="0.2.1",
                update_available=True,
            )
        ],
        excluded_servers=[],
        pending_bootstrap_tasks=[],
        tak_tunnel_pairs=[],
        beta_readiness=BetaReadinessSummaryView(status="ok", message="ok"),
        selected_server_agent_update=ServerAgentUpdateView(
            server_id=1,
            name="Tic One",
            server_type="tic",
            repository_url="https://github.com/altzxd/nelomai-panel.git",
            status="checked",
            agent_version="0.2.0",
            contract_version="1.0",
            current_version="0.2.0",
            latest_version="0.2.1",
            update_available=True,
            message="Agent update check completed",
        ),
        selected_view="servers",
        selected_bucket="active",
        selected_type="all",
        selected_sort="load_desc",
        selected_server=ServerDetailView(
            id=1,
            name="Tic One",
            host="144.31.109.224",
            server_type="tic",
            status="online",
            metrics_note="ok",
            ssh_port=22,
            ssh_login="root",
            cpu_percent=10.0,
            ram_percent=12.0,
            disk_used_gb=5.0,
            disk_total_gb=20.0,
            disk_percent=25.0,
            traffic_mbps=1.0,
            interface_count=1,
            endpoint_count=0,
            peer_count=1,
        ),
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_admin_page_data", return_value=stub_admin_page()):
        with patch("app.web.get_servers_page_data", return_value=stub_servers_page()):
            with TestClient(app) as client:
                settings_response = client.get("/admin?tab=settings&settings_view=basic", headers=auth_headers(admin))
                servers_response = client.get("/admin/servers?view=servers&selected_server_id=1", headers=auth_headers(admin))

    if settings_response.status_code != 200:
        raise PanelVersionsSummaryCheckFailure(f"settings page returned {settings_response.status_code}")
    if servers_response.status_code != 200:
        raise PanelVersionsSummaryCheckFailure(f"servers page returned {servers_response.status_code}")

    settings_body = settings_response.text
    settings_tokens = [
        "Версии панели и агентов",
        "Итог:",
        "Панель",
        "Текущая: 0.2.0",
        "Последняя: 0.2.1",
        "Рассинхрон версий",
        "Tic One",
        "TIC · agent: 0.2.0 · contract: 1.0",
        "Tak One",
        "TAK · agent: 0.2.1 · contract: 1.0",
    ]
    missing_settings = [token for token in settings_tokens if token not in settings_body]
    if missing_settings:
        raise PanelVersionsSummaryCheckFailure("settings summary misses tokens: " + ", ".join(missing_settings))

    servers_body = servers_response.text
    server_tokens = [
        "Итог по версиям серверов",
        "Версия агента",
        "Tic One",
        "Agent",
        "Contract",
        "Текущая",
        "Последняя",
        "0.2.0",
        "0.2.1",
        "Рассинхрон версий",
        "Версия агента: 0.2.0 · contract 1.0 · текущая 0.2.0 · последняя 0.2.1 · нужно обновление",
    ]
    missing_servers = [token for token in server_tokens if token not in servers_body]
    if missing_servers:
        raise PanelVersionsSummaryCheckFailure("servers summary misses tokens: " + ", ".join(missing_servers))

    print("OK: panel versions summary check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelVersionsSummaryCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
