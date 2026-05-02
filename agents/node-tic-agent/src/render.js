"use strict";

const crypto = require("node:crypto");

function configAddress(addressV4) {
  return String(addressV4 || "").trim();
}

function peerTunnelAddress(peerRecord) {
  if (peerRecord && typeof peerRecord.address_v4 === "string" && peerRecord.address_v4.trim()) {
    return peerRecord.address_v4.trim();
  }
  const slot = Number(peerRecord && peerRecord.slot);
  if (!Number.isInteger(slot) || slot <= 0) {
    return "10.200.255.2/32";
  }
  return `10.200.${Math.min(slot, 254)}.2/32`;
}

function peerAllowedIps(peerRecord) {
  return peerTunnelAddress(peerRecord);
}

function endpointHost(interfaceRecord) {
  if (String(interfaceRecord.route_mode || "").trim() === "via_tak" && interfaceRecord.tak_server_host) {
    return String(interfaceRecord.tak_server_host).trim();
  }
  if (interfaceRecord.tic_server_host) {
    return String(interfaceRecord.tic_server_host).trim();
  }
  return `${String(interfaceRecord.tic_server_id || "0")}.example.invalid`;
}

function placeholderKey(parts) {
  const normalized = parts
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(":");
  const encoded = Buffer.from(normalized, "utf8").toString("base64").replace(/=+$/g, "");
  return `${encoded}${"A".repeat(Math.max(0, 44 - encoded.length))}`.slice(0, 44);
}

function randomBase64(size = 32) {
  return crypto.randomBytes(size).toString("base64");
}

function nonEmptyAwgInitNoiseLines(initNoise) {
  return ["I1", "I2", "I3", "I4", "I5"]
    .map((key) => {
      const value = String(initNoise?.[key] || "");
      return value ? `${key} = ${value}` : "";
    })
    .filter(Boolean);
}

function resolvedKey(value, fallbackParts) {
  const preferred = String(value || "").trim();
  if (preferred) {
    return preferred;
  }
  return placeholderKey(fallbackParts);
}

function peerFileName(peerRecord) {
  return `${peerRecord.slot}.conf`;
}

function peerLinuxFileName(interfaceRecord, peerRecord) {
  return `${interfaceRecord.agent_interface_id}-${peerRecord.slot}.conf`;
}

function peerRevision(peerRecord) {
  const revision = Number(peerRecord && peerRecord.config_revision);
  return Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function renderInterfaceConfig(interfaceRecord, peerRecords) {
  const peers = Array.isArray(peerRecords)
    ? [...peerRecords]
        .filter((peerRecord) => peerRecord && peerRecord.is_enabled !== false)
        .sort((a, b) => Number(a.slot) - Number(b.slot))
    : [];
  const peerSections = peers.flatMap((peerRecord) => [
    "[Peer]",
    `# Peer slot: ${peerRecord.slot}`,
    `# Peer revision: ${peerRevision(peerRecord)}`,
    `# Peer artifact: peers/${peerFileName(peerRecord)}`,
    `PublicKey = ${resolvedKey(peerRecord.public_key, ["peer-public", interfaceRecord.agent_interface_id, peerRecord.slot, peerRevision(peerRecord)])}`,
    `AllowedIPs = ${peerAllowedIps(peerRecord)}`,
    ""
  ]);
  return [
    "# Nelomai generated WireGuard interface config",
    "[Interface]",
    `# Interface: ${String(interfaceRecord.name || "interface")}`,
    `# Agent interface id: ${String(interfaceRecord.agent_interface_id || "")}`,
    `Address = ${configAddress(interfaceRecord.address_v4)}`,
    `ListenPort = ${interfaceRecord.listen_port}`,
    `PrivateKey = ${resolvedKey(interfaceRecord.private_key, ["interface", interfaceRecord.agent_interface_id, interfaceRecord.name])}`,
    "SaveConfig = false",
    "",
    ...peerSections,
    ""
  ].join("\n");
}

function renderPeerConfig(interfaceRecord, peerRecord) {
  const interfaceName = String(interfaceRecord.name || "interface");
  const comment = peerRecord.comment ? `# ${peerRecord.comment}\n` : "";
  const routeMode = String(interfaceRecord.route_mode || "standalone");
  const endpointLabel = routeMode === "via_tak" ? "Not-Russia" : "Russia";
  return [
    `${comment}# Nelomai generated WireGuard peer config`,
    `[Interface]`,
    `# Interface: ${interfaceName}`,
    `# Peer slot: ${peerRecord.slot}`,
    `# Peer revision: ${peerRevision(peerRecord)}`,
    `# Agent interface id: ${interfaceRecord.agent_interface_id}`,
    `Address = ${peerTunnelAddress(peerRecord)}`,
    `DNS = 8.8.8.8`,
    `MTU = 1280`,
    `PrivateKey = ${resolvedKey(peerRecord.private_key, ["peer", interfaceRecord.agent_interface_id, peerRecord.slot, peerRevision(peerRecord)])}`,
    ``,
    `[Peer]`,
    `# Route mode: ${routeMode}`,
    `# Exit: ${endpointLabel}`,
    `PublicKey = ${resolvedKey(interfaceRecord.public_key, ["server", interfaceRecord.agent_interface_id, interfaceRecord.name])}`,
    `AllowedIPs = 0.0.0.0/0`,
    `Endpoint = ${endpointHost(interfaceRecord)}:${interfaceRecord.listen_port}`,
    `PersistentKeepalive = 21`,
    ``
  ].join("\n");
}

function renderTakTunnelServerConfig(tunnelRecord) {
  const header = tunnelRecord.awg_headers || {};
  const sessionNoise = tunnelRecord.awg_session_noise || {};
  const initNoise = tunnelRecord.awg_init_noise || {};
  const initNoiseLines = nonEmptyAwgInitNoiseLines(initNoise);
  return [
    "# Nelomai generated inter-server tunnel config",
    `# Protocol target: ${String(tunnelRecord.protocol || "amneziawg-2.0")}`,
    `# TunnelId: ${String(tunnelRecord.tunnel_id || "")}`,
    `[Interface]`,
    `ListenPort = ${Number(tunnelRecord.listen_port) || 0}`,
    `Address = ${String(tunnelRecord.tak_address_v4 || "")}`,
    `PrivateKey = ${String(tunnelRecord.server_private_key || "")}`,
    `H1 = ${String(header.H1 || "0")}`,
    `H2 = ${String(header.H2 || "0")}`,
    `H3 = ${String(header.H3 || "0")}`,
    `H4 = ${String(header.H4 || "0")}`,
    `S1 = ${Number(sessionNoise.S1) || 0}`,
    `S2 = ${Number(sessionNoise.S2) || 0}`,
    `S3 = ${Number(sessionNoise.S3) || 0}`,
    `S4 = ${Number(sessionNoise.S4) || 0}`,
    ...initNoiseLines,
    `# NatMode = ${String(tunnelRecord.nat_mode || "masquerade")}`,
    "",
    `[Peer]`,
    `# Role = tic-client`,
    `AllowedIPs = ${String(tunnelRecord.tic_address_v4 || "")}`,
    `PublicKey = ${String(tunnelRecord.client_public_key || "")}`,
    `PersistentKeepalive = 21`,
    ""
  ].join("\n");
}

function renderTicTunnelClientConfig(tunnelRecord) {
  const header = tunnelRecord.awg_headers || {};
  const sessionNoise = tunnelRecord.awg_session_noise || {};
  const initNoise = tunnelRecord.awg_init_noise || {};
  const junk = tunnelRecord.awg_junk || {};
  const initNoiseLines = nonEmptyAwgInitNoiseLines(initNoise);
  return [
    "# Nelomai generated inter-server tunnel config",
    `# Protocol target: ${String(tunnelRecord.protocol || "amneziawg-2.0")}`,
    `# TunnelId: ${String(tunnelRecord.tunnel_id || "")}`,
    `[Interface]`,
    `Address = ${String(tunnelRecord.tic_address_v4 || "")}`,
    `PrivateKey = ${String(tunnelRecord.client_private_key || "")}`,
    `Jc = ${Number(junk.Jc) || 0}`,
    `Jmin = ${Number(junk.Jmin) || 0}`,
    `Jmax = ${Number(junk.Jmax) || 0}`,
    `H1 = ${String(header.H1 || "0")}`,
    `H2 = ${String(header.H2 || "0")}`,
    `H3 = ${String(header.H3 || "0")}`,
    `H4 = ${String(header.H4 || "0")}`,
    `S1 = ${Number(sessionNoise.S1) || 0}`,
    `S2 = ${Number(sessionNoise.S2) || 0}`,
    `S3 = ${Number(sessionNoise.S3) || 0}`,
    `S4 = ${Number(sessionNoise.S4) || 0}`,
    ...initNoiseLines,
    `# NatMode = ${String(tunnelRecord.nat_mode || "masquerade")}`,
    "",
    `[Peer]`,
    `# Role = tak-server`,
    `AllowedIPs = ${String(tunnelRecord.network_cidr || tunnelRecord.tak_address_v4 || "")}`,
    `Endpoint = ${String(tunnelRecord.tak_server_host || "")}:${Number(tunnelRecord.listen_port) || 0}`,
    `PublicKey = ${String(tunnelRecord.server_public_key || "")}`,
    `PersistentKeepalive = 21`,
    ""
  ].join("\n");
}

function buildTakTunnelClientPayload(tunnelRecord) {
  return {
    protocol: String(tunnelRecord.protocol || "amneziawg-2.0"),
    tunnel_id: String(tunnelRecord.tunnel_id || ""),
    version: "2.0",
    endpoint: {
      host: String(tunnelRecord.tak_server_host || ""),
      port: Number(tunnelRecord.listen_port) || 0
    },
    addressing: {
      network_cidr: String(tunnelRecord.network_cidr || ""),
      tak_address_v4: String(tunnelRecord.tak_address_v4 || ""),
      tic_address_v4: String(tunnelRecord.tic_address_v4 || ""),
      allowed_ips: [String(tunnelRecord.network_cidr || "")].filter(Boolean)
    },
    keys: {
      client_private_key: String(tunnelRecord.client_private_key || ""),
      client_public_key: String(tunnelRecord.client_public_key || ""),
      server_public_key: String(tunnelRecord.server_public_key || "")
    },
    awg_parameters: {
      header_obfuscation: {
        H1: String(tunnelRecord.awg_headers?.H1 || "0"),
        H2: String(tunnelRecord.awg_headers?.H2 || "0"),
        H3: String(tunnelRecord.awg_headers?.H3 || "0"),
        H4: String(tunnelRecord.awg_headers?.H4 || "0")
      },
      session_noise: {
        S1: Number(tunnelRecord.awg_session_noise?.S1) || 0,
        S2: Number(tunnelRecord.awg_session_noise?.S2) || 0,
        S3: Number(tunnelRecord.awg_session_noise?.S3) || 0,
        S4: Number(tunnelRecord.awg_session_noise?.S4) || 0
      },
      init_noise: {
        I1: String(tunnelRecord.awg_init_noise?.I1 || ""),
        I2: String(tunnelRecord.awg_init_noise?.I2 || ""),
        I3: String(tunnelRecord.awg_init_noise?.I3 || ""),
        I4: String(tunnelRecord.awg_init_noise?.I4 || ""),
        I5: String(tunnelRecord.awg_init_noise?.I5 || "")
      },
      junk_packets: {
        Jc: Number(tunnelRecord.awg_junk?.Jc) || 0,
        Jmin: Number(tunnelRecord.awg_junk?.Jmin) || 0,
        Jmax: Number(tunnelRecord.awg_junk?.Jmax) || 0
      },
    },
    nat_mode: String(tunnelRecord.nat_mode || "masquerade"),
    generated_at: String(tunnelRecord.updated_at || tunnelRecord.created_at || ""),
  };
}

module.exports = {
  randomBase64,
  peerFileName,
  peerLinuxFileName,
  renderInterfaceConfig,
  renderPeerConfig,
  renderTakTunnelServerConfig,
  renderTicTunnelClientConfig,
  buildTakTunnelClientPayload
};
