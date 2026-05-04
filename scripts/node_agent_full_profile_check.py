from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


class FullProfileCheckFailure(RuntimeError):
    pass


def _node_binary() -> str | None:
    explicit = os.environ.get("NELOMAI_NODE_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("node")


def _bootstrap_plan(node_bin: str, profile: str) -> dict[str, object]:
    agent_entry = ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js"
    if not agent_entry.exists():
        raise FullProfileCheckFailure(f"Missing node agent entry: {agent_entry}")

    previous_profile = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE")
    previous_mode = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_MODE")
    previous_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_state_file = os.environ.get("NELOMAI_AGENT_STATE_FILE")
    previous_log = os.environ.get("NELOMAI_AGENT_LOG")
    previous_component = os.environ.get("NELOMAI_AGENT_COMPONENT")

    state_file = ROOT_DIR / ".tmp" / f"{profile}-profile-check-state.json"
    log_file = ROOT_DIR / ".tmp" / f"{profile}-profile-check-log.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()

    payload = {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.1",
        "component": "server-agent",
        "requested_capabilities": ["agent.bootstrap.v1", "agent.update.v1"],
        "action": "bootstrap_server",
        "server": {
            "id": 1,
            "name": f"{profile}-profile-check",
            "server_type": "tic",
            "host": "127.0.0.1",
            "ssh_port": 22,
            "ssh_login": "root",
            "ssh_password": "secret",
        },
        "repository_url": "https://github.com/example/nelomai.git",
        "os_family": "ubuntu",
        "os_version": "22.04",
    }

    try:
        os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = profile
        os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = "dry-run"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = ""
        os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
        os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"

        completed = subprocess.run(
            [node_bin, str(agent_entry)],
            cwd=ROOT_DIR,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode not in {0, 1}:
            raise FullProfileCheckFailure((completed.stderr or completed.stdout).strip() or f"Unexpected exit code: {completed.returncode}")
        try:
            response = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise FullProfileCheckFailure(f"Node agent returned invalid JSON for profile {profile}: {completed.stdout!r}") from exc
        if not response.get("ok"):
            raise FullProfileCheckFailure(f"Node agent returned error for profile {profile}: {response.get('error')!r}")
        bootstrap_plan = response.get("bootstrap_plan")
        if not isinstance(bootstrap_plan, dict):
            raise FullProfileCheckFailure(f"bootstrap_plan is missing for profile {profile}")
        return bootstrap_plan
    finally:
        if previous_profile is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = previous_profile
        if previous_mode is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_MODE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = previous_mode
        if previous_input_required is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = previous_input_required
        if previous_state_file is None:
            os.environ.pop("NELOMAI_AGENT_STATE_FILE", None)
        else:
            os.environ["NELOMAI_AGENT_STATE_FILE"] = previous_state_file
        if previous_log is None:
            os.environ.pop("NELOMAI_AGENT_LOG", None)
        else:
            os.environ["NELOMAI_AGENT_LOG"] = previous_log
        if previous_component is None:
            os.environ.pop("NELOMAI_AGENT_COMPONENT", None)
        else:
            os.environ["NELOMAI_AGENT_COMPONENT"] = previous_component


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; full-profile bootstrap check was not run")
        return

    safe_plan = _bootstrap_plan(node_bin, "safe-init")
    full_plan = _bootstrap_plan(node_bin, "full")

    safe_commands = safe_plan.get("commands")
    full_commands = full_plan.get("commands")
    if not isinstance(safe_commands, list) or not isinstance(full_commands, list):
        raise FullProfileCheckFailure("commands missing in bootstrap plans")

    if full_plan.get("command_profile") != "full":
        raise FullProfileCheckFailure(f"Unexpected full command_profile: {full_plan.get('command_profile')!r}")
    if len(full_commands) < len(safe_commands):
        raise FullProfileCheckFailure("full profile must not be shorter than safe-init")

    safe_prefix = full_commands[: len(safe_commands)]
    if safe_prefix != safe_commands:
        raise FullProfileCheckFailure("full profile must begin with the exact safe-init command sequence")

    extra_commands = full_commands[len(safe_commands):]
    expected_delta: list[str] = []
    if extra_commands != expected_delta:
        raise FullProfileCheckFailure(f"Unexpected full-profile delta: {extra_commands!r}")

    packages = full_plan.get("packages")
    if not isinstance(packages, list) or "bash" not in packages or "python3" not in packages or "build-essential" not in packages or "ufw" not in packages:
        raise FullProfileCheckFailure("full profile must expose complete package baseline including bash, build-essential, python3, and ufw")

    print("OK: node full-profile bootstrap check passed")


if __name__ == "__main__":
    try:
        main()
    except FullProfileCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
