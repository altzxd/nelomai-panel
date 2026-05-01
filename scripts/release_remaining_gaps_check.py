from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "release_remaining_gaps.md"
REQUIRED_TOKENS = [
    ".gitignore",
    "no local `.env` file",
    "SECRET_KEY",
    "SQLite",
    "PostgreSQL",
    "PEER_AGENT_COMMAND",
    "DEBUG=false",
    "reverse proxy",
    "TLS",
    "systemd",
    "blank Ubuntu 22.04 host",
    "safe-init",
    "full",
    "environment tasks, not code defects",
]


class RemainingGapsFailure(RuntimeError):
    pass


def run() -> None:
    if not DOC_PATH.exists():
        raise RemainingGapsFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if token not in content]
    if missing:
        raise RemainingGapsFailure(
            "remaining release gaps doc misses required tokens: " + ", ".join(missing)
        )
    print("OK: remaining release gaps check passed")


if __name__ == "__main__":
    try:
        run()
    except RemainingGapsFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
