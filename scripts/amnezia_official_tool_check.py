from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class AmneziaToolCheckFailure(RuntimeError):
    pass


def node_bin() -> str:
    return str(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe")


def run_agent(payload: dict[str, object], *, state_file: Path, runtime_root: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["NELOMAI_AGENT_COMPONENT"] = "tak-agent"
    env["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
    env["NELOMAI_AGENT_RUNTIME_ROOT"] = str(runtime_root)
    env["NELOMAI_AGENT_EXEC_MODE"] = "filesystem"
    env["NELOMAI_AMNEZIAWG_TOOL_MODULE"] = str(ROOT_DIR / "scripts" / "fake_amnezia_tool_module.js")
    completed = subprocess.run(
        [node_bin(), str(ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        cwd=str(ROOT_DIR),
        check=False,
    )
    if completed.returncode != 0:
        raise AmneziaToolCheckFailure((completed.stderr or completed.stdout or "agent failed").strip())
    try:
        return json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise AmneziaToolCheckFailure(f"invalid json: {completed.stdout}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AmneziaToolCheckFailure(message)


def payload_base(action: str, component: str, capability: str) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.0",
        "component": component,
        "requested_capabilities": [capability],
        "action": action,
    }


def run() -> None:
    tmp_dir = ROOT_DIR / ".tmp" / "amnezia-tool-check"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_file = tmp_dir / "state.json"
    runtime_root = tmp_dir / "runtime"
    if state_file.exists():
        state_file.unlink()
    if runtime_root.exists():
        import shutil

        shutil.rmtree(runtime_root)

    tak_server = {"id": 201, "name": "tak-official", "server_type": "tak", "host": "194.87.197.51"}
    tic_server = {"id": 101, "name": "tic-official", "server_type": "tic", "host": "144.31.109.224"}
    provision = run_agent(
        {
            **payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
            "server": tak_server,
            "tic_server": tic_server,
        },
        state_file=state_file,
        runtime_root=runtime_root,
    )
    require(provision.get("ok") is True, "provision_tak_tunnel must succeed")
    config = provision.get("amnezia_config")
    require(isinstance(config, dict), "amnezia_config must exist")
    require(config.get("source") == "official-tooling", "official tool source marker is required")
    artifacts = config.get("canonical_artifacts") or {}
    require(artifacts.get("server_config_text") == "# official fake server config", "official server artifact must be used")
    require(artifacts.get("client_config_text") == "# official fake client config", "official client artifact must be used")
    print("OK: official Amnezia tool adapter check passed")


if __name__ == "__main__":
    try:
        run()
    except AmneziaToolCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
