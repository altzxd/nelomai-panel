from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class FirstAdminBootstrapCheckFailure(RuntimeError):
    pass


def run() -> None:
    tmp_dir = ROOT_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    db_path = tmp_dir / "first-admin-bootstrap-check.db"
    if db_path.exists():
        db_path.unlink()
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path.as_posix()}"

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.main import app
    from app.models import AppSetting, User, UserRole
    from app.services import INITIAL_ADMIN_TOKEN_SETTING_KEY, INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY

    with TestClient(app) as client:
        with SessionLocal() as db:
            admin = db.execute(select(User).where(User.role == UserRole.ADMIN)).scalars().first()
            if admin is not None:
                raise FirstAdminBootstrapCheckFailure("startup must not auto-create admin user")
            token_row = db.get(AppSetting, INITIAL_ADMIN_TOKEN_SETTING_KEY)
            if token_row is None or not token_row.value.strip():
                raise FirstAdminBootstrapCheckFailure("startup must create initial admin bootstrap token")
            announced_row = db.get(AppSetting, INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY)
            if announced_row is None or not announced_row.value.strip():
                raise FirstAdminBootstrapCheckFailure("startup must mark initial admin bootstrap token as announced")
            token = token_row.value.strip()

        login_page = client.get("/")
        if login_page.status_code != 200:
            raise FirstAdminBootstrapCheckFailure(f"login page returned {login_page.status_code}")
        if "Первый администратор" not in login_page.text:
            raise FirstAdminBootstrapCheckFailure("login page does not mention initial admin bootstrap state")

        bootstrap_page = client.get(f"/bootstrap-admin/{token}")
        if bootstrap_page.status_code != 200:
            raise FirstAdminBootstrapCheckFailure(f"bootstrap admin page returned {bootstrap_page.status_code}")
        if "Создать администратора" not in bootstrap_page.text:
            raise FirstAdminBootstrapCheckFailure("bootstrap admin form is incomplete")

        create_response = client.post(
            f"/bootstrap-admin/{token}",
            data={"login": "altzxd", "password": "Pass1234"},
            follow_redirects=False,
        )
        if create_response.status_code != 303 or create_response.headers.get("location") != "/?registered=1":
            raise FirstAdminBootstrapCheckFailure("bootstrap admin submit did not redirect to login success page")

        used_again = client.get(f"/bootstrap-admin/{token}")
        if used_again.status_code != 404:
            raise FirstAdminBootstrapCheckFailure("used bootstrap admin token must not be reusable")

        with SessionLocal() as db:
            admin = db.execute(select(User).where(User.role == UserRole.ADMIN, User.login == "altzxd")).scalars().first()
            if admin is None:
                raise FirstAdminBootstrapCheckFailure("first admin user was not created")
            token_row = db.get(AppSetting, INITIAL_ADMIN_TOKEN_SETTING_KEY)
            if token_row is not None:
                raise FirstAdminBootstrapCheckFailure("bootstrap admin token must be removed after successful setup")

    print("OK: first admin bootstrap check passed")


if __name__ == "__main__":
    try:
        run()
    except FirstAdminBootstrapCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
