"use strict";

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
  const endpointLabel = interfaceRecord.tak_server_id == null ? "Russia" : "Not-Russia";
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

module.exports = {
  peerFileName,
  peerLinuxFileName,
  renderInterfaceConfig,
  renderPeerConfig
};
