from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class TunnelCheckFailure(RuntimeError):
    pass


def codex_node() -> str:
    return str(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe")


def run_agent(payload: dict[str, object], *, component_env: str, state_file: Path, runtime_root: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["NELOMAI_AGENT_COMPONENT"] = component_env
    env["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
    env["NELOMAI_AGENT_RUNTIME_ROOT"] = str(runtime_root)
    env["NELOMAI_AGENT_EXEC_MODE"] = "filesystem"
    completed = subprocess.run(
        [codex_node(), str(ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        cwd=str(ROOT_DIR),
        check=False,
    )
    if not completed.stdout.strip():
        raise TunnelCheckFailure(f"Empty stdout for action {payload.get('action')}")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TunnelCheckFailure(f"Invalid JSON for action {payload.get('action')}: {completed.stdout}") from exc
    return parsed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TunnelCheckFailure(message)


def payload_base(action: str, component: str, capability: str) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.1",
        "component": component,
        "requested_capabilities": [capability],
        "action": action,
    }


def run() -> None:
    tmp_dir = ROOT_DIR / ".tmp" / "tunnel-lifecycle-check"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_file = tmp_dir / "state.json"
    runtime_root = tmp_dir / "runtime"
    if state_file.exists():
        state_file.unlink()
    if runtime_root.exists():
        import shutil

        shutil.rmtree(runtime_root)

    tak_server = {"id": 201, "name": "tak-live", "server_type": "tak", "host": "194.87.197.51"}
    tic_server = {"id": 101, "name": "tic-live", "server_type": "tic", "host": "144.31.109.224"}

    provision_payload = {
        **payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
        "server": tak_server,
        "tic_server": tic_server,
    }
    provision = run_agent(provision_payload, component_env="tak-agent", state_file=state_file, runtime_root=runtime_root)
    require(provision.get("ok") is True, "provision_tak_tunnel must succeed")
    tunnel_id = str(provision.get("tunnel_id") or "")
    require(bool(tunnel_id), "provision_tak_tunnel must return tunnel_id")
    tunnel_artifacts = provision.get("tunnel_artifacts")
    amnezia_config = provision.get("amnezia_config")
    require(isinstance(tunnel_artifacts, dict), "provision_tak_tunnel must return tunnel_artifacts")
    require(isinstance(tunnel_artifacts.get("endpoint"), dict), "tunnel_artifacts must include endpoint")
    require(isinstance(tunnel_artifacts.get("addressing"), dict), "tunnel_artifacts must include addressing")
    require(isinstance(tunnel_artifacts.get("keys"), dict), "tunnel_artifacts must include keys")
    require(isinstance(tunnel_artifacts.get("awg_parameters"), dict), "tunnel_artifacts must include awg_parameters")
    require(isinstance(tunnel_artifacts.get("runtime_artifacts"), dict), "tunnel_artifacts must include runtime_artifacts")
    require(isinstance(amnezia_config, dict), "provision_tak_tunnel must return amnezia_config")
    require(isinstance(amnezia_config.get("endpoint"), dict), "amnezia_config must include endpoint")
    require(isinstance(amnezia_config.get("addressing"), dict), "amnezia_config must include addressing")
    require(isinstance(amnezia_config.get("keys"), dict), "amnezia_config must include keys")
    require(isinstance(amnezia_config.get("awg_parameters"), dict), "amnezia_config must include awg_parameters")

    verify_tak_payload = {
        **payload_base("verify_tak_tunnel_status", "tak-agent", "tunnel.tak.status.v1"),
        "server": tak_server,
        "tic_server": tic_server,
        "tunnel_id": tunnel_id,
    }
    verify_tak = run_agent(verify_tak_payload, component_env="tak-agent", state_file=state_file, runtime_root=runtime_root)
    require(verify_tak.get("ok") is True, "verify_tak_tunnel_status on Tak must succeed")
    tak_status = verify_tak.get("tunnel_status") or {}
    require(tak_status.get("exists") is True, "Tak tunnel must exist after provision")

    attach_payload = {
        **payload_base("attach_tak_tunnel", "tic-agent", "tunnel.tak.attach.v1"),
        "server": tic_server,
        "tak_server": tak_server,
        "tunnel_id": tunnel_id,
        "tunnel_artifacts": tunnel_artifacts,
    }
    attach = run_agent(attach_payload, component_env="tic-agent", state_file=state_file, runtime_root=runtime_root)
    require(attach.get("ok") is True, "attach_tak_tunnel must succeed")

    verify_tic_payload = {
        **payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
        "server": tic_server,
        "tak_server": tak_server,
        "tunnel_id": tunnel_id,
    }
    verify_tic = run_agent(verify_tic_payload, component_env="tic-agent", state_file=state_file, runtime_root=runtime_root)
    require(verify_tic.get("ok") is True, "verify_tak_tunnel_status on Tic must succeed")
    tic_status = verify_tic.get("tunnel_status") or {}
    require(tic_status.get("exists") is True, "Tic tunnel must exist after attach")
    artifacts = tic_status.get("runtime_artifacts") or {}
    require(artifacts.get("client_config_exists") is True, "Tic tunnel must create client config artifact")

    detach_payload = {
        **payload_base("detach_tak_tunnel", "tic-agent", "tunnel.tak.detach.v1"),
        "server": tic_server,
        "tak_server": tak_server,
        "tunnel_id": tunnel_id,
    }
    detach = run_agent(detach_payload, component_env="tic-agent", state_file=state_file, runtime_root=runtime_root)
    require(detach.get("ok") is True, "detach_tak_tunnel must succeed")

    verify_after_detach = run_agent(verify_tic_payload, component_env="tic-agent", state_file=state_file, runtime_root=runtime_root)
    require(verify_after_detach.get("ok") is True, "verify after detach must succeed")
    final_status = verify_after_detach.get("tunnel_status") or {}
    require(final_status.get("status") == "detached", "Tunnel must report detached status after detach")
    require(final_status.get("is_active") is False, "Tunnel must not stay active after detach")
    final_artifacts = final_status.get("runtime_artifacts") or {}
    require(final_artifacts.get("system_interface_exists") is False, "Detached tunnel must not keep a live system interface")
    require(final_artifacts.get("system_config_exists") is False, "Detached tunnel must not keep a live system config")

    print("OK: tunnel lifecycle check passed")


if __name__ == "__main__":
    try:
        run()
    except TunnelCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
