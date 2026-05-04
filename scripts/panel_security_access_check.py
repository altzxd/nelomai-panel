from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.main import app
from app.models import Interface, Peer, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class AccessCheckFailure(RuntimeError):
    pass


def assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:500].replace("\n", " ")
        raise AccessCheckFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_fixture_ids() -> tuple[int, int, int | None, int | None]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        admin = db.execute(
            select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())
        ).scalars().first()
        user = db.execute(
            select(User).where(User.role == UserRole.USER).order_by(User.id.asc())
        ).scalars().first()
        peer = db.execute(
            select(Peer)
            .join(Interface)
            .where(Interface.user_id == user.id, Interface.is_pending_owner.is_(False))
            .order_by(Peer.id.asc())
        ).scalars().first() if user is not None else None
        foreign_user = db.execute(
            select(User)
            .where(User.role == UserRole.USER, User.id != user.id if user is not None else True)
            .order_by(User.id.asc())
        ).scalars().first()
        if admin is None or user is None:
            raise AccessCheckFailure("seed users for admin/user are missing")
        return admin.id, user.id, peer.id if peer else None, foreign_user.id if foreign_user else None


def load_user(user_id: int) -> User:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise AccessCheckFailure(f"user {user_id} disappeared during access check")
        db.expunge(user)
        return user


def run() -> None:
    admin_id, user_id, peer_id, foreign_user_id = load_fixture_ids()
    admin = load_user(admin_id)
    user = load_user(user_id)
    admin_headers = auth_headers(admin)
    user_headers = auth_headers(user)

    with TestClient(app) as client:
        dashboard_redirect = client.get("/dashboard", follow_redirects=False)
        assert_status(dashboard_redirect, 303, "unauthorized dashboard redirect")
        location = dashboard_redirect.headers.get("location", "")
        if location != "/":
            raise AccessCheckFailure(f"unauthorized dashboard redirect points to {location!r}, expected '/'")

        assert_status(client.get("/admin", headers=user_headers), 403, "user cannot open /admin")
        assert_status(client.get("/admin/servers", headers=user_headers), 403, "user cannot open /admin/servers")
        assert_status(client.get("/admin/jobs", headers=user_headers), 403, "user cannot open /admin/jobs")
        assert_status(client.get("/admin/diagnostics", headers=user_headers), 403, "user cannot open /admin/diagnostics")
        assert_status(client.get("/api/admin/agent-contract", headers=user_headers), 403, "user cannot open /api/admin/agent-contract")
        assert_status(client.post("/api/admin/jobs/cleanup", headers=user_headers), 403, "user cannot clean panel jobs")

        assert_status(client.get("/dashboard", headers=user_headers), 200, "user dashboard opens")
        assert_status(client.get("/admin", headers=admin_headers), 200, "admin dashboard opens")
        assert_status(
            client.get(f"/dashboard?target_user_id={user_id}&preview=1", headers=admin_headers),
            200,
            "admin preview dashboard opens",
        )

        if foreign_user_id is not None:
            assert_status(
                client.get(f"/dashboard?target_user_id={foreign_user_id}", headers=user_headers),
                403,
                "user cannot open another user dashboard",
            )

        if peer_id is not None:
            assert_status(
                client.put(
                    f"/api/peers/{peer_id}/comment?preview=1",
                    json={"comment": "preview forbidden"},
                    headers=admin_headers,
                ),
                403,
                "preview mode blocks peer comment write",
            )

        invalid_download = client.get("/downloads/peer/not-a-real-token", follow_redirects=False)
        assert_status(invalid_download, 404, "invalid public download token")
        if "location" in invalid_download.headers:
            raise AccessCheckFailure("invalid public download token unexpectedly redirected")

    print("OK: panel security/access check passed")


if __name__ == "__main__":
    try:
        run()
    except AccessCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
