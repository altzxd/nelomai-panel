from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    tunnel = payload.get("tunnel") or {}
    servers = payload.get("servers") or {}
    provisional = payload.get("provisional_keys") or {}
    provisional_awg = payload.get("provisional_awg_parameters") or {}
    print(
        json.dumps(
            {
                "amnezia_config": {
                    "protocol": "amneziawg-2.0",
                    "version": "2.0",
                    "source": "official-tooling",
                    "tunnel_id": tunnel.get("tunnel_id"),
                    "endpoint": {
                        "host": ((servers.get("tak") or {}).get("host") or ""),
                        "port": tunnel.get("listen_port"),
                    },
                    "addressing": {
                        "network_cidr": tunnel.get("network_cidr"),
                        "tak_address_v4": tunnel.get("tak_address_v4"),
                        "tic_address_v4": tunnel.get("tic_address_v4"),
                        "allowed_ips": [tunnel.get("network_cidr")],
                    },
                    "keys": {
                        "client_private_key": provisional.get("client_private_key"),
                        "client_public_key": provisional.get("client_public_key"),
                        "server_public_key": provisional.get("server_public_key"),
                    },
                    "awg_parameters": provisional_awg,
                    "nat_mode": tunnel.get("nat_mode"),
                    "canonical_artifacts": {
                        "server_config_text": "# official fake server config",
                        "client_config_text": "# official fake client config",
                    },
                }
            }
        )
    )


if __name__ == "__main__":
    main()
