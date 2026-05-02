from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path


CONTRACT_RESPONSE = {
    "contract_version": "1.0",
    "supported_contracts": ["1.0"],
    "agent_version": "0.1.0",
    "capabilities": [
        "agent.bootstrap.v1",
        "agent.lifecycle.v1",
        "agent.status.v1",
        "agent.update.v1",
        "filters.block.v1",
        "filters.exclusion.v1",
        "interface.create.v1",
        "interface.route_mode.v1",
        "interface.state.v1",
        "interface.tak_server.v1",
        "peer.delete.v1",
        "peer.download.v1",
        "peer.download_bundle.v1",
        "peer.recreate.v1",
        "peer.state.v1",
        "tunnel.tak.attach.v1",
        "tunnel.tak.detach.v1",
        "tunnel.tak.provision.v1",
        "tunnel.tak.status.v1",
    ],
}


def response(payload: dict[str, object]) -> str:
    return json.dumps({**CONTRACT_RESPONSE, **payload})


def main() -> None:
    raw_payload = sys.stdin.read()
    try:
        payload = json.loads(raw_payload or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "invalid json"}))
        raise SystemExit(1)

    log_path = os.environ.get("NELOMAI_FAKE_AGENT_LOG")
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    action = str(payload.get("action") or "")
    if action == "prepare_interface":
        print(response({"ok": True, "listen_port": 19991, "address_v4": "10.199.91.1/24"}))
        return

    if action == "create_interface":
        interface = payload.get("interface") if isinstance(payload.get("interface"), dict) else {}
        name = str(interface.get("name") or "interface")
        print(response({"ok": True, "agent_interface_id": f"fake-{name}"}))
        return

    if action == "verify_server_status":
        print(response({"ok": True, "is_active": True}))
        return

    if action == "check_server_agent_update":
        print(
            response(
                {
                    "ok": True,
                    "status": "checked",
                    "current_version": "0.1.0",
                    "latest_version": "0.1.1",
                    "update_available": True,
                    "message": "fake update is available",
                }
            )
        )
        return

    if action == "update_server_agent":
        print(
            response(
                {
                    "ok": True,
                    "status": "updated",
                    "current_version": "0.1.1",
                    "latest_version": "0.1.1",
                    "update_available": False,
                    "message": "fake agent updated",
                }
            )
        )
        return

    if action == "provision_tak_tunnel":
        print(
            response(
                {
                    "ok": True,
                    "status": "provisioned",
                    "tunnel_id": "fake-tak-tunnel-1",
                    "protocol": "amneziawg-2.0",
                    "listen_port": 51831,
                    "network_cidr": "172.27.10.0/30",
                    "tak_address_v4": "172.27.10.1/30",
                    "tic_address_v4": "172.27.10.2/30",
                    "nat_mode": "masquerade",
                    "tunnel_artifacts": {
                        "format": "amneziawg-2.0",
                        "version": "2.0",
                        "source": "fake-agent",
                        "tunnel_id": "fake-tak-tunnel-1",
                        "endpoint": {"host": "127.0.0.51", "port": 51831},
                        "addressing": {
                            "network_cidr": "172.27.10.0/30",
                            "tak_address_v4": "172.27.10.1/30",
                            "tic_address_v4": "172.27.10.2/30",
                            "allowed_ips": ["172.27.10.0/30"],
                        },
                        "keys": {
                            "client_private_key": "fake-client-private-key",
                            "client_public_key": "fake-client-public-key",
                            "server_public_key": "fake-server-public-key",
                        },
                        "awg_parameters": {
                            "header_obfuscation": {"H1": 11, "H2": 22, "H3": 33, "H4": 44},
                            "session_noise": {"S1": 55, "S2": 66, "S3": 77, "S4": 88},
                            "init_noise": {"I1": 99, "I2": 111, "I3": 122, "I4": 133, "I5": 144},
                            "junk_packets": {"Jc": 3, "Jmin": 64, "Jmax": 96},
                        },
                        "runtime_artifacts": {
                            "server_config_text": "# fake server config",
                            "client_config_text": "# fake client config",
                        },
                    },
                    "amnezia_config": {
                        "interface_name": "amz-fake-1",
                        "protocol": "amneziawg-2.0",
                        "version": "2.0",
                        "endpoint": {"host": "127.0.0.51", "port": 51831},
                        "addressing": {
                            "network_cidr": "172.27.10.0/30",
                            "tak_address_v4": "172.27.10.1/30",
                            "tic_address_v4": "172.27.10.2/30",
                            "allowed_ips": ["172.27.10.0/30"],
                        },
                        "keys": {
                            "client_private_key": "fake-client-private-key",
                            "client_public_key": "fake-client-public-key",
                            "server_public_key": "fake-server-public-key",
                        },
                        "awg_parameters": {
                            "jitter_seed": "fake-seed",
                            "header_obfuscation": {"H1": 11, "H2": 22, "H3": 33, "H4": 44},
                            "session_noise": {"S1": 55, "S2": 66, "S3": 77, "S4": 88},
                            "init_noise": {"I1": 99, "I2": 111, "I3": 122, "I4": 133, "I5": 144},
                        },
                    },
                }
            )
        )
        return

    if action == "attach_tak_tunnel":
        print(response({"ok": True, "status": "attached", "tunnel_id": "fake-tak-tunnel-1"}))
        return

    if action == "verify_tak_tunnel_status":
        print(
            response(
                {
                    "ok": True,
                    "status": "checked",
                    "tunnel_status": {
                        "tunnel_id": "fake-tak-tunnel-1",
                        "status": "active",
                        "is_active": True,
                    },
                }
            )
        )
        return

    if action == "detach_tak_tunnel":
        print(
            response(
                {
                    "ok": True,
                    "status": "detached",
                    "tunnel_status": {
                        "tunnel_id": "fake-tak-tunnel-1",
                        "status": "detached",
                        "is_active": False,
                    },
                }
            )
        )
        return

    if action == "bootstrap_server" and os.environ.get("NELOMAI_FAKE_BOOTSTRAP_INPUT_REQUIRED") == "1":
        server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
        if str(server.get("name") or "") == "contract-check-bootstrap":
            print(
                response(
                    {
                        "ok": True,
                        "status": "input_required",
                        "logs": ["fake bootstrap connected", "fake bootstrap needs confirmation"],
                        "input_prompt": "Confirm agent install",
                        "input_key": "install_confirm",
                        "input_kind": "confirm",
                    }
                )
            )
            return

    if action == "bootstrap_server_input":
        input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        if input_payload.get("key") == "install_confirm":
            print(response({"ok": True, "status": "completed", "logs": ["fake confirmation accepted", "fake bootstrap completed"]}))
            return

    if action in {"bootstrap_server", "bootstrap_server_status", "bootstrap_server_input"}:
        print(response({"ok": True, "status": "completed", "logs": ["fake bootstrap ok"]}))
        return

    if action == "download_peer_config":
        content = "# fake WireGuard peer config\n[Interface]\nPrivateKey = fake\n"
        print(
            response(
                {
                    "ok": True,
                    "filename": "fake-peer.conf",
                    "content_type": "text/plain; charset=utf-8",
                    "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                }
            )
        )
        return

    if action == "download_interface_bundle":
        content = b"PK\x03\x04fake zip placeholder"
        print(
            response(
                {
                    "ok": True,
                    "filename": "fake-interface.zip",
                    "content_type": "application/zip",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        )
        return

    if action == "create_server_backup":
        content = b"PK\x03\x04fake server snapshot"
        print(
            response(
                {
                    "ok": True,
                    "filename": "fake-server-snapshot.zip",
                    "content_type": "application/zip",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        )
        return

    if action == "verify_server_backup_copy":
        print(response({"ok": True, "matches": True, "message": "fake snapshot matches"}))
        return

    if action == "cleanup_server_backups":
        print(response({"ok": True, "deleted_count": 1, "message": "fake server backups cleaned"}))
        return

    print(response({"ok": True}))


if __name__ == "__main__":
    main()
