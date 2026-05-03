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
from app.models import AuditLog, Interface, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class LivePanelTakRotationFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelTakRotationFailure(f"Missing required env {name}")
    return value


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelTakRotationFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelTakRotationFailure("No admin user found")
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
            raise LivePanelTakRotationFailure(f"bridge call failed: {detail}") from exc
        raise LivePanelTakRotationFailure(f"bridge returned invalid json: {stdout}") from exc
    if completed.returncode != 0 and not isinstance(parsed, dict):
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelTakRotationFailure(f"bridge call failed: {detail}")
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
            raise LivePanelTakRotationFailure(f"Interface {interface_id} not found")
        db.expunge(interface)
        return interface


def _latest_rotation_event(tic_server_id: int) -> AuditLog | None:
    with SessionLocal() as db:
        event = (
            db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "tak_tunnels.artifacts_rotated", AuditLog.server_id == tic_server_id)
                .order_by(AuditLog.id.desc())
            )
            .scalars()
            .first()
        )
        if event is not None:
            db.expunge(event)
        return event


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
    numeric_suffix = int(suffix, 16)
    prefix = f"live-tak-rotate-{suffix}"
    tic_name = f"{prefix}-tic 8q"
    tak_name = f"{prefix}-tak 8q"
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
            tak_id = _create_server(
                server_id=200000 + numeric_suffix,
                name=tak_name,
                server_type=ServerType.TAK,
                host=tak_host,
                ssh_port=tak_port,
                ssh_password=tak_password,
            )

            tic_server = {"id": tic_id, "name": tic_name, "server_type": "tic", "host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}
            tak_server = {"id": tak_id, "name": tak_name, "server_type": "tak", "host": tak_host, "ssh_port": tak_port, "ssh_login": "root", "ssh_password": tak_password}

            listen_port = 35000 + (numeric_suffix % 10000)
            subnet_octet = 170 + (numeric_suffix % 80)
            address_v4 = f"10.252.{subnet_octet}.1/24"
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
            _assert_status(create, 201, "create live Tak-rotation interface")
            interface_id = int(create.json()["id"])

            bind_tak = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak_id},
                headers=headers,
            )
            _assert_status(bind_tak, 200, "bind Tak to rotation interface")

            provision_before = _bridge_call(
                {
                    **_payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
                    "exec_mode": "filesystem",
                    "server": tak_server,
                    "tic_server": tic_server,
                    "reuse_existing_only": True,
                }
            )
            if provision_before.get("ok") is not True:
                raise LivePanelTakRotationFailure(f"initial Tak provision query failed: {provision_before}")
            tunnel_id_before = str(provision_before.get("tunnel_id") or "").strip()
            revision_before = int(provision_before.get("artifact_revision") or 0)
            if not tunnel_id_before or revision_before <= 0:
                raise LivePanelTakRotationFailure(f"missing initial tunnel identity/revision: {provision_before}")

            rotate = client.post(
                "/admin/diagnostics/tak-tunnels/rotate",
                data={
                    "focused_tic_server_id": str(tic_id),
                    "focused_tak_server_id": str(tak_id),
                },
                headers=headers,
            )
            _assert_status(rotate, 200, "render diagnostics after tunnel artifact rotation")

            interface = _get_interface(interface_id)
            if interface.tak_tunnel_fallback_active:
                raise LivePanelTakRotationFailure("Interface entered fallback during tunnel artifact rotation")

            provision_after = _bridge_call(
                {
                    **_payload_base("provision_tak_tunnel", "tak-agent", "tunnel.tak.provision.v1"),
                    "exec_mode": "filesystem",
                    "server": tak_server,
                    "tic_server": tic_server,
                    "reuse_existing_only": True,
                }
            )
            if provision_after.get("ok") is not True:
                raise LivePanelTakRotationFailure(f"post-rotate Tak provision query failed: {provision_after}")
            tunnel_id_after = str(provision_after.get("tunnel_id") or "").strip()
            revision_after = int(provision_after.get("artifact_revision") or 0)
            if tunnel_id_after != tunnel_id_before:
                raise LivePanelTakRotationFailure(
                    f"artifact rotation changed tunnel_id unexpectedly: before={tunnel_id_before}, after={tunnel_id_after}"
                )
            if revision_after <= revision_before:
                raise LivePanelTakRotationFailure(
                    f"artifact revision did not increase: before={revision_before}, after={revision_after}"
                )

            tic_status_after = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                    "tunnel_id": tunnel_id_after,
                }
            )
            if (tic_status_after.get("tunnel_status") or {}).get("is_active") is not True:
                raise LivePanelTakRotationFailure(f"Tic tunnel is not active after artifact rotation: {tic_status_after}")

            tak_status_after = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tak-agent", "tunnel.tak.status.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                    "tunnel_id": tunnel_id_after,
                }
            )
            if (tak_status_after.get("tunnel_status") or {}).get("is_active") is not True:
                raise LivePanelTakRotationFailure(f"Tak tunnel is not active after artifact rotation: {tak_status_after}")

            event = _latest_rotation_event(tic_id)
            if event is None:
                raise LivePanelTakRotationFailure("tak_tunnels.artifacts_rotated event not found after rotation")
            try:
                details = json.loads(event.details or "{}")
            except json.JSONDecodeError:
                details = {}
            if str(details.get("tunnel_id") or "").strip() != tunnel_id_after:
                raise LivePanelTakRotationFailure(f"rotation audit event has unexpected tunnel_id: {details}")
            if int(details.get("artifact_revision") or 0) != revision_after:
                raise LivePanelTakRotationFailure(f"rotation audit event has unexpected artifact_revision: {details}")

        print("OK: live panel tak rotation check passed")
    finally:
        settings.peer_agent_command = previous_command
        _cleanup(prefix)


if __name__ == "__main__":
    try:
        main()
    except LivePanelTakRotationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
