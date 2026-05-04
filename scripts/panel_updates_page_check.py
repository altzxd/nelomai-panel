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
from app.schemas import PanelUpdateCheckView, ServerAgentUpdateView, UpdatesPageView
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelUpdatesPageCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelUpdatesPageCheckFailure("missing admin seed user")
        db.expunge(admin)
        return admin


def build_manual_review_item() -> ServerAgentUpdateView:
    return ServerAgentUpdateView(
        server_id=3,
        name="Storage Legacy",
        server_type="storage",
        repository_url="https://github.com/altzxd/nelomai-panel.git",
        status="legacy",
        agent_version="0.0.5",
        contract_version="agent.runtime.v0",
        current_version="0.0.5",
        latest_version="0.2.0",
        update_available=False,
        release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
        message="Agent is on a legacy update path",
    )


def build_bulk_update_item() -> ServerAgentUpdateView:
    return ServerAgentUpdateView(
        server_id=1,
        name="Tic One",
        server_type="tic",
        repository_url="https://github.com/altzxd/nelomai-panel.git",
        status="checked",
        agent_version="0.1.1",
        contract_version="agent.runtime.v1",
        current_version="0.1.1",
        latest_version="0.2.0",
        update_available=True,
        release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
        message="Agent update check completed",
    )


def build_healthy_item() -> ServerAgentUpdateView:
    return ServerAgentUpdateView(
        server_id=2,
        name="Tak One",
        server_type="tak",
        repository_url="https://github.com/altzxd/nelomai-panel.git",
        status="checked",
        agent_version="0.2.0",
        contract_version="agent.runtime.v1",
        current_version="0.2.0",
        latest_version="0.2.0",
        update_available=False,
        release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
        message="Agent update check completed",
    )


def stub_page() -> UpdatesPageView:
    manual_review_item = build_manual_review_item()
    bulk_update_item = build_bulk_update_item()
    healthy_item = build_healthy_item()
    return UpdatesPageView(
        panel_update_summary=PanelUpdateCheckView(
            current_version="0.1.1",
            latest_version="0.2.0",
            update_available=True,
            repo_url="https://github.com/altzxd/nelomai-panel.git",
            release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
            message="Update is available",
        ),
        agent_update_summaries=[manual_review_item, bulk_update_item, healthy_item],
        problem_agent_update_summaries=[manual_review_item, bulk_update_item],
        healthy_agent_update_summaries=[healthy_item],
        update_available_count=2,
        version_issue_count=3,
        attention_only=False,
        bulk_updatable_agent_count=1,
        manual_review_agent_count=1,
        selected_server_type="all",
    )


def stub_attention_page() -> UpdatesPageView:
    page = stub_page()
    page.agent_update_summaries = page.problem_agent_update_summaries[:]
    page.healthy_agent_update_summaries = []
    page.attention_only = True
    return page


def stub_tic_page() -> UpdatesPageView:
    bulk_update_item = build_bulk_update_item()
    return UpdatesPageView(
        panel_update_summary=PanelUpdateCheckView(
            current_version="0.1.1",
            latest_version="0.2.0",
            update_available=True,
            repo_url="https://github.com/altzxd/nelomai-panel.git",
            release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
            message="Update is available",
        ),
        agent_update_summaries=[bulk_update_item],
        problem_agent_update_summaries=[bulk_update_item],
        healthy_agent_update_summaries=[],
        update_available_count=2,
        version_issue_count=2,
        attention_only=False,
        bulk_updatable_agent_count=1,
        manual_review_agent_count=0,
        selected_server_type="tic",
    )


def run() -> None:
    admin = load_admin()
    with patch("app.web.get_updates_page_data", return_value=stub_page()), patch("app.web.has_available_updates", return_value=True):
        with TestClient(app) as client:
            response = client.get("/admin/updates", headers=auth_headers(admin))
    if response.status_code != 200:
        raise PanelUpdatesPageCheckFailure(f"/admin/updates returned {response.status_code}")
    body = response.text
    required_tokens = [
        "/admin/updates",
        "is-warning-state",
        "Версии панели и серверных агентов",
        "Можно обновить массово: 1",
        "Требуют ручного разбора: 1",
        "Панель",
        "server_type=tic",
        "server_type=tak",
        "server_type=storage",
        "Агенты, требующие внимания",
        "Агенты без проблем",
        "Проверить панель",
        "Проверить все серверы",
        "Обновить все",
        "Storage Legacy",
        "Tic One",
        "Tak One",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelUpdatesPageCheckFailure("updates page misses tokens: " + ", ".join(missing))
    if not (body.index("Storage Legacy") < body.index("Tic One") < body.index("Tak One")):
        raise PanelUpdatesPageCheckFailure("updates page does not keep manual-review -> bulk-update -> healthy order")

    with patch("app.web.get_updates_page_data", return_value=stub_tic_page()), patch("app.web.has_available_updates", return_value=True):
        with TestClient(app) as client:
            tic_response = client.get("/admin/updates?server_type=tic", headers=auth_headers(admin))
    if tic_response.status_code != 200:
        raise PanelUpdatesPageCheckFailure(f"/admin/updates?server_type=tic returned {tic_response.status_code}")
    tic_body = tic_response.text
    if "Tic One" not in tic_body:
        raise PanelUpdatesPageCheckFailure("tic-only updates page misses Tic server")
    if "Tak One" in tic_body or "Storage Legacy" in tic_body:
        raise PanelUpdatesPageCheckFailure("tic-only updates page still shows other server types")

    with patch("app.web.get_updates_page_data", return_value=stub_attention_page()), patch("app.web.has_available_updates", return_value=True):
        with TestClient(app) as client:
            attention_response = client.get("/admin/updates?attention_only=1", headers=auth_headers(admin))
    if attention_response.status_code != 200:
        raise PanelUpdatesPageCheckFailure(f"/admin/updates?attention_only=1 returned {attention_response.status_code}")
    attention_body = attention_response.text
    attention_required_tokens = [
        'href="/admin/updates">Показать все</a>',
        "Агенты, требующие внимания",
        "Storage Legacy",
        "Tic One",
    ]
    missing_attention = [token for token in attention_required_tokens if token not in attention_body]
    if missing_attention:
        raise PanelUpdatesPageCheckFailure("attention-only updates page misses tokens: " + ", ".join(missing_attention))
    if "Агенты без проблем" in attention_body:
        raise PanelUpdatesPageCheckFailure("attention-only updates page still shows healthy-agents section")
    if "Tak One" in attention_body:
        raise PanelUpdatesPageCheckFailure("attention-only updates page still shows servers without update issues")
    if not (attention_body.index("Storage Legacy") < attention_body.index("Tic One")):
        raise PanelUpdatesPageCheckFailure("attention-only updates page does not keep manual-review before bulk-updatable agents")
    print("OK: panel updates page check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelUpdatesPageCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
