from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.models import Server
from app.security import decrypt_secret


def _load_local_env_defaults() -> dict[str, str]:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_LOCAL_ENV_DEFAULTS = _load_local_env_defaults()


def _env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        value = _LOCAL_ENV_DEFAULTS.get(name, default)
    return str(value).strip() or default


PLINK_BIN = Path(_env_value("NELOMAI_PANEL_PLINK_BIN", r"C:\Program Files\PuTTY\plink.exe"))
SSH_STRICT_HOST_KEY_CHECKING = _env_value("NELOMAI_PANEL_SSH_STRICT_HOST_KEY_CHECKING", "accept-new")
SSH_CONNECT_TIMEOUT = _env_value("NELOMAI_PANEL_SSH_CONNECT_TIMEOUT", "10")
SSH_KNOWN_HOSTS_FILE = _env_value("NELOMAI_PANEL_SSH_KNOWN_HOSTS_FILE", "")
SSH_PASS_BIN = _env_value("NELOMAI_PANEL_SSHPASS_BIN", "sshpass")
SSH_BIN = _env_value("NELOMAI_PANEL_SSH_BIN", "ssh")
SSH_KEY_FILE = _env_value("NELOMAI_PANEL_SSH_KEY_FILE", "")
AGENT_HEARTBEAT_MAX_AGE_SEC = _env_value("NELOMAI_PANEL_AGENT_HEARTBEAT_MAX_AGE_SEC", "120")
AGENT_AUTO_RESTART_STALE = _env_value("NELOMAI_PANEL_AGENT_AUTO_RESTART_STALE", "1")


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _env_file_for(component: str) -> str:
    normalized = component.replace("-agent", "")
    return f"/etc/default/nelomai-{normalized}-agent"


def _service_name_for(component: str) -> str:
    normalized = component.replace("-agent", "")
    return f"nelomai-{normalized}-agent.service"


def _default_status_file_for(component: str) -> str:
    normalized = component.replace("-agent", "")
    return f"/opt/nelomai/state/{normalized}-agent-daemon-status.json"


def _load_server_from_payload(payload: dict[str, object]) -> dict[str, object]:
    server = payload.get("server")
    if not isinstance(server, dict):
        server = payload.get("tic_server")
    if not isinstance(server, dict):
        fail("missing server payload")
    return server


def _resolve_server_identity(server: dict[str, object]) -> tuple[str, str, str, int]:
    host = str(server.get("host") or "").strip()
    ssh_login = str(server.get("ssh_login") or "").strip() or "root"
    ssh_password = decrypt_secret(str(server.get("ssh_password") or "").strip())
    ssh_port = int(server.get("ssh_port") or 22)
    server_id = server.get("id")
    if (not host or not ssh_password) and isinstance(server_id, int):
        with SessionLocal() as db:
            record = db.get(Server, server_id)
            if record is not None:
                host = host or record.host
                ssh_login = ssh_login or record.ssh_login or "root"
                ssh_password = ssh_password or decrypt_secret(record.ssh_password or "")
                ssh_port = ssh_port or record.ssh_port or 22
    if not host:
        fail("server payload must include host")
    if not ssh_password and not SSH_KEY_FILE:
        fail("server payload must include ssh_password or panel SSH key must be configured")
    return host, ssh_login, ssh_password, ssh_port


def _remote_command(component: str, exec_mode: str, payload_b64: str) -> str:
    env_file = _env_file_for(component)
    service_name = _service_name_for(component)
    default_status_file = _default_status_file_for(component)
    return (
        "bash -lc "
        f"\"set -a && test -f '{env_file}' && . '{env_file}'; set +a; "
        f"service_name='{service_name}'; "
        f"status_file=\\${{NELOMAI_AGENT_DAEMON_STATUS_FILE:-{default_status_file}}}; "
        f"max_age='{AGENT_HEARTBEAT_MAX_AGE_SEC}'; "
        f"auto_restart='{AGENT_AUTO_RESTART_STALE}'; "
        "if [ \\\"$auto_restart\\\" = '1' ]; then "
        "restart_needed=0; "
        "systemctl is-active --quiet \\\"$service_name\\\" || restart_needed=1; "
        "if [ -f \\\"$status_file\\\" ]; then "
        "now=\\$(date +%s); "
        "mtime=\\$(stat -c %Y \\\"$status_file\\\" 2>/dev/null || echo 0); "
        "age=\\$((now - mtime)); "
        "[ \\\"$age\\\" -gt \\\"$max_age\\\" ] && restart_needed=1; "
        "else restart_needed=1; fi; "
        "if [ \\\"$restart_needed\\\" -eq 1 ]; then "
        "echo \\\"[agent_watchdog] restarting $service_name (status_file=$status_file max_age=$max_age)\\\" >&2; "
        "systemctl restart \\\"$service_name\\\" || { echo '[agent_watchdog_restart_failed] systemctl restart failed' >&2; exit 91; }; "
        "sleep 2; "
        "systemctl is-active --quiet \\\"$service_name\\\" || { echo '[agent_watchdog_restart_failed] service not active after restart' >&2; exit 92; }; "
        "fi; "
        "fi; "
        f"tmp=\\$(mktemp /tmp/nelomai-bridge-XXXXXX.json) && "
        f"printf %s '{payload_b64}' | base64 -d > \\\"\\$tmp\\\" && "
        f"NELOMAI_AGENT_COMPONENT={component} "
        f"NELOMAI_AGENT_EXEC_MODE={exec_mode} "
        f"/usr/bin/node /opt/nelomai/current/agents/node-tic-agent/src/index.js < \\\"\\$tmp\\\"; "
        f"status=\\$?; rm -f \\\"\\$tmp\\\"; exit \\$status\""
    )


def _run_windows_plink(host: str, ssh_login: str, ssh_password: str, ssh_port: int, remote_command: str) -> subprocess.CompletedProcess[str]:
    if not PLINK_BIN.exists():
        fail(f"plink not found: {PLINK_BIN}")
    return subprocess.run(
        [
            str(PLINK_BIN),
            "-batch",
            "-ssh",
            f"{ssh_login}@{host}",
            "-P",
            str(ssh_port),
            "-pw",
            ssh_password,
            remote_command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_linux_ssh(host: str, ssh_login: str, ssh_password: str, ssh_port: int, remote_command: str) -> subprocess.CompletedProcess[str]:
    command = [
        SSH_PASS_BIN,
        "-p",
        ssh_password,
        SSH_BIN,
        "-o",
        f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY_CHECKING}",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
    ]
    if SSH_KNOWN_HOSTS_FILE:
        command.extend(["-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS_FILE}"])
    command.extend(
        [
            "-p",
            str(ssh_port),
            f"{ssh_login}@{host}",
            remote_command,
        ]
    )
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_linux_ssh_key(host: str, ssh_login: str, ssh_port: int, remote_command: str, key_file: str) -> subprocess.CompletedProcess[str]:
    command = [
        SSH_BIN,
        "-o",
        "BatchMode=yes",
        "-o",
        f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY_CHECKING}",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-i",
        key_file,
    ]
    if SSH_KNOWN_HOSTS_FILE:
        command.extend(["-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS_FILE}"])
    command.extend(
        [
            "-p",
            str(ssh_port),
            f"{ssh_login}@{host}",
            remote_command,
        ]
    )
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> None:
    raw_payload = sys.stdin.read()
    try:
        payload = json.loads(raw_payload or "{}")
    except json.JSONDecodeError:
        fail("invalid json payload")

    server = _load_server_from_payload(payload)
    host, ssh_login, ssh_password, ssh_port = _resolve_server_identity(server)
    component = str(payload.get("component") or "tic-agent").strip() or "tic-agent"
    exec_mode = str(payload.get("exec_mode") or "system").strip() or "system"
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    remote_command = _remote_command(component, exec_mode, payload_b64)

    if sys.platform == "win32":
        completed = _run_windows_plink(host, ssh_login, ssh_password, ssh_port, remote_command)
    else:
        completed = None
        key_file = SSH_KEY_FILE
        if key_file:
            key_path = Path(key_file)
            if not key_path.exists():
                fail(f"panel SSH key file not found: {key_file}")
            completed = _run_linux_ssh_key(host, ssh_login, ssh_port, remote_command, key_file)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
                fail(detail)
        if completed is None or completed.returncode != 0:
            completed = _run_linux_ssh(host, ssh_login, ssh_password, ssh_port, remote_command)

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = f"[bridge_exec_failed] rc={completed.returncode}; stderr={stderr or '<empty>'}; stdout={stdout or '<empty>'}"
        fail(detail)

    stdout = (completed.stdout or "").strip()
    if not stdout:
        print("{}")
        return
    print(stdout)


if __name__ == "__main__":
    main()
