"use strict";

module.exports = function fakeAmneziaToolModule(payload) {
  const tunnel = payload && typeof payload.tunnel === "object" ? payload.tunnel : {};
  const servers = payload && typeof payload.servers === "object" ? payload.servers : {};
  const provisional = payload && typeof payload.provisional_keys === "object" ? payload.provisional_keys : {};
  const provisionalAwg = payload && typeof payload.provisional_awg_parameters === "object" ? payload.provisional_awg_parameters : {};

  return {
    amnezia_config: {
      protocol: "amneziawg-2.0",
      version: "2.0",
      source: "official-tooling",
      tunnel_id: tunnel.tunnel_id,
      endpoint: {
        host: ((servers.tak || {}).host || ""),
        port: tunnel.listen_port,
      },
      addressing: {
        network_cidr: tunnel.network_cidr,
        tak_address_v4: tunnel.tak_address_v4,
        tic_address_v4: tunnel.tic_address_v4,
        allowed_ips: [tunnel.network_cidr].filter(Boolean),
      },
      keys: {
        client_private_key: provisional.client_private_key,
        client_public_key: provisional.client_public_key,
        server_public_key: provisional.server_public_key,
      },
      awg_parameters: provisionalAwg,
      nat_mode: tunnel.nat_mode,
      canonical_artifacts: {
        server_config_text: "# official fake server config",
        client_config_text: "# official fake client config",
      },
    },
  };
};
