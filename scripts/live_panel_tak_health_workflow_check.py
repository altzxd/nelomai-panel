from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

STEPS = [
    ("tunnel", "scripts/live_tunnel_remote_check.py"),
    ("rotation", "scripts/live_panel_tak_rotation_check.py"),
    ("fallback", "scripts/live_panel_tak_fallback_check.py"),
    ("backoff", "scripts/live_panel_tak_backoff_check.py"),
    ("clear backoff", "scripts/live_panel_tak_clear_backoff_check.py"),
    ("partial repair", "scripts/live_panel_tak_partial_repair_check.py"),
    ("manual repair", "scripts/live_panel_tak_manual_repair_check.py"),
    ("tak switch", "scripts/live_panel_tak_switch_check.py"),
]


class LiveTakHealthWorkflowFailure(RuntimeError):
    pass


def run_step(name: str, relative_script: str) -> None:
    env = os.environ.copy()
    if relative_script == "scripts/live_panel_tak_switch_check.py":
        if env.get("NELOMAI_TAK_HOST", "").strip() and env.get("NELOMAI_TAK_HOST", "").strip() == env.get("NELOMAI_TAK2_HOST", "").strip():
            env["NELOMAI_ENABLE_TAK2_SAME_HOST_EMULATION"] = "1"
    else:
        env.pop("NELOMAI_ENABLE_TAK2_SAME_HOST_EMULATION", None)
    completed = subprocess.run(
        [sys.executable, relative_script],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in [(completed.stdout or "").strip(), (completed.stderr or "").strip()] if part)
        raise LiveTakHealthWorkflowFailure(f"{name} failed\n{detail}".strip())


def main() -> None:
    for name, script in STEPS:
        run_step(name, script)
    print("OK: live Tic/Tak health workflow check passed")


if __name__ == "__main__":
    try:
        main()
    except LiveTakHealthWorkflowFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
