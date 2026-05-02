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


class LivePanelFallbackFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelFallbackFailure(f"Missing required env {name}")
    return value


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelFallbackFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelFallbackFailure("No admin user found")
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
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelFallbackFailure(f"bridge call failed: {detail}")
    stdout = (completed.stdout or "").strip()
    try:
        return json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise LivePanelFallbackFailure(f"bridge returned invalid json: {stdout}") from exc


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


def _create_server(*, name: str, server_type: ServerType, host: str, ssh_port: int, ssh_password: str) -> int:
    with SessionLocal() as db:
        server = Server(
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
            raise LivePanelFallbackFailure(f"Interface {interface_id} not found")
        db.expunge(interface)
        return interface


def main() -> None:
    tic_host = _required_env("NELOMAI_TIC_HOST")
    tic_password = _required_env("NELOMAI_TIC_SSH_PASSWORD")
    _required_env("NELOMAI_TIC_SSH_HOST_KEY")
    tak_host = _required_env("NELOMAI_TAK_HOST")
    tak_password = _required_env("NELOMAI_TAK_SSH_PASSWORD")
    _required_env("NELOMAI_TAK_SSH_HOST_KEY")
    tic_port = int(os.environ.get("NELOMAI_TIC_SSH_PORT", "22"))
    tak_port = int(os.environ.get("NELOMAI_TAK_SSH_PORT", "22"))

    admin = _load_admin()
    previous_command = settings.peer_agent_command
    settings.peer_agent_command = f'"{sys.executable}" ".\\scripts\\live_remote_peer_agent_bridge.py"'

    suffix = uuid.uuid4().hex[:6]
    prefix = f"live-fallback-{suffix}"
    tic_name = f"{prefix}-tic 8q"
    tak_name = f"{prefix}-tak 8q"
    interface_name = f"{prefix}-if"
    _cleanup(prefix)

    try:
        with TestClient(app) as client:
            headers = _auth_headers(admin)
            tic_id = _create_server(
                name=tic_name,
                server_type=ServerType.TIC,
                host=tic_host,
                ssh_port=tic_port,
                ssh_password=tic_password,
            )
            tak_id = _create_server(
                name=tak_name,
                server_type=ServerType.TAK,
                host=tak_host,
                ssh_port=tak_port,
                ssh_password=tak_password,
            )

            tic_server = {"id": tic_id, "name": tic_name, "server_type": "tic", "host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}
            tak_server = {"id": tak_id, "name": tak_name, "server_type": "tak", "host": tak_host, "ssh_port": tak_port, "ssh_login": "root", "ssh_password": tak_password}

            pre_detach_tic = _bridge_call(
                {
                    **_payload_base("detach_tak_tunnel", "tic-agent", "tunnel.tak.detach.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            if pre_detach_tic.get("ok") is not True and "requires known tunnel" not in str(pre_detach_tic.get("error") or ""):
                raise LivePanelFallbackFailure(f"pre-clean detach_tak_tunnel on Tic failed: {pre_detach_tic}")
            pre_detach_tak = _bridge_call(
                {
                    **_payload_base("detach_tak_tunnel", "tak-agent", "tunnel.tak.detach.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                }
            )
            if pre_detach_tak.get("ok") is not True and "requires known tunnel" not in str(pre_detach_tak.get("error") or ""):
                raise LivePanelFallbackFailure(f"pre-clean detach_tak_tunnel on Tak failed: {pre_detach_tak}")

            prepare = client.post(
                "/api/admin/interfaces/prepare",
                json={"name": interface_name, "tic_server_id": tic_id, "tak_server_id": tak_id},
                headers=headers,
            )
            _assert_status(prepare, 200, "prepare live via_tak interface")
            prepared = prepare.json()
            create = client.post(
                "/api/admin/interfaces",
                json={
                    "name": interface_name,
                    "tic_server_id": tic_id,
                    "tak_server_id": tak_id,
                    "listen_port": prepared["listen_port"],
                    "address_v4": prepared["address_v4"],
                    "peer_limit": 5,
                },
                headers=headers,
            )
            _assert_status(create, 201, "create live via_tak interface")
            interface_id = int(create.json()["id"])

            detach_tic = _bridge_call(
                {
                    **_payload_base("detach_tak_tunnel", "tic-agent", "tunnel.tak.detach.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            if detach_tic.get("ok") is not True:
                raise LivePanelFallbackFailure(f"detach_tak_tunnel on Tic failed: {detach_tic}")
            detach_tak = _bridge_call(
                {
                    **_payload_base("detach_tak_tunnel", "tak-agent", "tunnel.tak.detach.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                }
            )
            if detach_tak.get("ok") is not True:
                raise LivePanelFallbackFailure(f"detach_tak_tunnel on Tak failed: {detach_tak}")

            verify_down = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            tunnel_status = verify_down.get("tunnel_status") or {}
            if tunnel_status.get("is_active") is not False:
                raise LivePanelFallbackFailure(f"Tunnel still looks active after detach: {verify_down}")

            refresh_down = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
            _assert_status(refresh_down, 200, "refresh tic after tunnel down")
            interface = _get_interface(interface_id)
            if not interface.tak_tunnel_fallback_active:
                dashboard = client.get("/dashboard", headers=headers)
                _assert_status(dashboard, 200, "dashboard reconcile after refresh")
                interface = _get_interface(interface_id)
                if not interface.tak_tunnel_fallback_active:
                    raise LivePanelFallbackFailure("Interface did not switch to fallback after refresh or dashboard reconcile")

            provision = _bridge_call(
                {
                    **_payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                }
            )
            if provision.get("ok") is not True:
                raise LivePanelFallbackFailure(f"provision_tak_tunnel failed: {provision}")
            attach = _bridge_call(
                {
                    **_payload_base("attach_tak_tunnel", "tic-agent", "tunnel.tak.attach.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                    "tunnel_id": provision.get("tunnel_id"),
                    "tunnel_artifacts": provision.get("tunnel_artifacts"),
                    "amnezia_config": provision.get("amnezia_config"),
                }
            )
            if attach.get("ok") is not True:
                raise LivePanelFallbackFailure(f"attach_tak_tunnel failed: {attach}")

            refresh_up = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
            _assert_status(refresh_up, 200, "refresh tic after tunnel restore")
            interface = _get_interface(interface_id)
            if interface.tak_tunnel_fallback_active:
                raise LivePanelFallbackFailure("Interface did not return from fallback after refresh")

        print("OK: live panel tak fallback check passed")
    finally:
        settings.peer_agent_command = previous_command
        _cleanup(prefix)


if __name__ == "__main__":
    try:
        main()
    except LivePanelFallbackFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
