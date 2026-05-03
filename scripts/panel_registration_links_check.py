from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.main import app
from app.models import RegistrationLink, User, UserContactLink, UserRegion, UserResource, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class PanelRegistrationLinksCheckFailure(RuntimeError):
    pass


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise PanelRegistrationLinksCheckFailure("missing admin seed user")
        db.expunge(admin)
        return admin


def latest_registration_link() -> RegistrationLink:
    with SessionLocal() as db:
        link = db.execute(select(RegistrationLink).order_by(RegistrationLink.id.desc())).scalars().first()
        if link is None:
            raise PanelRegistrationLinksCheckFailure("registration link was not created")
        db.expunge(link)
        return link


def assert_registered_user(login: str) -> None:
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.login == login)).scalars().first()
        if user is None:
            raise PanelRegistrationLinksCheckFailure("registered user missing")
        if user.region != UserRegion.EAST:
            raise PanelRegistrationLinksCheckFailure(f"unexpected user region: {user.region}")
        if user.role != UserRole.USER:
            raise PanelRegistrationLinksCheckFailure(f"unexpected user role: {user.role}")
        if user.expires_at is not None:
            raise PanelRegistrationLinksCheckFailure("registered user must not have expires_at by default")
        resources = db.execute(select(UserResource).where(UserResource.user_id == user.id)).scalars().first()
        if resources is None:
            raise PanelRegistrationLinksCheckFailure("registered user resources missing")
        contact = db.execute(select(UserContactLink).where(UserContactLink.user_id == user.id)).scalars().first()
        if contact is None or contact.value != "@panel_reg_contact":
            raise PanelRegistrationLinksCheckFailure("registered user contact missing or invalid")


def assert_link_usage(token_id: str, login: str) -> None:
    with SessionLocal() as db:
        link = db.execute(select(RegistrationLink).where(RegistrationLink.token_id == token_id)).scalars().first()
        if link is None:
            raise PanelRegistrationLinksCheckFailure("used registration link missing")
        if link.used_at is None:
            raise PanelRegistrationLinksCheckFailure("registration link was not marked used")
        used_user = db.get(User, link.used_by_user_id) if link.used_by_user_id is not None else None
        if used_user is None or used_user.login != login:
            raise PanelRegistrationLinksCheckFailure("registration link points to wrong user")


def assert_all_unused_revoked(created_token_id: str, used_token_id: str) -> None:
    with SessionLocal() as db:
        created = db.execute(select(RegistrationLink).where(RegistrationLink.token_id == created_token_id)).scalars().first()
        used = db.execute(select(RegistrationLink).where(RegistrationLink.token_id == used_token_id)).scalars().first()
        if created is None or used is None:
            raise PanelRegistrationLinksCheckFailure("registration links missing during revoke validation")
        if created.revoked_at is None:
            raise PanelRegistrationLinksCheckFailure("unused registration link was not revoked")
        if used.revoked_at is not None:
            raise PanelRegistrationLinksCheckFailure("used registration link must not be revoked by bulk revoke")
        if created.comment != "invite for tester":
            raise PanelRegistrationLinksCheckFailure("registration link comment was not saved")


def run() -> None:
    admin = load_admin()
    login = ""
    token_id = ""
    second_token_id = ""

    with TestClient(app) as client:
        page_response = client.get("/admin/registration-links", headers=auth_headers(admin))
        if page_response.status_code != 200:
            raise PanelRegistrationLinksCheckFailure(f"admin registration-links page returned {page_response.status_code}")

        create_response = client.post("/admin/registration-links", headers=auth_headers(admin), follow_redirects=False)
        if create_response.status_code != 303:
            raise PanelRegistrationLinksCheckFailure(f"link creation returned {create_response.status_code}")
        location = create_response.headers.get("location") or ""
        if not location.startswith("/admin/registration-links?created_link_id="):
            raise PanelRegistrationLinksCheckFailure("link creation redirect is invalid")

        created_link = latest_registration_link()
        token_id = created_link.token_id
        login = f"panelreguser{created_link.id}"

        public_page = client.get(f"/registration/{token_id}")
        if public_page.status_code != 200:
            raise PanelRegistrationLinksCheckFailure(f"public registration page returned {public_page.status_code}")
        if "name=\"region\"" not in public_page.text:
            raise PanelRegistrationLinksCheckFailure("registration form misses region field")

        bare_page = client.get("/registration")
        if bare_page.status_code != 404:
            raise PanelRegistrationLinksCheckFailure("bare /registration path must not be available")

        submit_response = client.post(
            f"/registration/{token_id}",
            data={
                "login": login.upper(),
                "password": "Pass1234",
                "display_name": "Тест 123",
                "communication_channel": "@panel_reg_contact",
                "region": "east",
            },
            follow_redirects=False,
        )
        if submit_response.status_code != 303:
            raise PanelRegistrationLinksCheckFailure(f"registration submit returned {submit_response.status_code}")
        if submit_response.headers.get("location") != "/?registered=1":
            raise PanelRegistrationLinksCheckFailure("registration success redirect is invalid")

        login_page = client.get("/?registered=1")
        if login_page.status_code != 200 or "Аккаунт создан" not in login_page.text:
            raise PanelRegistrationLinksCheckFailure("login page does not show registration success message")

        used_again = client.get(f"/registration/{token_id}")
        if used_again.status_code != 404:
            raise PanelRegistrationLinksCheckFailure("used registration link must return 404")

        second_create_response = client.post("/admin/registration-links", headers=auth_headers(admin), follow_redirects=False)
        if second_create_response.status_code != 303:
            raise PanelRegistrationLinksCheckFailure(f"second link creation returned {second_create_response.status_code}")
        second_link = latest_registration_link()
        second_token_id = second_link.token_id
        if second_token_id == token_id:
            raise PanelRegistrationLinksCheckFailure("registration links must be unique")

        comment_response = client.post(
            f"/admin/registration-links/{second_link.id}/comment",
            data={"comment": "invite for tester"},
            headers=auth_headers(admin),
            follow_redirects=False,
        )
        if comment_response.status_code != 303:
            raise PanelRegistrationLinksCheckFailure(f"comment update returned {comment_response.status_code}")

        revoke_response = client.post("/admin/registration-links/revoke-unused", headers=auth_headers(admin), follow_redirects=False)
        if revoke_response.status_code != 303:
            raise PanelRegistrationLinksCheckFailure(f"bulk revoke returned {revoke_response.status_code}")

        links_page = client.get("/admin/registration-links", headers=auth_headers(admin))
        if links_page.status_code != 200:
            raise PanelRegistrationLinksCheckFailure("registration-links page failed after revoke")
        if login not in links_page.text:
            raise PanelRegistrationLinksCheckFailure("recent used links section does not show created user")
        if "Отозванные ссылки" not in links_page.text:
            raise PanelRegistrationLinksCheckFailure("registration-links page misses revoked section")
        if "invite for tester" not in links_page.text:
            raise PanelRegistrationLinksCheckFailure("registration-links page does not show saved comment")

    assert_registered_user(login)
    assert_link_usage(token_id, login)
    assert_all_unused_revoked(second_token_id, token_id)
    print("OK: panel registration links check passed")


if __name__ == "__main__":
    try:
        run()
    except PanelRegistrationLinksCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
