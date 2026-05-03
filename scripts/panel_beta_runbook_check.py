from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "panel_beta_runbook.md"

REQUIRED_DOC_TOKENS = [
    "small group of real users",
    "one real `Tic` server",
    "one real `Tak` server",
    "PostgreSQL",
    "reverse proxy",
    "TLS",
    "`systemd`",
    "production `SECRET_KEY`",
    "production `DATABASE_URL`",
    "production `PANEL_PUBLIC_BASE_URL`",
    "production `PEER_AGENT_COMMAND`",
    "`preflight_check.py` passes without failures",
    "first clean panel startup prints the one-time first-admin link",
    "first admin can be created through `/bootstrap-admin/{token}`",
    "live `Tic ↔ Tak` health workflow passes",
    "panel backups can be created and listed",
    "server backups can be created and verified",
    "`route_mode=via_tak`",
    "`via_tak -> standalone -> via_tak`",
    "`Ротировать артефакты`",
    "`Снять backoff`",
    "`Восстановить пару`",
    "rollback",
    "feedback",
]


class BetaRunbookFailure(RuntimeError):
    pass


def run() -> None:
    if not DOC_PATH.exists():
        raise BetaRunbookFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_DOC_TOKENS if token not in content]
    if missing:
        raise BetaRunbookFailure("panel beta runbook misses required tokens: " + ", ".join(missing))
    print("OK: panel beta runbook check passed")


if __name__ == "__main__":
    try:
        run()
    except BetaRunbookFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
