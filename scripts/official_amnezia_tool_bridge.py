from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


class BridgeFailure(RuntimeError):
    pass


def _string(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _candidate_quick_command() -> str | None:
    for candidate in ("awg-quick", "amneziawg-quick", "wg-quick"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _render_server_config(payload: dict[str, object]) -> str:
    tunnel = payload.get("tunnel") or {}
    provisional = payload.get("provisional_keys") or {}
    awg = payload.get("provisional_awg_parameters") or {}
    headers = awg.get("header_obfuscation") or {}
    session = awg.get("session_noise") or {}

    lines = [
        "# Official AmneziaWG bridge generated server config",
        "[Interface]",
        f"ListenPort = {_int(tunnel.get('listen_port'))}",
        f"Address = {_string(tunnel.get('tak_address_v4'))}",
        f"PrivateKey = {_string(provisional.get('server_private_key'))}",
        f"H1 = {_string(headers.get('H1'), '0')}",
        f"H2 = {_string(headers.get('H2'), '0')}",
        f"H3 = {_string(headers.get('H3'), '0')}",
        f"H4 = {_string(headers.get('H4'), '0')}",
        f"S1 = {_int(session.get('S1'))}",
        f"S2 = {_int(session.get('S2'))}",
        f"S3 = {_int(session.get('S3'))}",
        f"S4 = {_int(session.get('S4'))}",
        "",
        "[Peer]",
        f"PublicKey = {_string(provisional.get('client_public_key'))}",
        f"AllowedIPs = {_string(tunnel.get('tic_address_v4'))}",
        "PersistentKeepalive = 21",
        "",
    ]
    return "\n".join(lines)


def _render_client_config(payload: dict[str, object]) -> str:
    tunnel = payload.get("tunnel") or {}
    servers = payload.get("servers") or {}
    tak = servers.get("tak") or {}
    provisional = payload.get("provisional_keys") or {}
    awg = payload.get("provisional_awg_parameters") or {}
    headers = awg.get("header_obfuscation") or {}
    session = awg.get("session_noise") or {}
    init_noise = awg.get("init_noise") or {}
    junk = awg.get("junk_packets") or {}

    lines = [
        "# Official AmneziaWG bridge generated client config",
        "[Interface]",
        f"Address = {_string(tunnel.get('tic_address_v4'))}",
        f"PrivateKey = {_string(provisional.get('client_private_key'))}",
        f"Jc = {_int(junk.get('Jc'))}",
        f"Jmin = {_int(junk.get('Jmin'))}",
        f"Jmax = {_int(junk.get('Jmax'))}",
        f"H1 = {_string(headers.get('H1'), '0')}",
        f"H2 = {_string(headers.get('H2'), '0')}",
        f"H3 = {_string(headers.get('H3'), '0')}",
        f"H4 = {_string(headers.get('H4'), '0')}",
        f"S1 = {_int(session.get('S1'))}",
        f"S2 = {_int(session.get('S2'))}",
        f"S3 = {_int(session.get('S3'))}",
        f"S4 = {_int(session.get('S4'))}",
        f"I1 = {_string(init_noise.get('I1'))}",
        f"I2 = {_string(init_noise.get('I2'))}",
        f"I3 = {_string(init_noise.get('I3'))}",
        f"I4 = {_string(init_noise.get('I4'))}",
        f"I5 = {_string(init_noise.get('I5'))}",
        "",
        "[Peer]",
        f"PublicKey = {_string(provisional.get('server_public_key'))}",
        f"AllowedIPs = {_string(tunnel.get('network_cidr'))}",
        f"Endpoint = {_string(tak.get('host'))}:{_int(tunnel.get('listen_port'))}",
        "PersistentKeepalive = 21",
        "",
    ]
    return "\n".join(lines)


def _validate_config_text(config_text: str, *, name: str) -> None:
    quick_command = _candidate_quick_command()
    if not quick_command:
        raise BridgeFailure("Official AmneziaWG quick command is not installed")
    with tempfile.TemporaryDirectory(prefix="nelomai-awg-bridge-") as temp_dir:
        temp_path = Path(temp_dir) / f"{name}.conf"
        temp_path.write_text(config_text, encoding="utf-8")
        completed = subprocess.run(
            [quick_command, "strip", str(temp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "awg-quick strip failed").strip()
            raise BridgeFailure(detail)


def main() -> None:
    payload = json.loads(input() or "{}")
    tunnel = payload.get("tunnel") or {}
    servers = payload.get("servers") or {}
    provisional = payload.get("provisional_keys") or {}
    awg = payload.get("provisional_awg_parameters") or {}

    server_config = _render_server_config(payload)
    client_config = _render_client_config(payload)
    _validate_config_text(server_config, name="awgtaksrv")
    _validate_config_text(client_config, name="awgtakcli")

    print(
        json.dumps(
            {
                "amnezia_config": {
                    "protocol": _string(payload.get("protocol"), "amneziawg-2.0"),
                    "version": "2.0",
                    "source": "official-tooling",
                    "tunnel_id": _string(tunnel.get("tunnel_id")),
                    "endpoint": {
                        "host": _string((servers.get("tak") or {}).get("host")),
                        "port": _int(tunnel.get("listen_port")),
                    },
                    "addressing": {
                        "network_cidr": _string(tunnel.get("network_cidr")),
                        "tak_address_v4": _string(tunnel.get("tak_address_v4")),
                        "tic_address_v4": _string(tunnel.get("tic_address_v4")),
                        "allowed_ips": [_string(tunnel.get("network_cidr"))],
                    },
                    "keys": {
                        "client_private_key": _string(provisional.get("client_private_key")),
                        "client_public_key": _string(provisional.get("client_public_key")),
                        "server_public_key": _string(provisional.get("server_public_key")),
                    },
                    "awg_parameters": awg,
                    "nat_mode": _string(tunnel.get("nat_mode"), "masquerade"),
                    "canonical_artifacts": {
                        "server_config_text": server_config,
                        "client_config_text": client_config,
                    },
                }
            }
        )
    )


if __name__ == "__main__":
    main()
