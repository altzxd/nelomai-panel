from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


class SafeInitCheckFailure(RuntimeError):
    pass


def _node_binary() -> str | None:
    explicit = os.environ.get("NELOMAI_NODE_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("node")


def _assert_index(commands: list[str], expected: str) -> int:
    try:
        return commands.index(expected)
    except ValueError as exc:
        raise SafeInitCheckFailure(f"Missing safe-init command: {expected}") from exc


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; safe-init bootstrap check was not run")
        return

    agent_entry = ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js"
    if not agent_entry.exists():
        raise SafeInitCheckFailure(f"Missing node agent entry: {agent_entry}")

    previous_profile = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE")
    previous_mode = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_MODE")
    previous_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_state_file = os.environ.get("NELOMAI_AGENT_STATE_FILE")
    previous_log = os.environ.get("NELOMAI_AGENT_LOG")
    previous_component = os.environ.get("NELOMAI_AGENT_COMPONENT")

    state_file = ROOT_DIR / ".tmp" / "safe-init-check-state.json"
    log_file = ROOT_DIR / ".tmp" / "safe-init-check-log.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()

    payload = {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.0",
        "component": "server-agent",
        "requested_capabilities": ["agent.bootstrap.v1", "agent.update.v1"],
        "action": "bootstrap_server",
        "server": {
            "id": 1,
            "name": "safe-init-check",
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
        os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = "safe-init"
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
            raise SafeInitCheckFailure((completed.stderr or completed.stdout).strip() or f"Unexpected exit code: {completed.returncode}")

        try:
            response = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise SafeInitCheckFailure(f"Node agent returned invalid JSON: {completed.stdout!r}") from exc

        if not response.get("ok"):
            raise SafeInitCheckFailure(f"Node agent returned error: {response.get('error')!r}")

        bootstrap_plan = response.get("bootstrap_plan")
        if not isinstance(bootstrap_plan, dict):
            raise SafeInitCheckFailure("bootstrap_plan is missing")
        if bootstrap_plan.get("command_profile") != "safe-init":
            raise SafeInitCheckFailure(f"Unexpected command_profile: {bootstrap_plan.get('command_profile')!r}")

        commands = bootstrap_plan.get("commands")
        if not isinstance(commands, list) or not commands:
            raise SafeInitCheckFailure("bootstrap_plan.commands is empty")

        checks = [
            "apt-get update",
            "apt-get install -y ca-certificates curl git iproute2 iptables jq nftables tar unzip wireguard wireguard-tools zip",
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "systemctl daemon-reload",
        ]
        indexes = {command: _assert_index(commands, command) for command in checks}

        if indexes[checks[0]] >= indexes[checks[1]]:
            raise SafeInitCheckFailure("safe-init order is invalid: apt-get update must run before package install")
        if indexes[checks[1]] >= indexes[checks[2]]:
            raise SafeInitCheckFailure("safe-init order is invalid: package install must run before NodeSource bootstrap")
        if indexes[checks[2]] >= indexes[checks[3]]:
            raise SafeInitCheckFailure("safe-init order is invalid: NodeSource bootstrap must run before nodejs install")
        if indexes[checks[3]] >= indexes[checks[4]]:
            raise SafeInitCheckFailure("safe-init order is invalid: nodejs install must run before daemon-reload")

        forbidden_markers = [
            "apt-get install -y bash ca-certificates curl git jq tar unzip zip iproute2 iptables nftables wireguard wireguard-tools",
        ]
        for marker in forbidden_markers:
            if marker in commands:
                raise SafeInitCheckFailure(f"Unexpected duplicate package line: {marker}")

        if not any("git clone" in command for command in commands):
            raise SafeInitCheckFailure("safe-init must include git clone/pull step")
        if not any("npm install --omit=dev" in command for command in commands):
            raise SafeInitCheckFailure("safe-init must include npm install step")
        if not any("systemctl enable" in command for command in commands):
            raise SafeInitCheckFailure("safe-init must include systemctl enable step")
        if not any("systemctl restart" in command for command in commands):
            raise SafeInitCheckFailure("safe-init must include systemctl restart step")
        if not any("systemctl --no-pager --full status" in command for command in commands):
            raise SafeInitCheckFailure("safe-init must include service status step")

        print("OK: node safe-init bootstrap check passed")
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


if __name__ == "__main__":
    try:
        main()
    except SafeInitCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
