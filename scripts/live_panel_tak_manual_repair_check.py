from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
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
from app.models import AppSetting, AuditLog, Interface, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import (
    TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT,
    TAK_TUNNEL_REPAIR_STATE_KEY,
    ensure_default_settings,
    ensure_seed_data,
)


class LivePanelManualRepairFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelManualRepairFailure(f"Missing required env {name}")
    return value


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelManualRepairFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelManualRepairFailure("No admin user found")
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
            raise LivePanelManualRepairFailure(f"bridge call failed: {detail}") from exc
        raise LivePanelManualRepairFailure(f"bridge returned invalid json: {stdout}") from exc
    if completed.returncode != 0 and not isinstance(parsed, dict):
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelManualRepairFailure(f"bridge call failed: {detail}")
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


def _pair_key(tic_server_id: int, tak_server_id: int) -> str:
    return f"tic:{tic_server_id}|tak:{tak_server_id}"


def _load_repair_state_from_db(db: Any) -> dict[str, dict[str, object]]:
    row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
    if row is None or not row.value:
        return {}
    try:
        payload = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): dict(value) for key, value in payload.items() if isinstance(key, str) and isinstance(value, dict)}


def _save_repair_state(db: Any, state: dict[str, dict[str, object]]) -> None:
    row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
    payload = json.dumps(state, ensure_ascii=False) if state else ""
    if row is None:
        row = AppSetting(key=TAK_TUNNEL_REPAIR_STATE_KEY, value=payload)
    else:
        row.value = payload
    db.add(row)


def _cleanup(prefix: str, pair_key: str) -> None:
    with SessionLocal() as db:
        state = _load_repair_state_from_db(db)
        if pair_key in state:
            state.pop(pair_key, None)
            _save_repair_state(db, state)
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
            raise LivePanelManualRepairFailure(f"Interface {interface_id} not found")
        db.expunge(interface)
        return interface


def _read_pair_state(pair_key: str) -> dict[str, object]:
    with SessionLocal() as db:
        return dict(_load_repair_state_from_db(db).get(pair_key) or {})


def _force_pair_cooldown_expired(pair_key: str) -> None:
    with SessionLocal() as db:
        state = _load_repair_state_from_db(db)
        pair_state = dict(state.get(pair_key) or {})
        if not pair_state:
            raise LivePanelManualRepairFailure(f"Pair state {pair_key} not found")
        pair_state["cooldown_until"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        state[pair_key] = pair_state
        _save_repair_state(db, state)
        db.commit()


def _assert_manual_attention(pair_key: str) -> dict[str, object]:
    state = _read_pair_state(pair_key)
    failure_count = int(state.get("failure_count") or 0)
    if failure_count != TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT:
        raise LivePanelManualRepairFailure(
            f"Expected failure_count={TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT}, got {failure_count}; state={state}"
        )
    if not bool(state.get("manual_attention_required")):
        raise LivePanelManualRepairFailure(f"Pair did not enter manual_attention_required: {state}")
    return state


def _assert_success_reset(pair_key: str) -> dict[str, object]:
    state = _read_pair_state(pair_key)
    if int(state.get("failure_count") or 0) != 0:
        raise LivePanelManualRepairFailure(f"failure_count did not reset after manual repair: {state}")
    if bool(state.get("manual_attention_required")):
        raise LivePanelManualRepairFailure(f"manual_attention_required stayed true after manual repair: {state}")
    if not str(state.get("last_recovered_at") or "").strip():
        raise LivePanelManualRepairFailure(f"last_recovered_at missing after manual repair: {state}")
    return state


def _assert_manual_repair_audit_event(tic_server_id: int) -> None:
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
        if event is None:
            raise LivePanelManualRepairFailure("tak_tunnels.manual_repaired audit event not found")


def main() -> None:
    tic_host = _required_env("NELOMAI_TIC_HOST")
    tic_password = _required_env("NELOMAI_TIC_SSH_PASSWORD")
    _required_env("NELOMAI_TIC_SSH_HOST_KEY")
    tak_host = _required_env("NELOMAI_TAK_HOST")
    tak_password = _required_env("NELOMAI_TAK_SSH_PASSWORD")
    tak_host_key = _required_env("NELOMAI_TAK_SSH_HOST_KEY")
    tic_port = int(os.environ.get("NELOMAI_TIC_SSH_PORT", "22"))
    tak_port = int(os.environ.get("NELOMAI_TAK_SSH_PORT", "22"))

    admin = _load_admin()
    previous_command = settings.peer_agent_command
    previous_tak_host_key = tak_host_key
    settings.peer_agent_command = f'"{sys.executable}" ".\\scripts\\live_remote_peer_agent_bridge.py"'

    suffix = uuid.uuid4().hex[:6]
    numeric_suffix = int(suffix, 16)
    prefix = f"live-manual-repair-{suffix}"
    tic_name = f"{prefix}-tic 8q"
    tak_name = f"{prefix}-tak 8q"
    interface_name = f"{prefix}-if"
    pair_key = _pair_key(100000 + numeric_suffix, 200000 + numeric_suffix)
    _cleanup(prefix, pair_key)

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
            pair_key = _pair_key(tic_id, tak_id)

            tic_server = {"id": tic_id, "name": tic_name, "server_type": "tic", "host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}
            tak_server = {"id": tak_id, "name": tak_name, "server_type": "tak", "host": tak_host, "ssh_port": tak_port, "ssh_login": "root", "ssh_password": tak_password}

            listen_port = 32000 + (numeric_suffix % 10000)
            subnet_octet = 120 + (numeric_suffix % 100)
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
            _assert_status(create, 201, "create live manual-repair interface")
            interface_id = int(create.json()["id"])

            bind_tak = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak_id},
                headers=headers,
            )
            _assert_status(bind_tak, 200, "bind Tak to manual-repair interface")

            for component, payload in (
                ("tic-agent", {"server": tic_server, "tak_server": tak_server}),
                ("tak-agent", {"server": tak_server, "tic_server": tic_server}),
            ):
                response = _bridge_call(
                    {
                        **_payload_base("detach_tak_tunnel", component, "tunnel.tak.detach.v1"),
                        **payload,
                    }
                )
                if response.get("ok") is not True:
                    raise LivePanelManualRepairFailure(f"detach_tak_tunnel failed for {component}: {response}")

            os.environ["NELOMAI_TAK_SSH_HOST_KEY"] = "SHA256:nelomai-invalid-host-key"

            for attempt in range(1, TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT + 1):
                if attempt > 1:
                    _force_pair_cooldown_expired(pair_key)
                refresh = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
                _assert_status(refresh, 200, f"refresh tic for manual-repair attempt {attempt}")

            manual_state = _assert_manual_attention(pair_key)
            interface = _get_interface(interface_id)
            if not interface.tak_tunnel_fallback_active:
                raise LivePanelManualRepairFailure("Interface did not enter fallback before manual repair")

            os.environ["NELOMAI_TAK_SSH_HOST_KEY"] = previous_tak_host_key

            repair = client.post(
                "/admin/diagnostics/tak-tunnels/repair",
                data={
                    "focused_tic_server_id": str(tic_id),
                    "focused_tak_server_id": str(tak_id),
                },
                headers=headers,
            )
            if repair.status_code != 200:
                raise LivePanelManualRepairFailure(
                    f"manual repair endpoint did not render successfully: status={repair.status_code}; body={repair.text[:1000]}"
                )

            reset_state = _assert_success_reset(pair_key)
            if reset_state.get("last_recovered_at") == manual_state.get("last_recovered_at"):
                raise LivePanelManualRepairFailure("last_recovered_at did not change after manual repair")
            interface = _get_interface(interface_id)
            if interface.tak_tunnel_fallback_active:
                raise LivePanelManualRepairFailure("Interface stayed in fallback after manual repair")
            _assert_manual_repair_audit_event(tic_id)

        print("OK: live panel tak manual repair check passed")
    finally:
        os.environ["NELOMAI_TAK_SSH_HOST_KEY"] = previous_tak_host_key
        try:
            for component, payload in (
                ("tic-agent", {"server": {"host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}, "tak_server": {"host": tak_host, "ssh_port": tak_port, "ssh_login": "root", "ssh_password": tak_password}}),
                ("tak-agent", {"server": {"host": tak_host, "ssh_port": tak_port, "ssh_login": "root", "ssh_password": tak_password}, "tic_server": {"host": tic_host, "ssh_port": tic_port, "ssh_login": "root", "ssh_password": tic_password}}),
            ):
                try:
                    _bridge_call(
                        {
                            **_payload_base("detach_tak_tunnel", component, "tunnel.tak.detach.v1"),
                            **payload,
                        }
                    )
                except Exception:
                    pass
        finally:
            settings.peer_agent_command = previous_command
            _cleanup(prefix, pair_key)


if __name__ == "__main__":
    try:
        main()
    except LivePanelManualRepairFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
