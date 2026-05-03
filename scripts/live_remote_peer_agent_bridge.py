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


PLINK_BIN = Path(r"C:\Program Files\PuTTY\plink.exe")


def _same_host_tak2_emulation_enabled() -> bool:
    return os.environ.get("NELOMAI_ENABLE_TAK2_SAME_HOST_EMULATION", "").strip() == "1"


def _host_key_for(host: str) -> str:
    tic_host = os.environ.get("NELOMAI_TIC_HOST", "").strip()
    tak_host = os.environ.get("NELOMAI_TAK_HOST", "").strip()
    tak2_host = os.environ.get("NELOMAI_TAK2_HOST", "").strip()
    if host == tic_host:
        return os.environ.get("NELOMAI_TIC_SSH_HOST_KEY", "").strip()
    if host == tak2_host and tak2_host and tak2_host != tak_host:
        return os.environ.get("NELOMAI_TAK2_SSH_HOST_KEY", "").strip()
    if host == tak_host:
        return os.environ.get("NELOMAI_TAK_SSH_HOST_KEY", "").strip()
    if host == tak2_host and tak_host == tak2_host and _same_host_tak2_emulation_enabled():
        return os.environ.get("NELOMAI_TAK2_SSH_HOST_KEY", "").strip()
    return ""


def _env_file_for(component: str, server: dict[str, object]) -> str:
    normalized = component.replace("-agent", "")
    default_env_file = f"/etc/default/nelomai-{normalized}-agent"
    if component != "tak-agent":
        return default_env_file

    tak_host = os.environ.get("NELOMAI_TAK_HOST", "").strip()
    tak2_host = os.environ.get("NELOMAI_TAK2_HOST", "").strip()
    host = str(server.get("host") or "").strip()
    if (
        not _same_host_tak2_emulation_enabled()
        or not tak_host
        or not tak2_host
        or tak_host != tak2_host
        or host != tak_host
    ):
        return default_env_file

    name = str(server.get("name") or "").strip().lower()
    token = os.environ.get("NELOMAI_TAK2_NAME_TOKEN", "tak2").strip().lower()
    if token and token in name:
        return os.environ.get("NELOMAI_TAK2_ENV_FILE", "/etc/default/nelomai-tak2-agent").strip() or "/etc/default/nelomai-tak2-agent"
    return default_env_file


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    raw_payload = sys.stdin.read()
    try:
        payload = json.loads(raw_payload or "{}")
    except json.JSONDecodeError:
        fail("invalid json payload")

    server = payload.get("server")
    if not isinstance(server, dict):
        server = payload.get("tic_server")
    if not isinstance(server, dict):
        fail("missing server payload")

    host = str(server.get("host") or "").strip()
    ssh_login = str(server.get("ssh_login") or "").strip() or "root"
    ssh_password = str(server.get("ssh_password") or "").strip()
    ssh_port = int(server.get("ssh_port") or 22)
    server_id = server.get("id")
    if (not host or not ssh_password) and isinstance(server_id, int):
        with SessionLocal() as db:
            record = db.get(Server, server_id)
            if record is not None:
                host = host or record.host
                ssh_login = ssh_login or record.ssh_login or "root"
                ssh_password = ssh_password or record.ssh_password or ""
                ssh_port = ssh_port or record.ssh_port or 22
    if not host or not ssh_password:
        fail("server payload must include host and ssh_password")
    if not PLINK_BIN.exists():
        fail(f"plink not found: {PLINK_BIN}")

    component = str(payload.get("component") or "tic-agent").strip() or "tic-agent"
    exec_mode = str(payload.get("exec_mode") or "system").strip() or "system"
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    env_file = _env_file_for(component, server)
    remote_command = (
        "bash -lc "
        f"\"set -a && test -f '{env_file}' && . '{env_file}'; set +a; "
        f"tmp=\\$(mktemp /tmp/nelomai-bridge-XXXXXX.json) && "
        f"printf %s '{payload_b64}' | base64 -d > \\\"\\$tmp\\\" && "
        f"NELOMAI_AGENT_COMPONENT={component} "
        f"NELOMAI_AGENT_EXEC_MODE={exec_mode} "
        f"/usr/bin/node /opt/nelomai/current/agents/node-tic-agent/src/index.js < \\\"\\$tmp\\\"; "
        f"status=\\$?; rm -f \\\"\\$tmp\\\"; exit \\$status\""
    )
    completed = subprocess.run(
        [
            str(PLINK_BIN),
            "-batch",
            "-ssh",
            f"{ssh_login}@{host}",
            "-P",
            str(ssh_port),
            "-pw",
            ssh_password,
            *(
                ["-hostkey", host_key]
                if (host_key := _host_key_for(host))
                else []
            ),
            remote_command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        fail(detail)
    stdout = (completed.stdout or "").strip()
    if not stdout:
        print("{}")
        return
    print(stdout)


if __name__ == "__main__":
    main()
