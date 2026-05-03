from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DOC_PATH = ROOT_DIR / "docs" / "node_agent_live_validation.md"
REQUIRED_TOKENS = [
    "Ubuntu 22.04",
    "safe-init",
    "Tic ↔ Tak",
    "manual exit from `manual_attention_required`",
    "SSH transport reaches the host",
    "apt-get update",
    "WireGuard packages are installed",
    "Node.js is installed",
    "npm install --omit=dev",
    "systemd unit is written, enabled, restarted, and checked",
    "/admin/servers",
    "/admin/jobs",
    "bootstrap_snapshot",
    "completed status",
    "agent service is active under `systemd`",
    "failing command",
    "stderr/stdout",
    "live_panel_tak_rotation_check.py",
    "artifact rotation",
    "via_tak -> standalone -> via_tak",
    "failure_count",
    "cooldown",
    "manual_attention_required",
    "live_panel_tak_health_workflow_check.py",
    "live_panel_tak_switch_check.py",
    "Optional multi-`Tak` scenario",
]


class LiveValidationFailure(RuntimeError):
    pass


def run() -> None:
    if not DOC_PATH.exists():
        raise LiveValidationFailure(f"missing document: {DOC_PATH.relative_to(ROOT_DIR)}")
    content = DOC_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if token not in content]
    if missing:
        raise LiveValidationFailure(
            "node agent live validation doc misses required tokens: " + ", ".join(missing)
        )
    print("OK: node agent live validation check passed")


if __name__ == "__main__":
    try:
        run()
    except LiveValidationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
