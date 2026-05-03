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


class LivePanelClearBackoffFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LivePanelClearBackoffFailure(f"Missing required env {name}")
    return value


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LivePanelClearBackoffFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LivePanelClearBackoffFailure("No admin user found")
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
            raise LivePanelClearBackoffFailure(f"bridge call failed: {detail}") from exc
        raise LivePanelClearBackoffFailure(f"bridge returned invalid json: {stdout}") from exc
    if completed.returncode != 0 and not isinstance(parsed, dict):
        detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
        raise LivePanelClearBackoffFailure(f"bridge call failed: {detail}")
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
            raise LivePanelClearBackoffFailure(f"Interface {interface_id} not found")
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
            raise LivePanelClearBackoffFailure(f"Pair state {pair_key} not found")
        pair_state["cooldown_until"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        state[pair_key] = pair_state
        _save_repair_state(db, state)
        db.commit()


def _require_failure_state(pair_key: str, *, failure_count: int, manual_attention_required: bool) -> dict[str, object]:
    state = _read_pair_state(pair_key)
    actual_failure_count = int(state.get("failure_count") or 0)
    if actual_failure_count != failure_count:
        raise LivePanelClearBackoffFailure(
            f"Unexpected failure_count for {pair_key}: expected {failure_count}, got {actual_failure_count}; state={state}"
        )
    actual_manual = bool(state.get("manual_attention_required"))
    if actual_manual is not manual_attention_required:
        raise LivePanelClearBackoffFailure(
            f"Unexpected manual_attention_required for {pair_key}: expected {manual_attention_required}, got {actual_manual}; state={state}"
        )
    return state


def _assert_backoff_cleared_event(tic_server_id: int, tak_server_id: int) -> None:
    with SessionLocal() as db:
        event = (
            db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "tak_tunnels.backoff_cleared", AuditLog.server_id == tic_server_id)
                .order_by(AuditLog.id.desc())
            )
            .scalars()
            .first()
        )
        if event is None:
            raise LivePanelClearBackoffFailure("tak_tunnels.backoff_cleared audit event not found")
        try:
            details = json.loads(event.details or "{}")
        except json.JSONDecodeError:
            details = {}
        if int(details.get("tic_server_id") or 0) != tic_server_id or int(details.get("tak_server_id") or 0) != tak_server_id:
            raise LivePanelClearBackoffFailure("backoff_cleared event points to wrong pair")


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
    prefix = f"live-clear-backoff-{suffix}"
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

            listen_port = 34000 + (numeric_suffix % 10000)
            subnet_octet = 150 + (numeric_suffix % 90)
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
            _assert_status(create, 201, "create live clear-backoff interface")
            interface_id = int(create.json()["id"])

            bind_tak = client.put(
                f"/api/admin/interfaces/{interface_id}/tak-server",
                json={"tak_server_id": tak_id},
                headers=headers,
            )
            _assert_status(bind_tak, 200, "bind Tak to clear-backoff interface")

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
                    raise LivePanelClearBackoffFailure(f"detach_tak_tunnel failed for {component}: {response}")

            os.environ["NELOMAI_TAK_SSH_HOST_KEY"] = "SHA256:nelomai-invalid-host-key"

            for attempt in range(1, TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT + 1):
                if attempt > 1:
                    _force_pair_cooldown_expired(pair_key)
                refresh = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
                _assert_status(refresh, 200, f"refresh tic for clear-backoff attempt {attempt}")

            interface = _get_interface(interface_id)
            if not interface.tak_tunnel_fallback_active:
                raise LivePanelClearBackoffFailure("Interface did not enter fallback before clear-backoff")
            _require_failure_state(
                pair_key,
                failure_count=TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT,
                manual_attention_required=True,
            )

            clear_backoff = client.post(
                "/admin/diagnostics/tak-tunnels/clear-backoff",
                data={
                    "focused_tic_server_id": str(tic_id),
                    "focused_tak_server_id": str(tak_id),
                },
                headers=headers,
            )
            _assert_status(clear_backoff, 200, "clear backoff through diagnostics")
            _assert_backoff_cleared_event(tic_id, tak_id)

            after_clear = _read_pair_state(pair_key)
            if after_clear:
                raise LivePanelClearBackoffFailure(
                    f"pair state should be cleared immediately after clear-backoff action, got: {after_clear}"
                )

            refresh_after_clear = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
            _assert_status(refresh_after_clear, 200, "refresh tic after clear-backoff reset")
            after_first_retry = _require_failure_state(pair_key, failure_count=1, manual_attention_required=False)
            cooldown_until = str(after_first_retry.get("cooldown_until") or "").strip()
            if not cooldown_until:
                raise LivePanelClearBackoffFailure(
                    f"cooldown_until missing after first retry following clear-backoff: {after_first_retry}"
                )

            refresh_cooldown = client.post(f"/api/admin/servers/{tic_id}/refresh", headers=headers)
            _assert_status(refresh_cooldown, 200, "refresh tic after clear-backoff during cooldown")
            after_cooldown_refresh = _require_failure_state(pair_key, failure_count=1, manual_attention_required=False)
            if after_cooldown_refresh.get("last_attempt_at") != after_first_retry.get("last_attempt_at"):
                raise LivePanelClearBackoffFailure("cooldown refresh unexpectedly performed another repair attempt")
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

    print("OK: live panel tak clear-backoff check passed")


if __name__ == "__main__":
    try:
        main()
    except LivePanelClearBackoffFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
