from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "release_gates.md"
REQUIRED_TOKENS = [
    "release hygiene check passes",
    "clean-start check passes",
    "migration check passes",
    "production config rules are enforced",
    "security/access check passes",
    "safe-init",
    "full",
    "panel E2E bootstrap check passes",
    "SSH prompt and SSH exec checks pass",
    "live `Tic ↔ Tak` tunnel validation passes",
    "live panel fallback validation passes",
    "live panel backoff/manual-attention validation passes",
    "live panel partial-repair validation passes",
    "live panel manual-repair validation passes",
    "optional live panel multi-`Tak` switch validation is documented and skip-safe",
    "combined live `Tic ↔ Tak` health workflow is documented",
    "panel server inventory baseline is documented",
    "panel server release checklist is documented",
    "DEBUG=true",
    "placeholder `SECRET_KEY`",
    "SQLite `DATABASE_URL`",
    "empty `PEER_AGENT_COMMAND`",
    "`preflight_check.py` passes without failures",
]


class ReleaseSummaryFailure(RuntimeError):
    pass


def run() -> None:
    if not DOC_PATH.exists():
        raise ReleaseSummaryFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if token not in content]
    if missing:
        raise ReleaseSummaryFailure(
            "release summary misses required tokens: " + ", ".join(missing)
        )
    print("OK: release summary check passed")


if __name__ == "__main__":
    try:
        run()
    except ReleaseSummaryFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
