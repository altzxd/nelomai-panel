from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings


class BridgeCheckFailure(RuntimeError):
    pass


def _node_binary() -> str | None:
    explicit = os.environ.get("NELOMAI_NODE_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("node")


def _run_node_check(node_bin: str, target: Path) -> None:
    completed = subprocess.run(
        [node_bin, "--check", str(target)],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BridgeCheckFailure(
            f"Node syntax check failed for {target.name}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; node agent bridge check was not run")
        return

    agent_root = ROOT_DIR / "agents" / "node-tic-agent"
    agent_entry = agent_root / "src" / "index.js"
    required = [
        agent_root / "src" / "index.js",
        agent_root / "src" / "constants.js",
        agent_root / "src" / "registry.js",
        agent_root / "src" / "validation.js",
        agent_root / "src" / "response.js",
        agent_root / "src" / "state.js",
        agent_root / "src" / "render.js",
        agent_root / "src" / "zip.js",
    ]

    for target in required:
        if not target.exists():
            raise BridgeCheckFailure(f"Missing node agent file: {target}")
        _run_node_check(node_bin, target)

    temp_root = ROOT_DIR / ".tmp" / "node-agent-check"
    temp_root.mkdir(parents=True, exist_ok=True)
    state_file = temp_root / "state.json"
    log_file = temp_root / "payloads.jsonl"
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()

    previous_command = settings.peer_agent_command
    previous_component = os.environ.get("NELOMAI_AGENT_COMPONENT")
    previous_state_file = os.environ.get("NELOMAI_AGENT_STATE_FILE")
    previous_log_file = os.environ.get("NELOMAI_AGENT_LOG")
    previous_stub_mode = os.environ.get("NELOMAI_AGENT_STUB_MODE")
    previous_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_latest_version = os.environ.get("NELOMAI_AGENT_LATEST_VERSION")

    try:
        settings.peer_agent_command = f'"{node_bin}" "{agent_entry}"'
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"
        os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
        os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
        os.environ["NELOMAI_AGENT_STUB_MODE"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = "1"
        os.environ["NELOMAI_AGENT_LATEST_VERSION"] = "0.1.1"

        # Minimal direct bridge call. Full panel<->agent scenario remains covered
        # by the Python fake-agent contract check; this script verifies that the
        # Node skeleton is executable and responds through the same bridge.
        payload = (
            '{"contract_version":"1.0","supported_contracts":["1.0"],'
            '"panel_version":"0.1.1","component":"server-agent",'
            '"requested_capabilities":["agent.bootstrap.v1","agent.update.v1"],'
            '"action":"bootstrap_server",'
            '"server":{"id":1,"name":"bridge-check-server","server_type":"tic","host":"127.0.0.60","ssh_port":22,"ssh_login":"root","ssh_password":"secret"},'
            '"repository_url":"https://github.com/example/nelomai","os_family":"ubuntu","os_version":"22.04"}'
        )
        completed = subprocess.run(
            [node_bin, str(agent_entry)],
            cwd=ROOT_DIR,
            input=payload,
            text=True,
            capture_output=True,
        )
        if completed.returncode not in {0, 1}:
            raise BridgeCheckFailure(
                f"Node agent returned unexpected exit code {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        stdout = completed.stdout.strip()
        if not stdout:
            raise BridgeCheckFailure("Node agent returned empty stdout")
        if '"ok":true' not in stdout or '"status":"input_required"' not in stdout:
            raise BridgeCheckFailure(f"Unexpected node agent bootstrap response: {stdout}")

        print("OK: node agent bridge check passed")
    finally:
        settings.peer_agent_command = previous_command
        if previous_component is None:
            os.environ.pop("NELOMAI_AGENT_COMPONENT", None)
        else:
            os.environ["NELOMAI_AGENT_COMPONENT"] = previous_component
        if previous_state_file is None:
            os.environ.pop("NELOMAI_AGENT_STATE_FILE", None)
        else:
            os.environ["NELOMAI_AGENT_STATE_FILE"] = previous_state_file
        if previous_log_file is None:
            os.environ.pop("NELOMAI_AGENT_LOG", None)
        else:
            os.environ["NELOMAI_AGENT_LOG"] = previous_log_file
        if previous_stub_mode is None:
            os.environ.pop("NELOMAI_AGENT_STUB_MODE", None)
        else:
            os.environ["NELOMAI_AGENT_STUB_MODE"] = previous_stub_mode
        if previous_input_required is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = previous_input_required
        if previous_latest_version is None:
            os.environ.pop("NELOMAI_AGENT_LATEST_VERSION", None)
        else:
            os.environ["NELOMAI_AGENT_LATEST_VERSION"] = previous_latest_version


if __name__ == "__main__":
    try:
        main()
    except BridgeCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
