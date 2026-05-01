from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "panel_server_release_checklist.md"

REQUIRED_DOC_TOKENS = [
    "Python 3.11+",
    "`pyproject.toml`",
    "PostgreSQL",
    "`systemd`",
    "reverse proxy",
    "TLS",
    "`SECRET_KEY`",
    "`DATABASE_URL`",
    "`PEER_AGENT_COMMAND`",
    "migrations are applied",
    "backup storage path",
]


class ReleaseChecklistFailure(RuntimeError):
    pass


def run() -> None:
    if not DOC_PATH.exists():
        raise ReleaseChecklistFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_DOC_TOKENS if token not in content]
    if missing:
        raise ReleaseChecklistFailure(
            "panel server release checklist misses required tokens: " + ", ".join(missing)
        )
    print("OK: panel server release checklist is documented")


if __name__ == "__main__":
    run()
