from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
DOC_PATH = ROOT_DIR / "docs" / "panel_server_runbook.md"

REQUIRED_ENV_LINES = {
    "DEBUG=false",
    "SECRET_KEY=<generate-a-long-random-production-secret>",
    "DATABASE_URL=postgresql+psycopg://nelomai:change-me@db.example.local:5432/nelomai_panel",
    "PANEL_PUBLIC_BASE_URL=https://nelomai.ru",
    "PEER_AGENT_COMMAND=.venv/bin/python scripts/peer_agent_ssh_bridge.py",
}

REQUIRED_DOC_TOKENS = [
    ".env.example",
    "DEBUG=false",
    "SECRET_KEY",
    "DATABASE_URL",
    "PANEL_PUBLIC_BASE_URL",
    "PEER_AGENT_COMMAND",
    "scripts/install_panel_server.sh",
    "alembic upgrade head",
    "reverse proxy",
    "TLS",
    "systemd",
    "preflight_check.py",
    "Initial admin setup link:",
    "/bootstrap-admin/{token}",
]


class RunbookFailure(RuntimeError):
    pass


def run() -> None:
    if not ENV_EXAMPLE_PATH.exists():
        raise RunbookFailure(".env.example is missing")
    env_content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    missing_env = [line for line in REQUIRED_ENV_LINES if line not in env_content]
    if missing_env:
        raise RunbookFailure(".env.example misses required release lines: " + ", ".join(missing_env))

    if not DOC_PATH.exists():
        raise RunbookFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    doc_content = DOC_PATH.read_text(encoding="utf-8")
    missing_doc = [token for token in REQUIRED_DOC_TOKENS if token not in doc_content]
    if missing_doc:
        raise RunbookFailure("panel runbook misses required tokens: " + ", ".join(missing_doc))

    print("OK: panel release runbook check passed")


if __name__ == "__main__":
    try:
        run()
    except RunbookFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
