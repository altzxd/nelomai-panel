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


class LivePanelPartialRepairFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelPartialRepairFailure(f"Missing required env {name}")
    return value


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelPartialRepairFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelPartialRepairFailure("No admin user found")
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
            raise LivePanelPartialRepairFailure(f"bridge call failed: {detail}") from exc
        raise LivePanelPartialRepairFailure(f"bridge returned invalid json: {stdout}") from exc
    if completed.returncode != 0 and not isinstance(parsed, dict):
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelPartialRepairFailure(f"bridge call failed: {detail}")
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


def _latest_manual_repair_event(tic_server_id: int) -> AuditLog | None:
    with SessionLocal() as db:
        event = (
            db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "tak_tunnels.manual_repaired", AuditLog.server_id == tic_server_id)
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
    prefix = f"live-partial-repair-{suffix}"
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

            listen_port = 33000 + (numeric_suffix % 10000)
            subnet_octet = 140 + (numeric_suffix % 100)
            address_v4 = f"10.253.{subnet_octet}.1/24"
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
            _assert_status(create, 201, "create live partial-repair interface")
            interface_id = int(create.json()["id"])

            bind_tak = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak_id},
                headers=headers,
            )
            _assert_status(bind_tak, 200, "bind Tak to partial-repair interface")

            tak_status_before = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tak-agent", "tunnel.tak.status.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                }
            )
            tak_tunnel_before = tak_status_before.get("tunnel_status") or {}
            tunnel_id_before = str(tak_tunnel_before.get("tunnel_id") or "").strip()
            if not tunnel_id_before or tak_tunnel_before.get("is_active") is not True:
                raise LivePanelPartialRepairFailure(f"Tak tunnel is not active before partial repair check: {tak_status_before}")

            detach_tic = _bridge_call(
                {
                    **_payload_base("detach_tak_tunnel", "tic-agent", "tunnel.tak.detach.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            if detach_tic.get("ok") is not True:
                raise LivePanelPartialRepairFailure(f"detach_tak_tunnel on Tic failed: {detach_tic}")

            tic_status_down = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            if (tic_status_down.get("tunnel_status") or {}).get("is_active") is not False:
                raise LivePanelPartialRepairFailure(f"Tic tunnel did not go inactive after detach: {tic_status_down}")

            repair = client.post(
                "/admin/diagnostics/tak-tunnels/repair",
                data={
                    "focused_tic_server_id": str(tic_id),
                    "focused_tak_server_id": str(tak_id),
                },
                headers=headers,
            )
            _assert_status(repair, 200, "render diagnostics after partial repair")

            tic_status_after = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tic-agent", "tunnel.tak.status.v1"),
                    "server": tic_server,
                    "tak_server": tak_server,
                }
            )
            if (tic_status_after.get("tunnel_status") or {}).get("is_active") is not True:
                raise LivePanelPartialRepairFailure(f"Tic tunnel did not return after partial repair: {tic_status_after}")

            tak_status_after = _bridge_call(
                {
                    **_payload_base("verify_tak_tunnel_status", "tak-agent", "tunnel.tak.status.v1"),
                    "server": tak_server,
                    "tic_server": tic_server,
                }
            )
            tak_tunnel_after = tak_status_after.get("tunnel_status") or {}
            tunnel_id_after = str(tak_tunnel_after.get("tunnel_id") or "").strip()
            if tunnel_id_after != tunnel_id_before:
                raise LivePanelPartialRepairFailure(
                    f"Partial repair unexpectedly changed tunnel_id: before={tunnel_id_before}, after={tunnel_id_after}"
                )

            event = _latest_manual_repair_event(tic_id)
            if event is None:
                raise LivePanelPartialRepairFailure("tak_tunnels.manual_repaired event not found after partial repair")
            try:
                details = json.loads(event.details or "{}")
            except json.JSONDecodeError:
                details = {}
            strategy = str(details.get("repair_strategy") or "").strip()
            if strategy != "partial":
                raise LivePanelPartialRepairFailure(f"Expected repair_strategy=partial, got {strategy!r}; details={details}")

        print("OK: live panel tak partial repair check passed")
    finally:
        settings.peer_agent_command = previous_command
        _cleanup(prefix)


if __name__ == "__main__":
    try:
        main()
    except LivePanelPartialRepairFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
