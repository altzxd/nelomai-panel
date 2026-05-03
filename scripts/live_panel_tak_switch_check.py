from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Interface, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class LivePanelTakSwitchFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelTakSwitchFailure(f"Missing required env {name}")
    return value


def _optional_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelTakSwitchFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelTakSwitchFailure("No admin user found")
        db.expunge(user)
        return user


def _bridge_call(payload: dict[str, object]) -> dict[str, object]:
    import subprocess

    bridge = ROOT_DIR / "scripts" / "live_remote_peer_agent_bridge.py"
    completed = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
            raise LivePanelTakSwitchFailure(f"bridge call failed: {detail}") from exc
        raise LivePanelTakSwitchFailure(f"bridge returned invalid json: {stdout}") from exc
    if completed.returncode != 0 and not isinstance(parsed, dict):
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelTakSwitchFailure(f"bridge call failed: {detail}")
    return parsed


def _payload_base(action: str, component: str, capability: str) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "supported_contracts": ["1.0"],
        "panel_version": "0.1.0",
        "component": component,
        "requested_capabilities": [capability],
        "action": action,
    }


def _cleanup(prefix: str) -> None:
    with SessionLocal() as db:
        for interface in db.execute(select(Interface).where(Interface.name.like(f"{prefix}%"))).scalars().all():
            db.delete(interface)
        for server in db.execute(select(Server).where(Server.name.like(f"{prefix}%"))).scalars().all():
            db.delete(server)
        db.commit()


def _create_server(*, server_id: int, name: str, server_type: ServerType, host: str, ssh_port: int, ssh_password: str) -> int:
    with SessionLocal() as db:
        server = Server(
            id=server_id,
            name=name,
            server_type=server_type,
            host=host,
            ssh_port=ssh_port,
            ssh_login="root",
            ssh_password=ssh_password,
            is_active=True,
            is_excluded=False,
        )
        db.add(server)
        db.commit()
        db.refresh(server)
        return server.id


def _get_interface(interface_id: int) -> Interface:
    with SessionLocal() as db:
        interface = db.get(Interface, interface_id)
        if interface is None:
            raise LivePanelTakSwitchFailure(f"Interface {interface_id} not found")
        db.expunge(interface)
        return interface


def _tunnel_status(server: dict[str, object], counterpart_key: str, counterpart_server: dict[str, object]) -> dict[str, object]:
    component = "tic-agent" if str(server.get("server_type")) == "tic" else "tak-agent"
    return _bridge_call(
        {
            **_payload_base("verify_tak_tunnel_status", component, "tunnel.tak.status.v1"),
            "server": server,
            counterpart_key: counterpart_server,
        }
    )


def main() -> None:
    second_tak_host = _optional_env("NELOMAI_TAK2_HOST")
    second_tak_password = _optional_env("NELOMAI_TAK2_SSH_PASSWORD")
    second_tak_host_key = _optional_env("NELOMAI_TAK2_SSH_HOST_KEY")
    if not second_tak_host or not second_tak_password or not second_tak_host_key:
        print("SKIP: live Tak switch check requires NELOMAI_TAK2_HOST / NELOMAI_TAK2_SSH_PASSWORD / NELOMAI_TAK2_SSH_HOST_KEY")
        return

    tic_host = _required_env("NELOMAI_TIC_HOST")
    tic_password = _required_env("NELOMAI_TIC_SSH_PASSWORD")
    _required_env("NELOMAI_TIC_SSH_HOST_KEY")
    tak1_host = _required_env("NELOMAI_TAK_HOST")
    tak1_password = _required_env("NELOMAI_TAK_SSH_PASSWORD")
    _required_env("NELOMAI_TAK_SSH_HOST_KEY")
    tic_port = int(os.environ.get("NELOMAI_TIC_SSH_PORT", "22"))
    tak1_port = int(os.environ.get("NELOMAI_TAK_SSH_PORT", "22"))
    tak2_port = int(os.environ.get("NELOMAI_TAK2_SSH_PORT", "22"))

    admin = _load_admin()
    previous_command = settings.peer_agent_command
    settings.peer_agent_command = f'"{sys.executable}" ".\\scripts\\live_remote_peer_agent_bridge.py"'

    suffix = uuid.uuid4().hex[:6]
    numeric_suffix = int(suffix, 16)
    prefix = f"live-tak-switch-{suffix}"
    tic_name = f"{prefix}-tic 8q"
    tak1_name = f"{prefix}-tak1 8q"
    tak2_name = f"{prefix}-tak2 8q"
    interface_name = f"{prefix}-if"
    _cleanup(prefix)

    try:
        with TestClient(app) as client:
            headers = _auth_headers(admin)
            tic_id = _create_server(
                server_id=100000 + numeric_suffix,
                name=tic_name,
                server_type=ServerType.TIC,
                host=tic_host,
                ssh_port=tic_port,
                ssh_password=tic_password,
            )
            tak1_id = _create_server(
                server_id=200000 + numeric_suffix,
                name=tak1_name,
                server_type=ServerType.TAK,
                host=tak1_host,
                ssh_port=tak1_port,
                ssh_password=tak1_password,
            )
            tak2_id = _create_server(
                server_id=300000 + numeric_suffix,
                name=tak2_name,
                server_type=ServerType.TAK,
                host=second_tak_host,
                ssh_port=tak2_port,
                ssh_password=second_tak_password,
            )

            tic_server = {"id": tic_id, "name": tic_name, "server_type": "tic", "host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}
            tak1_server = {"id": tak1_id, "name": tak1_name, "server_type": "tak", "host": tak1_host, "ssh_port": tak1_port, "ssh_login": "root", "ssh_password": tak1_password}
            tak2_server = {"id": tak2_id, "name": tak2_name, "server_type": "tak", "host": second_tak_host, "ssh_port": tak2_port, "ssh_login": "root", "ssh_password": second_tak_password}

            listen_port = 34000 + (numeric_suffix % 10000)
            subnet_octet = 160 + (numeric_suffix % 90)
            address_v4 = f"10.254.{subnet_octet}.1/24"
            create = client.post(
                "/api/admin/interfaces",
                json={
                    "name": interface_name,
                    "tic_server_id": tic_id,
                    "listen_port": listen_port,
                    "address_v4": address_v4,
                    "peer_limit": 5,
                },
                headers=headers,
            )
            _assert_status(create, 201, "create live Tak-switch interface")
            interface_id = int(create.json()["id"])

            bind_tak1 = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak1_id},
                headers=headers,
            )
            _assert_status(bind_tak1, 200, "bind Tak1 to interface")

            status_tic_tak1_before = _tunnel_status(tic_server, "tak_server", tak1_server)
            tunnel1_before = status_tic_tak1_before.get("tunnel_status") or {}
            tunnel1_id_before = str(tunnel1_before.get("tunnel_id") or "").strip()
            if not tunnel1_id_before or tunnel1_before.get("is_active") is not True:
                raise LivePanelTakSwitchFailure(f"Tak1 tunnel is not active before switch: {status_tic_tak1_before}")

            switch = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak2_id},
                headers=headers,
            )
            _assert_status(switch, 200, "switch interface to Tak2")

            interface = _get_interface(interface_id)
            if interface.tak_server_id != tak2_id:
                raise LivePanelTakSwitchFailure(f"Interface did not switch to Tak2: tak_server_id={interface.tak_server_id}")
            if interface.tak_tunnel_fallback_active:
                raise LivePanelTakSwitchFailure("Interface entered fallback during Tak switch")

            status_tic_tak2_after = _tunnel_status(tic_server, "tak_server", tak2_server)
            tunnel2_after = status_tic_tak2_after.get("tunnel_status") or {}
            tunnel2_id_after = str(tunnel2_after.get("tunnel_id") or "").strip()
            if not tunnel2_id_after or tunnel2_after.get("is_active") is not True:
                raise LivePanelTakSwitchFailure(f"Tak2 tunnel is not active after switch: {status_tic_tak2_after}")
            if tunnel2_id_after == tunnel1_id_before:
                raise LivePanelTakSwitchFailure("Tak switch reused the old tunnel_id unexpectedly")

            status_tak1_after = _tunnel_status(tak1_server, "tic_server", tic_server)
            if (status_tak1_after.get("tunnel_status") or {}).get("is_active") is True:
                raise LivePanelTakSwitchFailure(f"Tak1 tunnel stayed active after switch: {status_tak1_after}")

        print("OK: live panel tak switch check passed")
    finally:
        settings.peer_agent_command = previous_command
        _cleanup(prefix)


if __name__ == "__main__":
    try:
        main()
    except LivePanelTakSwitchFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
