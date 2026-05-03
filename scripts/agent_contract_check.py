from __future__ import annotations

import json
import os
import re
import sys
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
from app.models import AppSetting, Interface, Peer, Server, ServerType, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class ContractFailure(RuntimeError):
    pass


def assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:500].replace("\n", " ")
        raise ContractFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def load_payloads(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_payload(payloads: list[dict[str, Any]], action: str) -> dict[str, Any]:
    for payload in reversed(payloads):
        if payload.get("action") == action:
            return payload
    raise ContractFailure(f"Fake agent did not receive action {action!r}")


def find_payload_for_interface(payloads: list[dict[str, Any]], action: str, interface_id: int) -> dict[str, Any]:
    for payload in reversed(payloads):
        if payload.get("action") == action and payload.get("interface", {}).get("id") == interface_id:
            return payload
    raise ContractFailure(f"Fake agent did not receive action {action!r} for interface {interface_id}")


def find_payload_for_peer(payloads: list[dict[str, Any]], action: str, peer_id: int) -> dict[str, Any]:
    for payload in reversed(payloads):
        if payload.get("action") == action and payload.get("peer", {}).get("id") == peer_id:
            return payload
    raise ContractFailure(f"Fake agent did not receive action {action!r} for peer {peer_id}")


def cleanup_contract_records() -> None:
    with SessionLocal() as db:
        interfaces = db.execute(select(Interface).where(Interface.name.like("contract-check-%"))).scalars().all()
        servers = db.execute(select(Server).where(Server.name.like("contract-check-%"))).scalars().all()
        for interface in interfaces:
            db.delete(interface)
        db.flush()
        for server in servers:
            db.delete(server)
        db.commit()


def prepare_data() -> tuple[int, int, int, int, int, int, dict[str, Any]]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)

        settings_to_force = {
            "block_filters_enabled": "1",
            "exclusion_filters_enabled": "1",
            "nelomai_git_repo": "",
        }
        original_settings: dict[str, str | None] = {}
        for key, value in settings_to_force.items():
            setting = db.get(AppSetting, key)
            original_settings[key] = setting.value if setting else None
            setting = setting or AppSetting(key=key, value=value)
            setting.value = value
            db.add(setting)

        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        user = db.execute(select(User).where(User.role == UserRole.USER).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise ContractFailure("No admin user found")
        if user is None:
            raise ContractFailure("No regular user found")

        interface = db.execute(
            select(Interface)
            .where(Interface.user_id == user.id, Interface.is_pending_owner.is_(False))
            .order_by(Interface.id.asc())
        ).scalars().first()
        if interface is None:
            raise ContractFailure("No regular-user interface found")

        peer = db.execute(
            select(Peer).where(Peer.interface_id == interface.id).order_by(Peer.id.asc())
        ).scalars().first()
        if peer is None:
            raise ContractFailure("No peer found for regular-user interface")

        contract_server = Server(
            name="contract-check-server 9z",
            server_type=ServerType.TIC,
            host="127.0.0.50",
            ssh_port=22,
            ssh_login="root",
            ssh_password="secret",
            is_excluded=False,
            is_active=False,
        )
        contract_tak_server = Server(
            name="contract-check-tak-server 9z",
            server_type=ServerType.TAK,
            host="127.0.0.51",
            ssh_port=22,
            ssh_login="root",
            ssh_password="secret",
            is_excluded=False,
            is_active=False,
        )
        db.add(contract_server)
        db.add(contract_tak_server)
        db.flush()

        original_state = {
            "settings": original_settings,
            "interface_exclusion_filters_enabled": interface.exclusion_filters_enabled,
            "peer_is_enabled": peer.is_enabled,
            "peer_block_filters_enabled": peer.block_filters_enabled,
            "peer_handshake_at": peer.handshake_at,
            "peer_traffic_7d_mb": peer.traffic_7d_mb,
            "peer_traffic_30d_mb": peer.traffic_30d_mb,
            "interface_peer_states": {item.id: item.is_enabled for item in interface.peers},
        }

        interface.exclusion_filters_enabled = True
        peer.block_filters_enabled = True
        db.add(interface)
        db.add(peer)
        db.commit()
        return admin.id, user.id, interface.id, peer.id, contract_server.id, contract_tak_server.id, original_state


def restore_data(interface_id: int, peer_id: int, original_state: dict[str, Any]) -> None:
    with SessionLocal() as db:
        for key, value in original_state["settings"].items():
            setting = db.get(AppSetting, key)
            if setting is None:
                continue
            if value is None:
                db.delete(setting)
            else:
                setting.value = value
                db.add(setting)

        interface = db.get(Interface, interface_id)
        if interface is not None:
            interface.exclusion_filters_enabled = original_state["interface_exclusion_filters_enabled"]
            for item in interface.peers:
                if item.id in original_state["interface_peer_states"]:
                    item.is_enabled = original_state["interface_peer_states"][item.id]
                    db.add(item)
            db.add(interface)

        peer = db.get(Peer, peer_id)
        if peer is not None:
            peer.is_enabled = original_state["peer_is_enabled"]
            peer.block_filters_enabled = original_state["peer_block_filters_enabled"]
            peer.handshake_at = original_state["peer_handshake_at"]
            peer.traffic_7d_mb = original_state["peer_traffic_7d_mb"]
            peer.traffic_30d_mb = original_state["peer_traffic_30d_mb"]
            db.add(peer)

        db.commit()


def load_user(user_id: int) -> User:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise ContractFailure(f"User {user_id} disappeared during contract check")
        db.expunge(user)
        return user


def assert_common_payload(payload: dict[str, Any], interface_id: int | None = None) -> None:
    if payload.get("contract_version") != "1.0":
        raise ContractFailure(f"Payload {payload.get('action')} has wrong contract_version")
    supported = payload.get("supported_contracts")
    if not isinstance(supported, list) or "1.0" not in supported:
        raise ContractFailure(f"Payload {payload.get('action')} has no supported contract list")
    if not payload.get("component"):
        raise ContractFailure(f"Payload {payload.get('action')} has no component")
    capabilities = payload.get("requested_capabilities")
    if not isinstance(capabilities, list):
        raise ContractFailure(f"Payload {payload.get('action')} has no requested capabilities list")
    if interface_id is None:
        return
    if payload.get("interface", {}).get("id") != interface_id:
        raise ContractFailure(f"Payload {payload.get('action')} has wrong interface id")
    server_identity = payload.get("interface", {}).get("server_identity")
    if not isinstance(server_identity, dict):
        raise ContractFailure(f"Payload {payload.get('action')} has no interface server_identity block")
    if server_identity.get("tic_server_id") is None:
        raise ContractFailure(f"Payload {payload.get('action')} has no tic_server_id in server_identity")
    if "agent_interface_id" not in server_identity:
        raise ContractFailure(f"Payload {payload.get('action')} has no agent_interface_id in server_identity")
    if "tic_server" not in payload:
        raise ContractFailure(f"Payload {payload.get('action')} has no tic_server block")
    if "exclusion_filters" not in payload:
        raise ContractFailure(f"Payload {payload.get('action')} has no exclusion_filters block")
    if "block_filters" not in payload:
        raise ContractFailure(f"Payload {payload.get('action')} has no block_filters block")


def assert_component_registry_split() -> None:
    constants_path = ROOT_DIR / "agents" / "node-tic-agent" / "src" / "constants.js"
    source = constants_path.read_text(encoding="utf-8")
    tic_only_actions = [
        "prepare_interface",
        "create_interface",
        "toggle_interface",
        "update_interface_route_mode",
        "update_interface_tak_server",
        "update_interface_exclusion_filters",
        "toggle_peer",
        "recreate_peer",
        "delete_peer",
        "download_peer_config",
        "download_interface_bundle",
        "update_peer_block_filters",
    ]
    for action in tic_only_actions:
        match = re.search(
            rf"{re.escape(action)}:\s*\{{\s*components:\s*\[(?P<components>[^\]]+)\]",
            source,
            re.MULTILINE,
        )
        if match is None:
            raise ContractFailure(f"{action} is missing in constants.js action registry")
        components = {item.strip().strip("\"'") for item in match.group("components").split(",") if item.strip()}
        if components != {"tic-agent"}:
            raise ContractFailure(f"{action} must be restricted to tic-agent, got {sorted(components)}")


def run() -> None:
    assert_component_registry_split()
    fake_agent = ROOT_DIR / "scripts" / "fake_peer_agent.py"
    cleanup_contract_records()
    admin_id, user_id, interface_id, peer_id, contract_server_id, contract_tak_server_id, original_state = prepare_data()
    admin = load_user(admin_id)
    user = load_user(user_id)
    previous_command = settings.peer_agent_command

    temp_root = ROOT_DIR / ".tmp"
    temp_root.mkdir(exist_ok=True)
    log_path = temp_root / "agent-contract-payloads.jsonl"
    if log_path.exists():
        log_path.unlink()
    os.environ["NELOMAI_FAKE_AGENT_LOG"] = str(log_path)
    os.environ["NELOMAI_FAKE_BOOTSTRAP_INPUT_REQUIRED"] = "1"
    settings.peer_agent_command = f'"{sys.executable}" "{fake_agent}"'

    try:
        with TestClient(app) as client:
            user_headers = auth_headers(user)
            admin_headers = auth_headers(admin)

            assert_status(
                client.post(
                    "/api/admin/interfaces/prepare",
                    json={"name": "contract-check-prepared", "tic_server_id": contract_server_id, "tak_server_id": contract_tak_server_id},
                    headers=admin_headers,
                ),
                200,
                "prepare interface",
            )
            create_response = client.post(
                "/api/admin/interfaces",
                json={
                    "name": "contract-check-created",
                    "tic_server_id": contract_server_id,
                    "tak_server_id": contract_tak_server_id,
                    "listen_port": 19992,
                    "address_v4": "10.199.92.1/24",
                    "peer_limit": 5,
                },
                headers=admin_headers,
            )
            assert_status(create_response, 201, "create interface")
            created_contract_interface_id = int(create_response.json()["id"])
            assert_status(client.post(f"/api/peers/{peer_id}/toggle", headers=user_headers), 200, "toggle peer")
            assert_status(client.post(f"/api/peers/{peer_id}/recreate", headers=user_headers), 200, "recreate peer")
            assert_status(client.get(f"/api/peers/{peer_id}/download", headers=user_headers), 200, "download peer")
            assert_status(client.get(f"/api/interfaces/{interface_id}/download-all", headers=user_headers), 200, "download interface bundle")
            assert_status(client.post(f"/api/admin/interfaces/{interface_id}/toggle", headers=admin_headers), 200, "toggle interface")
            assert_status(
                client.put(
                    f"/api/admin/interfaces/{interface_id}/route-mode",
                    json={"route_mode": "standalone"},
                    headers=admin_headers,
                ),
                200,
                "update route mode",
            )
            assert_status(
                client.put(
                    f"/api/admin/interfaces/{interface_id}/tak-server",
                    json={"tak_server_id": None},
                    headers=admin_headers,
                ),
                200,
                "update tak server",
            )
            assert_status(
                client.put(
                    f"/api/admin/peers/{peer_id}/block-filters",
                    json={"enabled": False},
                    headers=admin_headers,
                ),
                200,
                "update peer block filters",
            )
            assert_status(
                client.put(
                    f"/api/admin/interfaces/{interface_id}/exclusion-filters",
                    json={"enabled": False},
                    headers=admin_headers,
                ),
                200,
                "update interface exclusion filters",
            )
            assert_status(client.post(f"/api/admin/servers/{contract_server_id}/restart-agent", headers=admin_headers), 204, "restart server agent")
            assert_status(client.post(f"/api/admin/servers/{contract_server_id}/refresh", headers=admin_headers), 200, "verify server status")
            assert_status(client.post(f"/api/admin/servers/{contract_server_id}/reboot", headers=admin_headers), 204, "reboot server")
            assert_status(
                client.put(
                    "/api/admin/settings/updates",
                    json={
                        "nelomai_git_repo": "https://github.com/example/nelomai",
                    },
                    headers=admin_headers,
                ),
                200,
                "update Git settings",
            )
            bootstrap_response = client.post(
                "/api/admin/servers",
                json={
                    "server_type": "tic",
                    "tic_region": "europe",
                    "name": "contract-check-bootstrap",
                    "host": "127.0.0.52",
                    "ssh_port": 22,
                    "ssh_login": "root",
                    "ssh_password": "secret",
                },
                headers=admin_headers,
            )
            assert_status(bootstrap_response, 201, "bootstrap server starts with input request")
            bootstrap_task = bootstrap_response.json()
            if bootstrap_task.get("status") != "input_required":
                raise ContractFailure("bootstrap_server should return input_required in fake interactive scenario")
            if bootstrap_task.get("input_key") != "install_confirm" or bootstrap_task.get("input_kind") != "confirm":
                raise ContractFailure("bootstrap input request does not expose expected key/kind")
            bootstrap_task_id = int(bootstrap_task["id"])
            bootstrap_input_response = client.post(
                f"/api/admin/server-bootstrap/{bootstrap_task_id}/input",
                json={"value": "yes"},
                headers=admin_headers,
            )
            assert_status(bootstrap_input_response, 200, "bootstrap server accepts input")
            completed_bootstrap = bootstrap_input_response.json()
            if completed_bootstrap.get("status") != "completed" or not completed_bootstrap.get("server_id"):
                raise ContractFailure("bootstrap input did not complete and create server record")
            assert_status(client.get("/api/admin/agent-updates/check", headers=admin_headers), 200, "check server agent update")
            assert_status(
                client.post(
                    "/api/admin/agent-updates/apply",
                    json={"server_id": None},
                    headers=admin_headers,
                ),
                200,
                "apply server agent update",
            )
            full_backup_response = client.post("/api/admin/backups", json={"backup_type": "full"}, headers=admin_headers)
            assert_status(full_backup_response, 201, "create full backup for server snapshot contract")
            full_backup_id = int(full_backup_response.json()["id"])
            assert_status(
                client.post("/api/admin/backups/latest-full/verify-server-copies", headers=admin_headers),
                200,
                "verify latest full backup server copies",
            )
            assert_status(
                client.post("/api/admin/backups/server-copies/cleanup", headers=admin_headers),
                200,
                "cleanup server backup copies",
            )
            assert_status(client.delete(f"/api/admin/backups/{full_backup_id}", headers=admin_headers), 204, "delete full backup")

        payloads = load_payloads(log_path)
        expected_actions = [
            "prepare_interface",
            "create_interface",
            "toggle_peer",
            "recreate_peer",
            "download_peer_config",
            "download_interface_bundle",
            "toggle_interface",
            "update_interface_route_mode",
            "update_interface_tak_server",
            "update_peer_block_filters",
            "update_interface_exclusion_filters",
            "restart_server_agent",
            "verify_server_status",
            "reboot_server",
            "bootstrap_server",
            "bootstrap_server_input",
            "check_server_agent_update",
            "update_server_agent",
            "cleanup_server_backups",
        ]
        for action in expected_actions:
            payload = (
                find_payload_for_interface(payloads, action, interface_id)
                if action
                not in {
                    "prepare_interface",
                    "create_interface",
                    "restart_server_agent",
                    "verify_server_status",
                    "reboot_server",
                    "bootstrap_server",
                    "bootstrap_server_input",
                    "check_server_agent_update",
                    "update_server_agent",
                    "cleanup_server_backups",
                }
                else find_payload(payloads, action)
            )
            assert_common_payload(
                payload,
                None
                if action in {
                    "prepare_interface",
                    "create_interface",
                    "restart_server_agent",
                    "verify_server_status",
                    "reboot_server",
                    "bootstrap_server",
                    "bootstrap_server_input",
                    "check_server_agent_update",
                    "update_server_agent",
                    "cleanup_server_backups",
                }
                else interface_id,
            )

        peer_payload = find_payload_for_peer(payloads, "toggle_peer", peer_id)
        if peer_payload.get("peer", {}).get("id") != peer_id:
            raise ContractFailure("toggle_peer payload has wrong peer id")
        if peer_payload.get("block_filters", {}).get("enabled") is not True:
            raise ContractFailure("toggle_peer payload should start with block filters enabled")

        peer_block_payload = find_payload_for_peer(payloads, "update_peer_block_filters", peer_id)
        if peer_block_payload.get("peer", {}).get("id") != peer_id:
            raise ContractFailure("update_peer_block_filters payload has wrong peer id")
        if peer_block_payload.get("block_filters", {}).get("enabled") is not False:
            raise ContractFailure("update_peer_block_filters payload must send effective disabled state")
        if peer_block_payload.get("target_state", {}).get("block_filters_enabled") is not False:
            raise ContractFailure("update_peer_block_filters payload has wrong target_state")

        interface_exclusion_payload = find_payload(payloads, "update_interface_exclusion_filters")
        if interface_exclusion_payload.get("exclusion_filters", {}).get("enabled") is not False:
            raise ContractFailure("update_interface_exclusion_filters payload must send effective disabled state")
        if interface_exclusion_payload.get("target_state", {}).get("exclusion_filters_enabled") is not False:
            raise ContractFailure("update_interface_exclusion_filters payload has wrong target_state")

        prepare_payload = find_payload(payloads, "prepare_interface")
        if "peer_limit" in prepare_payload or "peer_limit" in prepare_payload.get("interface", {}):
            raise ContractFailure("prepare_interface must not send peer_limit to Tic agent")
        if prepare_payload.get("interface", {}).get("name") != "contract-check-prepared":
            raise ContractFailure("prepare_interface payload has wrong interface name")

        create_payload = find_payload(payloads, "create_interface")
        if "peer_limit" in create_payload or "peer_limit" in create_payload.get("interface", {}):
            raise ContractFailure("create_interface must not send peer_limit to Tic agent")
        create_identity = create_payload.get("interface", {}).get("server_identity")
        if not isinstance(create_identity, dict) or create_identity.get("tic_server_id") != contract_server_id:
            raise ContractFailure("create_interface payload has wrong server_identity")
        if create_payload.get("interface", {}).get("listen_port") != 19992:
            raise ContractFailure("create_interface payload has wrong listen_port")
        if create_payload.get("interface", {}).get("address_v4") != "10.199.92.1/24":
            raise ContractFailure("create_interface payload has wrong address_v4")
        with SessionLocal() as db:
            created_interface = db.get(Interface, created_contract_interface_id)
            if created_interface is not None and created_interface.agent_interface_id is None:
                raise ContractFailure("created interface did not persist agent_interface_id")
        provision_tunnel_payload = find_payload(payloads, "provision_tak_tunnel")
        if provision_tunnel_payload.get("server", {}).get("id") != contract_tak_server_id:
            raise ContractFailure("provision_tak_tunnel must target Tak contract server")
        attach_tunnel_payload = find_payload(payloads, "attach_tak_tunnel")
        if attach_tunnel_payload.get("server", {}).get("id") != contract_server_id:
            raise ContractFailure("attach_tak_tunnel must target Tic contract server")
        if not isinstance(attach_tunnel_payload.get("tunnel_artifacts"), dict):
            raise ContractFailure("attach_tak_tunnel payload must include tunnel_artifacts")
        if "amnezia_config" in attach_tunnel_payload:
            raise ContractFailure("attach_tak_tunnel payload must not include legacy amnezia_config")

        toggle_interface_payload = find_payload(payloads, "toggle_interface")
        if "is_enabled" not in toggle_interface_payload.get("target_state", {}):
            raise ContractFailure("toggle_interface payload must include target_state.is_enabled")

        route_payload = find_payload(payloads, "update_interface_route_mode")
        if route_payload.get("target_state", {}).get("route_mode") != "standalone":
            raise ContractFailure("update_interface_route_mode payload has wrong target route_mode")

        tak_payload = find_payload(payloads, "update_interface_tak_server")
        target_state = tak_payload.get("target_state", {})
        if target_state.get("tak_server_id") is not None or target_state.get("route_mode") != "standalone":
            raise ContractFailure("update_interface_tak_server payload has wrong target_state")

        for action in ["restart_server_agent", "verify_server_status", "reboot_server"]:
            payload = find_payload(payloads, action)
            server = payload.get("server", {})
            if server.get("id") != contract_server_id:
                raise ContractFailure(f"{action} payload has wrong server id")
            if "ssh_password" not in server:
                raise ContractFailure(f"{action} payload must include SSH credentials for Node-agent bridge")
            expected_component = "server-agent" if action in {"bootstrap_server", "bootstrap_server_input"} else "tic-agent"
            if payload.get("component") != expected_component:
                raise ContractFailure(f"{action} payload must target {expected_component} component")

        bootstrap_start_payload = next(
            (
                payload for payload in payloads
                if payload.get("action") == "bootstrap_server"
                and payload.get("server", {}).get("name") == "contract-check-bootstrap"
            ),
            None,
        )
        if bootstrap_start_payload is None:
            raise ContractFailure("bootstrap_server payload for interactive server was not sent")
        assert_common_payload(bootstrap_start_payload)
        if bootstrap_start_payload.get("component") != "server-agent":
            raise ContractFailure("bootstrap_server payload must target server-agent component")
        if "agent.bootstrap.v1" not in bootstrap_start_payload.get("requested_capabilities", []):
            raise ContractFailure("bootstrap_server payload must request agent.bootstrap.v1")
        if bootstrap_start_payload.get("repository_url") != "https://github.com/example/nelomai":
            raise ContractFailure("bootstrap_server payload must include monorepo Git repository")
        if bootstrap_start_payload.get("os_family") != "ubuntu" or bootstrap_start_payload.get("os_version") != "22.04":
            raise ContractFailure("bootstrap_server payload must include Ubuntu 22.04 hints")

        bootstrap_input_payload = find_payload(payloads, "bootstrap_server_input")
        assert_common_payload(bootstrap_input_payload)
        input_payload = bootstrap_input_payload.get("input")
        if not isinstance(input_payload, dict):
            raise ContractFailure("bootstrap_server_input payload must include input block")
        if input_payload.get("key") != "install_confirm" or input_payload.get("kind") != "confirm" or input_payload.get("value") != "yes":
            raise ContractFailure("bootstrap_server_input payload has wrong input block")

        for action in ["check_server_agent_update", "update_server_agent"]:
            tic_payload = next(
                (
                    payload
                    for payload in payloads
                    if payload.get("action") == action
                    and payload.get("server", {}).get("id") == contract_server_id
                ),
                None,
            )
            if tic_payload is None:
                raise ContractFailure(f"{action} must be sent to Tic contract server")
            if tic_payload.get("component") != "tic-agent":
                raise ContractFailure(f"{action} Tic payload must target tic-agent component")
            if tic_payload.get("repository_url") != "https://github.com/example/nelomai":
                raise ContractFailure(f"{action} Tic payload must include monorepo Git repository")
            tak_payload = next(
                (
                    payload
                    for payload in payloads
                    if payload.get("action") == action
                    and payload.get("server", {}).get("id") == contract_tak_server_id
                ),
                None,
            )
            if tak_payload is None:
                raise ContractFailure(f"{action} must be sent to Tak contract server")
            if tak_payload.get("component") != "tak-agent":
                raise ContractFailure(f"{action} Tak payload must target tak-agent component")
            if tak_payload.get("repository_url") != "https://github.com/example/nelomai":
                raise ContractFailure(f"{action} Tak payload must include monorepo Git repository")

        bootstrap_payload = find_payload(payloads, "create_interface")
        if "interface.create.v1" not in bootstrap_payload.get("requested_capabilities", []):
            raise ContractFailure("create_interface payload must request interface.create.v1 capability")

        server_backup_payload = find_payload(payloads, "create_server_backup")
        backup_policy = server_backup_payload.get("backup_policy")
        if not isinstance(backup_policy, dict):
            raise ContractFailure("create_server_backup payload must include backup_policy")
        if backup_policy.get("fresh_retention_days") != 90 or backup_policy.get("fresh_size_limit_mb") != 5120:
            raise ContractFailure("create_server_backup payload has wrong fresh backup policy")
        if backup_policy.get("monthly_retention_days") != 365 or backup_policy.get("monthly_size_limit_mb") != 3072:
            raise ContractFailure("create_server_backup payload has wrong monthly backup policy")
        verify_backup_payload = find_payload(payloads, "verify_server_backup_copy")
        snapshot = verify_backup_payload.get("snapshot")
        if not isinstance(snapshot, dict) or not snapshot.get("sha256") or not snapshot.get("size_bytes"):
            raise ContractFailure("verify_server_backup_copy payload must include snapshot size and sha256")
        cleanup_backup_payload = find_payload(payloads, "cleanup_server_backups")
        if cleanup_backup_payload.get("keep_latest_count") != 1:
            raise ContractFailure("cleanup_server_backups payload must keep one latest server backup")
        if cleanup_backup_payload.get("component") not in {"tic-agent", "tak-agent"}:
            raise ContractFailure("cleanup_server_backups payload must target Tic/Tak agent")

        print("OK: agent contract check passed")
    finally:
        settings.peer_agent_command = previous_command
        os.environ.pop("NELOMAI_FAKE_AGENT_LOG", None)
        os.environ.pop("NELOMAI_FAKE_BOOTSTRAP_INPUT_REQUIRED", None)
        restore_data(interface_id, peer_id, original_state)
        cleanup_contract_records()
        if log_path.exists():
            log_path.unlink()


if __name__ == "__main__":
    try:
        run()
    except ContractFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
