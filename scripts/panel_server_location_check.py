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
from app.models import AppSetting, User, UserRole
from app.schemas import BetaReadinessSummaryView, ServerDetailView, ServerListItemView, ServersPageView
from app.security import create_access_token
from app.services import (
    PermissionDeniedError,
    _validate_server_create_payload,
    ensure_default_settings,
    ensure_seed_data,
)
from app.schemas import ServerCreate


class PanelServerLocationCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelServerLocationCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_page() -> ServersPageView:
    return ServersPageView(
        servers=[
            ServerListItemView(
                id=1,
                name="Tic One",
                host="144.31.109.224",
                server_type="tic",
                tic_region="europe",
                tic_region_label="Европа",
                location_label="Европа",
                available=True,
                status="online",
                metrics_note="ok",
                ssh_port=22,
                cpu_percent=10.0,
                ram_percent=12.0,
                disk_used_gb=5.0,
                disk_total_gb=20.0,
                disk_percent=25.0,
                traffic_mbps=1.0,
                interface_count=1,
                endpoint_count=1,
                peer_count=1,
            ),
            ServerListItemView(
                id=2,
                name="Tak One",
                host="194.87.197.51",
                server_type="tak",
                tak_country="Finland",
                location_label="Finland",
                available=True,
                status="online",
                metrics_note="ok",
                ssh_port=22,
                cpu_percent=10.0,
                ram_percent=12.0,
                disk_used_gb=5.0,
                disk_total_gb=20.0,
                disk_percent=25.0,
                traffic_mbps=1.0,
                interface_count=0,
                endpoint_count=1,
                peer_count=0,
            ),
        ],
        excluded_servers=[],
        pending_bootstrap_tasks=[],
        tak_tunnel_pairs=[],
        beta_readiness=BetaReadinessSummaryView(status="ok", message="ok"),
        selected_view="servers",
        selected_bucket="active",
        selected_type="all",
        selected_location="Европа",
        selected_sort="load_desc",
        selected_server=ServerDetailView(
            id=1,
            name="Tic One",
            host="144.31.109.224",
            server_type="tic",
            tic_region="europe",
            tic_region_label="Европа",
            location_label="Европа",
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
            endpoint_count=1,
            peer_count=1,
        ),
    )


def check_backend_validation() -> None:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        repo_row = db.get(AppSetting, "nelomai_git_repo")
        if repo_row is None:
            raise PanelServerLocationCheckFailure("nelomai_git_repo setting missing")
        repo_row.value = "https://github.com/altzxd/nelomai-panel.git"
        db.add(repo_row)
        db.commit()

        try:
            _validate_server_create_payload(
                db,
                ServerCreate(
                    name="tic-missing-region",
                    server_type="tic",
                    host="1.1.1.1",
                    ssh_port=22,
                    ssh_login="root",
                    ssh_password="secret",
                ),
            )
        except PermissionDeniedError:
            pass
        else:
            raise PanelServerLocationCheckFailure("tic server creation must require region")

        try:
            _validate_server_create_payload(
                db,
                ServerCreate(
                    name="tak-missing-country",
                    server_type="tak",
                    host="2.2.2.2",
                    ssh_port=22,
                    ssh_login="root",
                    ssh_password="secret",
                ),
            )
        except PermissionDeniedError:
            pass
        else:
            raise PanelServerLocationCheckFailure("tak server creation must require country")


def run() -> None:
    admin = load_admin()
    check_backend_validation()

    with patch("app.web.get_servers_page_data", return_value=stub_page()):
        with TestClient(app) as client:
            response = client.get("/admin/servers?view=servers&location=%D0%95%D0%B2%D1%80%D0%BE%D0%BF%D0%B0", headers=auth_headers(admin))

    if response.status_code != 200:
        raise PanelServerLocationCheckFailure(f"/admin/servers returned {response.status_code}")

    body = response.text
    required_tokens = [
        "Регион / страна",
        "Европа",
        "Finland",
        "Регион",
        "Страна",
        "Выберите регион",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelServerLocationCheckFailure("server location UI misses tokens: " + ", ".join(missing))
    print("OK: panel server location check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelServerLocationCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
