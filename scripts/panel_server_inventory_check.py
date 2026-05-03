from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "panel_server_inventory.md"
PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"

REQUIRED_DOC_TOKENS = [
    "Python 3.11+",
    "project dependencies installed from `pyproject.toml`",
    "PostgreSQL",
    "OpenSSH client",
    "sshpass",
    "Git",
    "systemd",
    "reverse proxy",
    "TLS",
    "SECRET_KEY",
    "DATABASE_URL",
    "PEER_AGENT_COMMAND",
    "backup storage path",
]

REQUIRED_DEPENDENCIES = {
    "alembic",
    "fastapi",
    "httpx",
    "jinja2",
    "passlib",
    "psycopg[binary]",
    "pydantic-settings",
    "pyjwt",
    "python-multipart",
    "sqlalchemy",
    "uvicorn[standard]",
}


class InventoryFailure(RuntimeError):
    pass


def normalize_requirement(raw: str) -> str:
    return raw.split(">=", 1)[0].split("<", 1)[0].strip()


def check_doc() -> None:
    if not DOC_PATH.exists():
        raise InventoryFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_DOC_TOKENS if token not in content]
    if missing:
        raise InventoryFailure(
            "panel server inventory doc misses required tokens: " + ", ".join(missing)
        )


def check_pyproject() -> None:
    if not PYPROJECT_PATH.exists():
        raise InventoryFailure("pyproject.toml is missing")
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    raw_dependencies = data.get("project", {}).get("dependencies", [])
    installed = {normalize_requirement(item) for item in raw_dependencies}
    missing = sorted(REQUIRED_DEPENDENCIES - installed)
    if missing:
        raise InventoryFailure(
            "pyproject.toml misses required panel runtime dependencies: " + ", ".join(missing)
        )


def run() -> None:
    check_doc()
    check_pyproject()
    print("OK: panel server inventory baseline is documented and consistent")


if __name__ == "__main__":
    run()
