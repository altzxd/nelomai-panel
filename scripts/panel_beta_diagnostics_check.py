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


class PanelBetaDiagnosticsCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelBetaDiagnosticsCheckFailure("missing admin seed data")
        db.expunge(admin)
        return admin


def stub_page() -> DiagnosticsPageView:
    return DiagnosticsPageView(
        has_report=True,
        overall_status="warning",
        summary="beta diagnostics stub",
        problem_nodes=["Готовность к beta rollout"],
        checks=[
            DiagnosticsCheckView(
                key="beta_readiness",
                title="Готовность к beta rollout",
                status="warning",
                message="Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap’ы.",
                details=[
                    "Runbook: docs/panel_beta_runbook.md",
                    "Не задан production SECRET_KEY.",
                    "Панель всё ещё использует SQLite вместо PostgreSQL.",
                ],
                source_label="Открыть серверы",
                source_url="/admin/servers",
                action_links=[
                    DiagnosticsCheckView.ActionLinkView(
                        label="Настройки панели",
                        url="/admin?tab=settings&settings_view=basic",
                    ),
                    DiagnosticsCheckView.ActionLinkView(
                        label="Backup-настройки",
                        url="/admin?tab=settings&settings_view=backups",
                    ),
                    DiagnosticsCheckView.ActionLinkView(
                        label="Серверы",
                        url="/admin/servers",
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
        raise PanelBetaDiagnosticsCheckFailure(f"/admin/diagnostics returned {response.status_code}: {detail}")

    body = response.text
    required_tokens = [
        "Готовность к beta rollout",
        "Runbook: docs/panel_beta_runbook.md",
        "Не задан production SECRET_KEY.",
        "Панель всё ещё использует SQLite вместо PostgreSQL.",
        "/admin/servers",
        "/admin?tab=settings&settings_view=basic",
        "/admin?tab=settings&amp;settings_view=backups",
    ]
    missing = [token for token in required_tokens if token not in body]
    if missing:
        raise PanelBetaDiagnosticsCheckFailure("beta diagnostics misses tokens: " + ", ".join(missing))
    print("OK: panel beta diagnostics check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelBetaDiagnosticsCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
