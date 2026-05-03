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


def stub_page() -> UpdatesPageView:
    return UpdatesPageView(
        panel_update_summary=PanelUpdateCheckView(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
            repo_url="https://github.com/altzxd/nelomai-panel.git",
            release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
            message="Update is available",
        ),
        agent_update_summaries=[
            ServerAgentUpdateView(
                server_id=1,
                name="Tic One",
                server_type="tic",
                repository_url="https://github.com/altzxd/nelomai-panel.git",
                status="checked",
                agent_version="0.1.0",
                contract_version="agent.runtime.v1",
                current_version="0.1.0",
                latest_version="0.2.0",
                update_available=True,
                release_url="https://github.com/altzxd/nelomai-panel/releases/tag/0.2.0",
                message="Agent update check completed",
            ),
            ServerAgentUpdateView(
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
            ),
        ],
        update_available_count=2,
        version_issue_count=2,
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
        "Проверить панель",
        "Проверить все серверы",
        "Обновить все",
        "Tic One",
        "Tak One",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelUpdatesPageCheckFailure("updates page misses tokens: " + ", ".join(missing))
    print("OK: panel updates page check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelUpdatesPageCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
