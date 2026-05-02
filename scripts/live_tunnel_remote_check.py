from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PLINK_BIN = Path(r"C:\Program Files\PuTTY\plink.exe")


class LiveTunnelCheckFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LiveTunnelCheckFailure(f"Missing required env {name}")
    return value


def _remote_run(*, host: str, port: int, password: str, host_key: str, remote_command: str) -> subprocess.CompletedProcess[str]:
    if not PLINK_BIN.exists():
        raise LiveTunnelCheckFailure(f"plink not found: {PLINK_BIN}")
    return subprocess.run(
        [
            str(PLINK_BIN),
            "-batch",
            "-ssh",
            f"root@{host}",
            "-P",
            str(port),
            "-pw",
            password,
            "-hostkey",
            host_key,
            remote_command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _shell_quote(value: str) -> str:
    return shlex.quote(str(value))


def _assert_remote_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or f"exit={result.returncode}"
    raise LiveTunnelCheckFailure(f"{label} failed: {detail}")


def _agent_call(
    *,
    host: str,
    port: int,
    password: str,
    host_key: str,
    component: str,
    exec_mode: str,
    payload: dict[str, object],
    extra_env: dict[str, str] | None = None,
) -> dict[str, object]:
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    exports = [
        f"NELOMAI_AGENT_COMPONENT={_shell_quote(component)}",
        f"NELOMAI_AGENT_EXEC_MODE={_shell_quote(exec_mode)}",
    ]
    for key, value in (extra_env or {}).items():
        exports.append(f"{key}={_shell_quote(value)}")
    env_file = f"/etc/default/nelomai-{component.replace('-agent', '')}-agent"
    remote_command = (
        "bash -lc "
        f"\"set -a && test -f {_shell_quote(env_file)} && . {_shell_quote(env_file)}; set +a; "
        f"tmp=\\$(mktemp /tmp/nelomai-tunnel-XXXXXX.json) && "
        f"printf %s '{payload_b64}' | base64 -d > \\\"\\$tmp\\\" && "
        f"{' '.join(exports)} "
        f"/usr/bin/node /opt/nelomai/current/agents/node-tic-agent/src/index.js < \\\"\\$tmp\\\"; "
        f"status=\\$?; rm -f \\\"\\$tmp\\\"; exit \\$status\""
    )
    result = _remote_run(
        host=host,
        port=port,
        password=password,
        host_key=host_key,
        remote_command=remote_command,
    )
    _assert_remote_ok(result, f"{component}:{payload.get('action')}")
    stdout = (result.stdout or "").strip()
    if not stdout:
        raise LiveTunnelCheckFailure(f"{component}:{payload.get('action')} returned empty stdout")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LiveTunnelCheckFailure(f"{component}:{payload.get('action')} returned invalid JSON: {stdout}") from exc


def _payload_base(action: str, component: str, capability: str) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.0",
        "component": component,
        "requested_capabilities": [capability],
        "action": action,
    }


def main() -> None:
    tic_host = _required_env("NELOMAI_TIC_HOST")
    tic_password = _required_env("NELOMAI_TIC_SSH_PASSWORD")
    tic_host_key = _required_env("NELOMAI_TIC_SSH_HOST_KEY")
    tak_host = _required_env("NELOMAI_TAK_HOST")
    tak_password = _required_env("NELOMAI_TAK_SSH_PASSWORD")
    tak_host_key = _required_env("NELOMAI_TAK_SSH_HOST_KEY")
    tak_amnezia_tool_cmd = os.environ.get("NELOMAI_TAK_AMNEZIA_TOOL_CMD", "").strip()
    tic_userspace_impl = os.environ.get("NELOMAI_TIC_WG_QUICK_USERSPACE_IMPLEMENTATION", "").strip()
    tic_port = int(os.environ.get("NELOMAI_TIC_SSH_PORT", "22"))
    tak_port = int(os.environ.get("NELOMAI_TAK_SSH_PORT", "22"))

    suffix = uuid.uuid4().hex[:8]
    numeric_suffix = int(suffix[:6], 16)
    tic_server = {"id": 100000 + numeric_suffix, "name": f"live-tic-{suffix}", "server_type": "tic", "host": tic_host}
    tak_server = {"id": 200000 + numeric_suffix, "name": f"live-tak-{suffix}", "server_type": "tak", "host": tak_host}

    provision = _agent_call(
        host=tak_host,
        port=tak_port,
        password=tak_password,
        host_key=tak_host_key,
        component="tak-agent",
        exec_mode="filesystem",
        extra_env={"NELOMAI_AMNEZIAWG_TOOL_CMD": tak_amnezia_tool_cmd} if tak_amnezia_tool_cmd else None,
        payload={
            **_payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
            "server": tak_server,
            "tic_server": tic_server,
        },
    )
    if provision.get("ok") is not True:
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel returned error: {provision}")
    tunnel_id = str(provision.get("tunnel_id") or "").strip()
    tunnel_artifacts = provision.get("tunnel_artifacts")
    amnezia_config = provision.get("amnezia_config")
    if not tunnel_id or not isinstance(tunnel_artifacts, dict) or not isinstance(amnezia_config, dict):
        raise LiveTunnelCheckFailure("provision_tak_tunnel did not return tunnel_id + tunnel_artifacts + amnezia_config")
    if not isinstance(tunnel_artifacts.get("endpoint"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured tunnel_artifacts.endpoint: {provision}")
    if not isinstance(tunnel_artifacts.get("addressing"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured tunnel_artifacts.addressing: {provision}")
    if not isinstance(tunnel_artifacts.get("keys"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured tunnel_artifacts.keys: {provision}")
    if not isinstance(tunnel_artifacts.get("awg_parameters"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured tunnel_artifacts.awg_parameters: {provision}")
    if not isinstance(tunnel_artifacts.get("runtime_artifacts"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured tunnel_artifacts.runtime_artifacts: {provision}")
    if not isinstance(amnezia_config.get("endpoint"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured endpoint: {provision}")
    if not isinstance(amnezia_config.get("addressing"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured addressing: {provision}")
    if not isinstance(amnezia_config.get("keys"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured keys: {provision}")
    if not isinstance(amnezia_config.get("awg_parameters"), dict):
        raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not return structured awg_parameters: {provision}")
    if tak_amnezia_tool_cmd and "fake_amnezia_tool.py" in tak_amnezia_tool_cmd:
        if amnezia_config.get("source") != "official-tooling":
            raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not use official tooling source: {provision}")
        runtime_artifacts = tunnel_artifacts.get("runtime_artifacts") or {}
        if runtime_artifacts.get("server_config_text") != "# official fake server config":
            raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not use tunnel_artifacts.server_config_text from tool: {provision}")
        if runtime_artifacts.get("client_config_text") != "# official fake client config":
            raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not use tunnel_artifacts.client_config_text from tool: {provision}")
        canonical_artifacts = amnezia_config.get("canonical_artifacts") or {}
        if canonical_artifacts.get("server_config_text") != "# official fake server config":
            raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not use canonical server artifact from tool: {provision}")
        if canonical_artifacts.get("client_config_text") != "# official fake client config":
            raise LiveTunnelCheckFailure(f"provision_tak_tunnel did not use canonical client artifact from tool: {provision}")

    verify_tak = _agent_call(
        host=tak_host,
        port=tak_port,
        password=tak_password,
        host_key=tak_host_key,
        component="tak-agent",
        exec_mode="filesystem",
        payload={
            **_payload_base("verify_tak_tunnel_status", "tak-agent", "tunnel.tak.status.v1"),
            "server": tak_server,
            "tic_server": tic_server,
            "tunnel_id": tunnel_id,
        },
    )
    tak_status = verify_tak.get("tunnel_status") or {}
    if verify_tak.get("ok") is not True or tak_status.get("exists") is not True:
        raise LiveTunnelCheckFailure(f"Tak tunnel verify failed: {verify_tak}")
    if tak_amnezia_tool_cmd and "fake_amnezia_tool.py" in tak_amnezia_tool_cmd:
        print("OK: live Tak tunnel provision command-path check passed")
        return

    attach = _agent_call(
        host=tic_host,
        port=tic_port,
        password=tic_password,
        host_key=tic_host_key,
        component="tic-agent",
        exec_mode="system",
        extra_env={"WG_QUICK_USERSPACE_IMPLEMENTATION": tic_userspace_impl} if tic_userspace_impl else None,
        payload={
            **_payload_base("attach_tak_tunnel", "tic-agent", "tunnel.tak.attach.v1"),
            "server": tic_server,
            "tak_server": tak_server,
            "tunnel_id": tunnel_id,
            "tunnel_artifacts": tunnel_artifacts,
            "amnezia_config": amnezia_config,
        },
    )
    if attach.get("ok") is not True:
        raise LiveTunnelCheckFailure(f"attach_tak_tunnel returned error: {attach}")

    verify_tic = _agent_call(
        host=tic_host,
        port=tic_port,
        password=tic_password,
        host_key=tic_host_key,
        component="tic-agent",
        exec_mode="system",
        payload={
            **_payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
            "server": tic_server,
            "tak_server": tak_server,
            "tunnel_id": tunnel_id,
        },
    )
    tic_status = verify_tic.get("tunnel_status") or {}
    artifacts = tic_status.get("runtime_artifacts") or {}
    if verify_tic.get("ok") is not True:
        raise LiveTunnelCheckFailure(f"Tic tunnel verify failed: {verify_tic}")
    if artifacts.get("client_config_exists") is not True:
        raise LiveTunnelCheckFailure(f"Tic client config missing after attach: {verify_tic}")
    if artifacts.get("system_config_exists") is not True:
        raise LiveTunnelCheckFailure(f"Tic system config missing after attach: {verify_tic}")

    print("OK: live Tak tunnel check passed")


if __name__ == "__main__":
    try:
        main()
    except LiveTunnelCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
