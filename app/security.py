from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def create_peer_download_token(peer_id: int, token_id: str, expires_at: datetime | None = None) -> str:
    payload = {
        "scope": "peer_download",
        "peer_id": peer_id,
        "jti": token_id,
    }
    if expires_at is not None:
        payload["exp"] = expires_at
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_peer_download_token(token: str) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if payload.get("scope") != "peer_download":
        raise jwt.InvalidTokenError("Invalid peer download scope")
    return payload


def create_auth_download_token(*, scope: str, resource_id: int, owner_user_id: int) -> str:
    payload = {
        "scope": scope,
        "rid": resource_id,
        "owner_user_id": owner_user_id,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_auth_download_token(token: str) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    scope = str(payload.get("scope") or "")
    if scope not in {"peer_auth_download", "interface_auth_download"}:
        raise jwt.InvalidTokenError("Invalid authenticated download scope")
    return payload
