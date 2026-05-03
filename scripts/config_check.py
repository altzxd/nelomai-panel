from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings


REQUIRED_ENV_EXAMPLE_KEYS = {
    "APP_NAME",
    "DEBUG",
    "SECRET_KEY",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "DATABASE_URL",
    "NELOMAI_GIT_REPO",
    "PEER_AGENT_COMMAND",
    "PEER_AGENT_TIMEOUT_SECONDS",
}
PLACEHOLDER_TOKENS = {
    "",
    "change-me",
    "changeme",
    "change-me-with-a-long-random-value",
    "dev-only-change-me-with-a-long-random-value",
    "postgres",
    "localhost",
}
DEFAULT_SECRET_KEYS = {
    "change-me-with-a-long-random-value",
    "dev-only-change-me-with-a-long-random-value",
}
SENSITIVE_KEY_RE = re.compile(r"(secret|password|private|api_?key|token$)", re.IGNORECASE)
NON_SECRET_KEYS = {"ACCESS_TOKEN_EXPIRE_MINUTES"}


class ConfigFailure(RuntimeError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_placeholder_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in PLACEHOLDER_TOKENS:
        return True
    return any(token in normalized for token in ("change-me", "example", "placeholder", "your-", "<"))


def add_issue(issues: list[str], message: str) -> None:
    issues.append(message)


def add_warning(warnings: list[str], message: str) -> None:
    warnings.append(message)


def check_env_example(issues: list[str], warnings: list[str]) -> None:
    env_example = ROOT_DIR / ".env.example"
    if not env_example.exists():
        add_issue(issues, ".env.example is missing")
        return

    values = parse_env_file(env_example)
    missing = sorted(REQUIRED_ENV_EXAMPLE_KEYS - set(values))
    for key in missing:
        add_issue(issues, f".env.example misses required key {key}")

    for key, value in values.items():
        if key in NON_SECRET_KEYS:
            continue
        if not SENSITIVE_KEY_RE.search(key):
            continue
        if value and not is_placeholder_value(value):
            add_issue(issues, f".env.example contains non-placeholder sensitive value for {key}")

    database_url = values.get("DATABASE_URL", "")
    parsed = urlparse(database_url)
    if parsed.password and not is_placeholder_value(parsed.password):
        add_issue(issues, ".env.example DATABASE_URL contains a non-placeholder password")
    if database_url.startswith("sqlite"):
        add_warning(warnings, ".env.example uses SQLite; production should use PostgreSQL")


def check_runtime_settings(issues: list[str], warnings: list[str], production: bool) -> None:
    if settings.access_token_expire_minutes <= 0:
        add_issue(issues, "ACCESS_TOKEN_EXPIRE_MINUTES must be greater than zero")
    if settings.peer_agent_timeout_seconds <= 0:
        add_issue(issues, "PEER_AGENT_TIMEOUT_SECONDS must be greater than zero")

    if production:
        if settings.debug:
            add_issue(issues, "DEBUG must be false in production")
        if settings.secret_key in DEFAULT_SECRET_KEYS or is_placeholder_value(settings.secret_key):
            add_issue(issues, "SECRET_KEY must be replaced in production")
        if len(settings.secret_key) < 32:
            add_issue(issues, "SECRET_KEY must be at least 32 characters in production")
        if settings.database_url.startswith("sqlite"):
            add_issue(issues, "DATABASE_URL must not use SQLite in production")
        if not settings.database_url.startswith(("postgresql+psycopg://", "postgresql://")):
            add_issue(issues, "DATABASE_URL should use PostgreSQL in production")
        if not settings.nelomai_git_repo.strip():
            add_issue(issues, "NELOMAI_GIT_REPO must be configured in production")
        if not settings.peer_agent_command:
            add_issue(issues, "PEER_AGENT_COMMAND must be configured in production")
    else:
        if settings.debug:
            add_warning(warnings, "DEBUG is true; OK for local dev, not for production")
        if settings.secret_key in DEFAULT_SECRET_KEYS or is_placeholder_value(settings.secret_key):
            add_warning(warnings, "SECRET_KEY is a development placeholder")
        if settings.database_url.startswith("sqlite"):
            add_warning(warnings, "DATABASE_URL uses SQLite; OK for local dev, not for server install")
        if not settings.nelomai_git_repo.strip():
            add_warning(warnings, "NELOMAI_GIT_REPO is empty; deploy/update paths will require manual repo configuration")
        if not settings.peer_agent_command:
            add_warning(warnings, "PEER_AGENT_COMMAND is empty; agent-backed actions will return 503")


def check_local_env_file(warnings: list[str]) -> None:
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        add_warning(warnings, ".env exists locally; keep it out of Git and never commit real secrets")


def run() -> None:
    profile = (
        os.environ.get("NELOMAI_CONFIG_PROFILE")
        or os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or "development"
    ).strip().lower()
    production = profile in {"prod", "production", "server"}

    issues: list[str] = []
    warnings: list[str] = []
    check_env_example(issues, warnings)
    check_runtime_settings(issues, warnings, production=production)
    check_local_env_file(warnings)

    for warning in warnings:
        print(f"WARN: {warning}")

    if issues:
        print("FAIL: config check found issues", file=sys.stderr)
        for index, issue in enumerate(issues, start=1):
            print(f"{index}. {issue}", file=sys.stderr)
        raise SystemExit(1)

    mode = "production" if production else "development"
    print(f"OK: config check passed ({mode} profile)")


if __name__ == "__main__":
    run()
