"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const childProcess = require("node:child_process");

const {
  peerFileName,
  peerLinuxFileName,
  renderInterfaceConfig,
  renderPeerConfig,
  renderTakTunnelServerConfig,
  renderTicTunnelClientConfig,
  buildTakTunnelClientPayload
} = require("./render");

function runtimeRoot() {
  return process.env.NELOMAI_AGENT_RUNTIME_ROOT || path.join(__dirname, "..", ".runtime");
}

function ensureDir(targetPath) {
  fs.mkdirSync(targetPath, { recursive: true });
}

function commandExists(command) {
  const completed = childProcess.spawnSync("bash", ["-lc", `command -v ${command}`], {
    encoding: "utf8"
  });
  return completed.status === 0;
}

function readTextFileSafe(targetPath) {
  try {
    if (!fs.existsSync(targetPath) || !fs.statSync(targetPath).isFile()) {
      return "";
    }
    return fs.readFileSync(targetPath, "utf8");
  } catch {
    return "";
  }
}

function roundMetric(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return null;
  }
  const factor = 10 ** digits;
  return Math.round(numeric * factor) / factor;
}

function readMemoryMetrics() {
  const meminfo = readTextFileSafe("/proc/meminfo");
  const totalMatch = meminfo.match(/^MemTotal:\s+(\d+)\s+kB$/m);
  const availableMatch = meminfo.match(/^MemAvailable:\s+(\d+)\s+kB$/m);
  const totalKb = Number(totalMatch && totalMatch[1]) || 0;
  const availableKb = Number(availableMatch && availableMatch[1]) || 0;
  if (totalKb <= 0) {
    return {
      ram_percent: null,
      ram_total_gb: null,
      ram_used_gb: null
    };
  }
  const usedKb = Math.max(0, totalKb - availableKb);
  return {
    ram_percent: roundMetric((usedKb / totalKb) * 100),
    ram_total_gb: roundMetric(totalKb / 1024 / 1024, 2),
    ram_used_gb: roundMetric(usedKb / 1024 / 1024, 2)
  };
}

function readDiskMetrics(targetPath = "/") {
  const completed = childProcess.spawnSync("df", ["-kP", targetPath], {
    encoding: "utf8"
  });
  if (completed.status !== 0) {
    return {
      disk_total_gb: null,
      disk_used_gb: null,
      disk_percent: null
    };
  }
  const lines = String(completed.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2) {
    return {
      disk_total_gb: null,
      disk_used_gb: null,
      disk_percent: null
    };
  }
  const parts = lines[1].split(/\s+/);
  const totalKb = Number(parts[1]) || 0;
  const usedKb = Number(parts[2]) || 0;
  const percentRaw = String(parts[4] || "").replace("%", "");
  const percent = Number(percentRaw);
  return {
    disk_total_gb: totalKb > 0 ? roundMetric(totalKb / 1024 / 1024, 2) : null,
    disk_used_gb: totalKb > 0 ? roundMetric(usedKb / 1024 / 1024, 2) : null,
    disk_percent: Number.isFinite(percent) ? roundMetric(percent) : null
  };
}

function readUptimeMetrics() {
  const raw = readTextFileSafe("/proc/uptime").trim();
  const [uptimeText] = raw.split(/\s+/, 1);
  const uptimeSeconds = Number(uptimeText);
  if (!Number.isFinite(uptimeSeconds) || uptimeSeconds < 0) {
    return {
      uptime_seconds: null
    };
  }
  return {
    uptime_seconds: Math.floor(uptimeSeconds)
  };
}

function readLoadMetrics() {
  const loads = os.loadavg();
  return {
    load_1m: roundMetric(loads[0], 2),
    load_5m: roundMetric(loads[1], 2),
    load_15m: roundMetric(loads[2], 2)
  };
}

function readCpuPercent() {
  const cpuCount = Math.max(1, os.cpus().length || 1);
  const [load1] = os.loadavg();
  if (!Number.isFinite(load1)) {
    return null;
  }
  return roundMetric(Math.min(100, (load1 / cpuCount) * 100));
}

function defaultNetworkInterface() {
  const completed = childProcess.spawnSync("bash", ["-lc", "ip route show default 2>/dev/null | awk 'NR==1 {print $5}'"], {
    encoding: "utf8"
  });
  if (completed.status !== 0) {
    return "";
  }
  return String(completed.stdout || "").trim();
}

function readNetworkMetrics() {
  const iface = defaultNetworkInterface();
  if (!iface) {
    return {
      network_interface: null,
      network_rx_bytes: null,
      network_tx_bytes: null
    };
  }
  const rx = Number(readTextFileSafe(`/sys/class/net/${iface}/statistics/rx_bytes`).trim());
  const tx = Number(readTextFileSafe(`/sys/class/net/${iface}/statistics/tx_bytes`).trim());
  return {
    network_interface: iface,
    network_rx_bytes: Number.isFinite(rx) ? rx : null,
    network_tx_bytes: Number.isFinite(tx) ? tx : null
  };
}

function inspectSystemMetrics() {
  const memory = readMemoryMetrics();
  const disk = readDiskMetrics("/");
  const uptime = readUptimeMetrics();
  const load = readLoadMetrics();
  const network = readNetworkMetrics();
  return {
    cpu_percent: readCpuPercent(),
    ...memory,
    ...disk,
    ...uptime,
    ...load,
    ...network
  };
}

function interfaceDirectory(interfaceRecord) {
  return path.join(runtimeRoot(), "interfaces", String(interfaceRecord.agent_interface_id || interfaceRecord.name || "interface"));
}

function interfaceConfigPath(interfaceRecord) {
  return path.join(interfaceDirectory(interfaceRecord), "wg0.conf");
}

function interfaceMetaPath(interfaceRecord) {
  return path.join(interfaceDirectory(interfaceRecord), "interface.json");
}

function peersDirectory(interfaceRecord) {
  return path.join(interfaceDirectory(interfaceRecord), "peers");
}

function peerConfigPath(interfaceRecord, peerRecord) {
  return path.join(peersDirectory(interfaceRecord), peerFileName(peerRecord));
}

function writeTextFile(targetPath, content) {
  ensureDir(path.dirname(targetPath));
  fs.writeFileSync(targetPath, content, "utf8");
}

function writeJsonFile(targetPath, value) {
  writeTextFile(targetPath, JSON.stringify(value, null, 2));
}

function tunnelsRoot() {
  return path.join(runtimeRoot(), "tunnels");
}

function tunnelDirectory(tunnelRecord) {
  return path.join(tunnelsRoot(), String(tunnelRecord.tunnel_id || "tunnel"));
}

function tunnelMetaPath(tunnelRecord) {
  return path.join(tunnelDirectory(tunnelRecord), "tunnel.json");
}

function tunnelServerConfigPath(tunnelRecord) {
  return path.join(tunnelDirectory(tunnelRecord), "server.amneziawg.conf");
}

function tunnelClientConfigPath(tunnelRecord) {
  return path.join(tunnelDirectory(tunnelRecord), "client.amneziawg.conf");
}

function tunnelClientPayloadPath(tunnelRecord) {
  return path.join(tunnelDirectory(tunnelRecord), "client-payload.json");
}

function systemTunnelRoot() {
  return process.env.NELOMAI_AGENT_SYSTEM_TUNNEL_ROOT || "/etc/wireguard";
}

function systemTunnelQuickConfigRoot() {
  const explicit = String(process.env.NELOMAI_AGENT_SYSTEM_TUNNEL_QUICK_ROOT || "").trim();
  if (explicit) {
    return explicit;
  }
  return "/etc/amnezia/amneziawg";
}

function systemTunnelName(tunnelRecord) {
  const sequence = String(Number(tunnelRecord.sequence) || 0).padStart(5, "0");
  const ticTail = String(Number(tunnelRecord.tic_server_id) || 0).slice(-3).padStart(3, "0");
  const takTail = String(Number(tunnelRecord.tak_server_id) || 0).slice(-3).padStart(3, "0");
  return `awg${sequence}${ticTail}${takTail}`;
}

function systemTunnelConfigPath(tunnelRecord) {
  return path.join(systemTunnelRoot(), `${systemTunnelName(tunnelRecord)}.conf`);
}

function systemTunnelQuickConfigPath(tunnelRecord) {
  return path.join(systemTunnelQuickConfigRoot(), `${systemTunnelName(tunnelRecord)}.conf`);
}

function stripCidr(address) {
  const raw = String(address || "").trim();
  if (!raw) {
    return "";
  }
  const [host] = raw.split("/", 1);
  return host || "";
}

function ipv4NetworkCidr(address) {
  const raw = String(address || "").trim();
  if (!raw.includes("/")) {
    return "";
  }
  const [host, prefixText] = raw.split("/", 2);
  const octets = host.split(".").map((value) => Number(value));
  const prefix = Number(prefixText);
  if (octets.length !== 4 || octets.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) {
    return "";
  }
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32) {
    return "";
  }
  const ip =
    ((octets[0] << 24) >>> 0) |
    ((octets[1] << 16) >>> 0) |
    ((octets[2] << 8) >>> 0) |
    (octets[3] >>> 0);
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const network = ip & mask;
  const parts = [
    (network >>> 24) & 0xff,
    (network >>> 16) & 0xff,
    (network >>> 8) & 0xff,
    network & 0xff,
  ];
  return `${parts.join(".")}/${prefix}`;
}

function interfaceRouteTableId(interfaceRecord) {
  const candidates = [
    Number(String(interfaceRecord.agent_interface_id || "").replace(/\D+/g, "").slice(-4)),
    Number(interfaceRecord.panel_interface_id),
    Number(interfaceRecord.listen_port),
  ];
  const suffix = candidates.find((value) => Number.isInteger(value) && value > 0) || 1;
  return 20000 + Math.min(suffix, 9999);
}

function interfaceRouteRulePriority(interfaceRecord) {
  return 10000 + Math.min(interfaceRouteTableId(interfaceRecord), 19999);
}

function interfaceRouteMark(interfaceRecord) {
  return interfaceRouteTableId(interfaceRecord);
}

function readTunnelRecord(tunnelDirectoryPath) {
  const metaPath = path.join(tunnelDirectoryPath, "tunnel.json");
  if (!fs.existsSync(metaPath) || !fs.statSync(metaPath).isFile()) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(metaPath, "utf8"));
  } catch {
    return null;
  }
}

function findTunnelRecordForInterface(interfaceRecord) {
  const ticServerId = Number(interfaceRecord.tic_server_id);
  const takServerId = Number(interfaceRecord.tak_server_id);
  if (!Number.isInteger(ticServerId) || ticServerId <= 0 || !Number.isInteger(takServerId) || takServerId <= 0) {
    return null;
  }
  const root = tunnelsRoot();
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    return null;
  }
  const entries = fs.readdirSync(root, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const record = readTunnelRecord(path.join(root, entry.name));
    if (!record || typeof record !== "object") {
      continue;
    }
    if (Number(record.tic_server_id) !== ticServerId || Number(record.tak_server_id) !== takServerId) {
      continue;
    }
    return record;
  }
  return null;
}

function buildInterfaceForwardingCommands(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  return [
    "sysctl -w net.ipv4.ip_forward=1 >/dev/null",
    `iptables -C FORWARD -i ${interfaceName} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ${interfaceName} -j ACCEPT`,
    `iptables -C FORWARD -o ${interfaceName} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o ${interfaceName} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT`,
  ];
}

function buildStandaloneNetworkingCommands(interfaceRecord) {
  const subnet = ipv4NetworkCidr(interfaceRecord.address_v4);
  if (!subnet) {
    return [];
  }
  const tableId = interfaceRouteTableId(interfaceRecord);
  const rulePriority = interfaceRouteRulePriority(interfaceRecord);
  const mark = interfaceRouteMark(interfaceRecord);
  const interfaceName = systemInterfaceName(interfaceRecord);
  return [
    ...buildInterfaceForwardingCommands(interfaceRecord),
    `DEFAULT_IF=$(ip route show default | awk 'NR==1 {print $5}'); [ -n "$DEFAULT_IF" ] && (iptables -t nat -C POSTROUTING -s ${subnet} -o "$DEFAULT_IF" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${subnet} -o "$DEFAULT_IF" -j MASQUERADE)`,
    `iptables -t mangle -D PREROUTING -i ${interfaceName} -j MARK --set-mark ${mark} 2>/dev/null || true`,
    `ip rule del fwmark ${mark} table ${tableId} priority ${rulePriority} 2>/dev/null || true`,
    `ip rule del from ${subnet} table ${tableId} 2>/dev/null || true`,
    `ip route flush table ${tableId} 2>/dev/null || true`,
  ];
}

function buildViaTakNetworkingCommands(interfaceRecord) {
  const subnet = ipv4NetworkCidr(interfaceRecord.address_v4);
  const tunnelRecord = findTunnelRecordForInterface(interfaceRecord);
  if (!subnet || !tunnelRecord) {
    return buildInterfaceForwardingCommands(interfaceRecord);
  }
  const tableId = interfaceRouteTableId(interfaceRecord);
  const rulePriority = interfaceRouteRulePriority(interfaceRecord);
  const mark = interfaceRouteMark(interfaceRecord);
  const tunnelName = systemTunnelName(tunnelRecord);
  const interfaceName = systemInterfaceName(interfaceRecord);
  const takGateway = stripCidr(tunnelRecord.tak_address_v4);
  return [
    ...buildInterfaceForwardingCommands(interfaceRecord),
    `iptables -C FORWARD -o ${tunnelName} -j ACCEPT 2>/dev/null || iptables -A FORWARD -o ${tunnelName} -j ACCEPT`,
    `iptables -C FORWARD -i ${tunnelName} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ${tunnelName} -j ACCEPT`,
    `DEFAULT_IF=$(ip route show default | awk 'NR==1 {print $5}'); [ -n "$DEFAULT_IF" ] && iptables -t nat -D POSTROUTING -s ${subnet} -o "$DEFAULT_IF" -j MASQUERADE 2>/dev/null || true`,
    `iptables -t nat -C POSTROUTING -s ${subnet} -o ${tunnelName} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${subnet} -o ${tunnelName} -j MASQUERADE`,
    `iptables -t mangle -C PREROUTING -i ${interfaceName} -j MARK --set-mark ${mark} 2>/dev/null || iptables -t mangle -A PREROUTING -i ${interfaceName} -j MARK --set-mark ${mark}`,
    `ip route replace table ${tableId} default via ${takGateway} dev ${tunnelName}`,
    `ip rule add fwmark ${mark} table ${tableId} priority ${rulePriority} 2>/dev/null || true`,
    `ip rule add from ${subnet} table ${tableId} priority ${rulePriority} 2>/dev/null || true`,
  ];
}

function buildInterfaceNetworkingCommands(interfaceRecord) {
  const routeMode = String(interfaceRecord.route_mode || "standalone").trim();
  if (routeMode === "via_tak" && Number(interfaceRecord.tak_server_id) > 0) {
    return buildViaTakNetworkingCommands(interfaceRecord);
  }
  return buildStandaloneNetworkingCommands(interfaceRecord);
}

function buildTunnelServerNetworkingCommands(tunnelRecord) {
  const tunnelName = systemTunnelName(tunnelRecord);
  const tunnelNetwork = String(tunnelRecord.network_cidr || "").trim();
  if (!tunnelNetwork) {
    return [];
  }
  return [
    "sysctl -w net.ipv4.ip_forward=1 >/dev/null",
    `iptables -C FORWARD -i ${tunnelName} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ${tunnelName} -j ACCEPT`,
    `iptables -C FORWARD -o ${tunnelName} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o ${tunnelName} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT`,
    `DEFAULT_IF=$(ip route show default | awk 'NR==1 {print $5}'); [ -n "$DEFAULT_IF" ] && (iptables -t nat -C POSTROUTING -s ${tunnelNetwork} -o "$DEFAULT_IF" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${tunnelNetwork} -o "$DEFAULT_IF" -j MASQUERADE)`,
  ];
}

function resolveTunnelQuickCommand() {
  const explicit = String(process.env.NELOMAI_AGENT_TUNNEL_QUICK_CMD || "").trim();
  if (explicit) {
    return explicit;
  }
  const candidates = ["awg-quick", "amneziawg-quick", "wg-quick"];
  return candidates.find((candidate) => commandExists(candidate)) || null;
}

function resolveTunnelUserspaceImplementation() {
  const scoped = String(process.env.NELOMAI_AGENT_TUNNEL_USERSPACE_IMPLEMENTATION || "").trim();
  if (scoped) {
    return scoped;
  }
  const legacy = String(process.env.WG_QUICK_USERSPACE_IMPLEMENTATION || "").trim();
  if (legacy) {
    return legacy;
  }
  return "";
}

function generateWireGuardKeyPair() {
  const privateCompleted = childProcess.spawnSync("wg", ["genkey"], {
    encoding: "utf8"
  });
  if (privateCompleted.status !== 0) {
    throw new Error((privateCompleted.stderr || privateCompleted.stdout || "wg genkey failed").trim());
  }
  const privateKey = String(privateCompleted.stdout || "").trim();
  if (!privateKey) {
    throw new Error("wg genkey returned empty private key");
  }
  const publicCompleted = childProcess.spawnSync("wg", ["pubkey"], {
    input: `${privateKey}\n`,
    encoding: "utf8"
  });
  if (publicCompleted.status !== 0) {
    throw new Error((publicCompleted.stderr || publicCompleted.stdout || "wg pubkey failed").trim());
  }
  const publicKey = String(publicCompleted.stdout || "").trim();
  if (!publicKey) {
    throw new Error("wg pubkey returned empty public key");
  }
  return {
    private_key: privateKey,
    public_key: publicKey
  };
}

function syncInterfaceArtifacts(interfaceRecord) {
  const directory = interfaceDirectory(interfaceRecord);
  ensureDir(directory);
  ensureDir(peersDirectory(interfaceRecord));
  const peerRecords = Array.isArray(interfaceRecord.peers) ? interfaceRecord.peers : [];
  writeTextFile(interfaceConfigPath(interfaceRecord), renderInterfaceConfig(interfaceRecord, peerRecords));
  writeJsonFile(interfaceMetaPath(interfaceRecord), interfaceRecord);
}

function syncPeerArtifacts(interfaceRecord, peerRecord) {
  syncInterfaceArtifacts(interfaceRecord);
  writeTextFile(peerConfigPath(interfaceRecord, peerRecord), renderPeerConfig(interfaceRecord, peerRecord));
}

function syncAllPeerArtifacts(interfaceRecord) {
  syncInterfaceArtifacts(interfaceRecord);
  const peerRecords = Array.isArray(interfaceRecord.peers) ? interfaceRecord.peers : [];
  for (const peerRecord of peerRecords) {
    if (!peerRecord || typeof peerRecord !== "object") {
      continue;
    }
    writeTextFile(peerConfigPath(interfaceRecord, peerRecord), renderPeerConfig(interfaceRecord, peerRecord));
  }
}

function removePeerArtifacts(interfaceRecord, peerRecord) {
  const peerPath = peerConfigPath(interfaceRecord, peerRecord);
  if (fs.existsSync(peerPath) && fs.statSync(peerPath).isFile()) {
    fs.unlinkSync(peerPath);
  }
  syncInterfaceArtifacts(interfaceRecord);
}

function syncTunnelArtifacts(tunnelRecord) {
  const directory = tunnelDirectory(tunnelRecord);
  ensureDir(directory);
  writeJsonFile(tunnelMetaPath(tunnelRecord), tunnelRecord);
  const localRole = String(tunnelRecord.local_role || "").trim().toLowerCase();
  if (localRole === "tic") {
    writeTextFile(
      tunnelClientConfigPath(tunnelRecord),
      String(tunnelRecord.client_config_text || "").trim() || renderTicTunnelClientConfig(tunnelRecord)
    );
  } else {
    writeTextFile(
      tunnelServerConfigPath(tunnelRecord),
      String(tunnelRecord.server_config_text || "").trim() || renderTakTunnelServerConfig(tunnelRecord)
    );
  }
  writeJsonFile(tunnelClientPayloadPath(tunnelRecord), buildTakTunnelClientPayload(tunnelRecord));
}

function inspectTunnelArtifacts(tunnelRecord) {
  const directory = tunnelDirectory(tunnelRecord);
  const metaPath = tunnelMetaPath(tunnelRecord);
  const serverConfigPath = tunnelServerConfigPath(tunnelRecord);
  const clientConfigPath = tunnelClientConfigPath(tunnelRecord);
  const clientPayloadPath = tunnelClientPayloadPath(tunnelRecord);
  const systemConfigPath = systemTunnelConfigPath(tunnelRecord);
  const systemInterfaceName = systemTunnelName(tunnelRecord);
  return {
    runtime_dir: directory,
    meta_path: metaPath,
    server_config_path: serverConfigPath,
    client_config_path: clientConfigPath,
    client_payload_path: clientPayloadPath,
    system_config_path: systemConfigPath,
    system_interface_name: systemInterfaceName,
    runtime_dir_exists: fs.existsSync(directory) && fs.statSync(directory).isDirectory(),
    meta_exists: fs.existsSync(metaPath) && fs.statSync(metaPath).isFile(),
    server_config_exists: fs.existsSync(serverConfigPath) && fs.statSync(serverConfigPath).isFile(),
    client_config_exists: fs.existsSync(clientConfigPath) && fs.statSync(clientConfigPath).isFile(),
    client_payload_exists: fs.existsSync(clientPayloadPath) && fs.statSync(clientPayloadPath).isFile(),
    system_config_exists: Boolean(systemConfigPath) && fs.existsSync(systemConfigPath) && fs.statSync(systemConfigPath).isFile(),
    system_interface_exists: Boolean(systemInterfaceName) && childProcess.spawnSync("bash", ["-lc", `ip link show dev ${systemInterfaceName}`], {
      encoding: "utf8"
    }).status === 0
  };
}

function removeTunnelArtifacts(tunnelRecord) {
  const directory = tunnelDirectory(tunnelRecord);
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return;
  }
  fs.rmSync(directory, { recursive: true, force: true });
}

function removeInterfaceArtifacts(interfaceRecord) {
  const directory = interfaceDirectory(interfaceRecord);
  if (fs.existsSync(directory) && fs.statSync(directory).isDirectory()) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function collectInterfaceBundleEntries(interfaceRecord) {
  const directory = interfaceDirectory(interfaceRecord);
  const entries = [];
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return entries;
  }
  const walk = (baseDir, relativePrefix = "") => {
    for (const fileName of fs.readdirSync(baseDir).sort()) {
      const fullPath = path.join(baseDir, fileName);
      const relativeName = relativePrefix ? `${relativePrefix}/${fileName}` : fileName;
      const stat = fs.statSync(fullPath);
      if (stat.isDirectory()) {
        walk(fullPath, relativeName);
        continue;
      }
      if (!stat.isFile()) {
        continue;
      }
      entries.push({
        name: relativeName,
        content: fs.readFileSync(fullPath)
      });
    }
  };
  walk(directory);
  return entries;
}

function executionMode() {
  return (process.env.NELOMAI_AGENT_EXEC_MODE || "filesystem").trim().toLowerCase();
}

function systemWireGuardRoot() {
  return process.env.NELOMAI_AGENT_SYSTEM_WG_ROOT || "/etc/wireguard";
}

function systemPeersRoot() {
  return path.join(systemWireGuardRoot(), "peers");
}

function systemInterfaceName(interfaceRecord) {
  const preferred = String(interfaceRecord.agent_interface_id || interfaceRecord.name || "interface").trim();
  if (/^[A-Za-z0-9_=+.-]{1,15}$/.test(preferred)) {
    return preferred;
  }
  const ticTail = String(Number(interfaceRecord.tic_server_id) || 0).slice(-3).padStart(3, "0");
  const sequenceMatch = preferred.match(/-(\d+)$/);
  const sequence = sequenceMatch ? String(sequenceMatch[1]).padStart(5, "0").slice(-5) : "00000";
  return `wg${ticTail}${sequence}`;
}

function systemInterfaceConfigPath(interfaceRecord) {
  return path.join(systemWireGuardRoot(), `${systemInterfaceName(interfaceRecord)}.conf`);
}

function systemInterfacePeersRoot(interfaceRecord) {
  return path.join(systemPeersRoot(), systemInterfaceName(interfaceRecord));
}

function systemPeerConfigPath(interfaceRecord, peerRecord) {
  return path.join(systemInterfacePeersRoot(interfaceRecord), peerFileName(peerRecord));
}

function ensureSystemEnvironment() {
  if (executionMode() !== "system") {
    return {
      checked: false,
      mode: executionMode(),
      wireguard_root: systemWireGuardRoot(),
      peers_root: systemPeersRoot()
    };
  }
  if (process.platform !== "linux") {
    throw new Error("System execution mode is supported only on Linux");
  }
  if (!commandExists("bash")) {
    throw new Error("System execution mode requires bash in PATH");
  }
  if (!commandExists("ip")) {
    throw new Error("System execution mode requires ip command in PATH");
  }
  if (!commandExists("wg")) {
    throw new Error("System execution mode requires wg command in PATH");
  }
  if (!commandExists("wg-quick")) {
    throw new Error("System execution mode requires wg-quick command in PATH");
  }
  ensureDir(runtimeRoot());
  ensureDir(systemWireGuardRoot());
  ensureDir(systemPeersRoot());
  return {
    checked: true,
    mode: executionMode(),
    wireguard_root: systemWireGuardRoot(),
    peers_root: systemPeersRoot()
  };
}

function ensureWireGuardKeyMaterial(interfaceRecord, options = {}) {
  if (!commandExists("wg")) {
    return false;
  }
  if (executionMode() === "system") {
    ensureSystemEnvironment();
  }
  let changed = false;
  const rotatePeerSlots = new Set(
    Array.isArray(options.rotate_peer_slots)
      ? options.rotate_peer_slots
          .map((value) => Number(value))
          .filter((value) => Number.isInteger(value) && value > 0)
      : []
  );
  if (!String(interfaceRecord.private_key || "").trim() || !String(interfaceRecord.public_key || "").trim()) {
    Object.assign(interfaceRecord, generateWireGuardKeyPair());
    changed = true;
  }
  const peers = Array.isArray(interfaceRecord.peers) ? interfaceRecord.peers : [];
  for (const peerRecord of peers) {
    if (!peerRecord || typeof peerRecord !== "object") {
      continue;
    }
    const slot = Number(peerRecord.slot);
    const shouldRotate = rotatePeerSlots.has(slot);
    const missingKeys = !String(peerRecord.private_key || "").trim() || !String(peerRecord.public_key || "").trim();
    if (shouldRotate || missingKeys) {
      Object.assign(peerRecord, generateWireGuardKeyPair());
      changed = true;
    }
  }
  return changed;
}

function inspectRuntimeEnvironment() {
  const mode = executionMode();
  const runtime_root = runtimeRoot();
  const wireguard_root = systemWireGuardRoot();
  const peers_root = systemPeersRoot();
  const linux = process.platform === "linux";
  const bash = linux ? commandExists("bash") : false;
  const ip = linux ? commandExists("ip") : false;
  const wg = linux ? commandExists("wg") : false;

  ensureDir(runtime_root);
  const runtime_writable = fs.existsSync(runtime_root) && fs.statSync(runtime_root).isDirectory();

  const checks = [
    {
      key: "platform_linux",
      ok: linux,
      message: linux ? "Linux platform detected" : "Linux platform is required for system mode"
    },
    {
      key: "bash",
      ok: mode !== "system" ? true : bash,
      message: mode !== "system" ? "bash check skipped in filesystem mode" : bash ? "bash command found" : "bash command is missing"
    },
    {
      key: "ip",
      ok: mode !== "system" ? true : ip,
      message: mode !== "system" ? "ip check skipped in filesystem mode" : ip ? "ip command found" : "ip command is missing"
    },
    {
      key: "wg",
      ok: mode !== "system" ? true : wg,
      message: mode !== "system" ? "wg check skipped in filesystem mode" : wg ? "wg command found" : "wg command is missing"
    },
    {
      key: "wg_quick",
      ok: mode !== "system" ? true : commandExists("wg-quick"),
      message: mode !== "system" ? "wg-quick check skipped in filesystem mode" : commandExists("wg-quick") ? "wg-quick command found" : "wg-quick command is missing"
    },
    {
      key: "runtime_root",
      ok: runtime_writable,
      message: runtime_writable ? "Runtime root is writable" : "Runtime root is not writable"
    },
    {
      key: "wireguard_root",
      ok: mode !== "system" ? true : wireguard_root.length > 0,
      message: mode !== "system" ? "WireGuard root check skipped in filesystem mode" : `WireGuard root: ${wireguard_root}`
    },
    {
      key: "peers_root",
      ok: mode !== "system" ? true : peers_root.length > 0,
      message: mode !== "system" ? "Peers root check skipped in filesystem mode" : `Peers root: ${peers_root}`
    }
  ];

  const live_interfaces = mode === "system" && linux && wg ? inspectLiveInterfaces() : [];
  const metrics = linux ? inspectSystemMetrics() : null;

  return {
    mode,
    runtime_root,
    wireguard_root,
    peers_root,
    ready: checks.every((item) => item.ok),
    checks,
    live_interfaces,
    metrics
  };
}

function parseWgDump(output) {
  const lines = String(output || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) {
    return null;
  }
  const [interfaceLine, ...peerLines] = lines;
  const interfaceParts = interfaceLine.split(/\t+/);
  const interfaceData = {
    private_key: interfaceParts[0] || "",
    public_key: interfaceParts[1] || "",
    listen_port: Number(interfaceParts[2]) || 0,
    fwmark: interfaceParts[3] || "",
    peers: []
  };
  for (const line of peerLines) {
    const parts = line.split(/\t+/);
    if (!parts.length || !parts[0]) {
      continue;
    }
    const latestHandshake = Number(parts[4]) || 0;
    interfaceData.peers.push({
      public_key: parts[0] || "",
      preshared_key: parts[1] || "",
      endpoint: parts[2] || "",
      allowed_ips: parts[3] || "",
      latest_handshake_unix: latestHandshake,
      latest_handshake_at: latestHandshake > 0 ? new Date(latestHandshake * 1000).toISOString() : null,
      transfer_rx_bytes: Number(parts[5]) || 0,
      transfer_tx_bytes: Number(parts[6]) || 0,
      persistent_keepalive: Number(parts[7]) || 0
    });
  }
  return interfaceData;
}

function inspectLiveInterface(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const statusCompleted = childProcess.spawnSync("bash", ["-lc", `ip link show dev ${interfaceName}`], {
    encoding: "utf8"
  });
  const peerRecords = Array.isArray(interfaceRecord.peers) ? interfaceRecord.peers : [];
  const peerByPublicKey = new Map();
  for (const peerRecord of peerRecords) {
    const key = String(peerRecord && peerRecord.public_key || "").trim();
    if (key) {
      peerByPublicKey.set(key, peerRecord);
    }
  }
  const snapshot = {
    agent_interface_id: String(interfaceRecord.agent_interface_id || "").trim() || null,
    system_interface_name: interfaceName,
    is_up: statusCompleted.status === 0,
    peers: []
  };
  if (statusCompleted.status !== 0) {
    for (const peerRecord of peerRecords) {
      snapshot.peers.push({
        slot: Number(peerRecord.slot) || 0,
        public_key: String(peerRecord.public_key || "").trim() || null,
        latest_handshake_at: null,
        transfer_rx_bytes: null,
        transfer_tx_bytes: null
      });
    }
    return snapshot;
  }
  const dumpCompleted = childProcess.spawnSync("wg", ["show", interfaceName, "dump"], {
    encoding: "utf8"
  });
  if (dumpCompleted.status !== 0) {
    return snapshot;
  }
  const dump = parseWgDump(dumpCompleted.stdout);
  if (!dump) {
    return snapshot;
  }
  for (const peerSnapshot of dump.peers) {
    const peerRecord = peerByPublicKey.get(String(peerSnapshot.public_key || "").trim());
    snapshot.peers.push({
      slot: peerRecord ? Number(peerRecord.slot) || 0 : 0,
      public_key: String(peerSnapshot.public_key || "").trim() || null,
      latest_handshake_at: peerSnapshot.latest_handshake_at,
      transfer_rx_bytes: Number.isFinite(peerSnapshot.transfer_rx_bytes) ? peerSnapshot.transfer_rx_bytes : null,
      transfer_tx_bytes: Number.isFinite(peerSnapshot.transfer_tx_bytes) ? peerSnapshot.transfer_tx_bytes : null
    });
  }
  for (const peerRecord of peerRecords) {
    const key = String(peerRecord.public_key || "").trim();
    if (!key) {
      continue;
    }
    const exists = snapshot.peers.some((item) => item.public_key === key);
    if (!exists) {
      snapshot.peers.push({
        slot: Number(peerRecord.slot) || 0,
        public_key: key,
        latest_handshake_at: null,
        transfer_rx_bytes: null,
        transfer_tx_bytes: null
      });
    }
  }
  return snapshot;
}

function inspectLiveInterfaces() {
  const root = path.join(runtimeRoot(), "interfaces");
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    return [];
  }
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const snapshots = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const metaPath = path.join(root, entry.name, "interface.json");
    if (!fs.existsSync(metaPath) || !fs.statSync(metaPath).isFile()) {
      continue;
    }
    try {
      const interfaceRecord = JSON.parse(fs.readFileSync(metaPath, "utf8"));
      if (!interfaceRecord || typeof interfaceRecord !== "object") {
        continue;
      }
      snapshots.push(inspectLiveInterface(interfaceRecord));
    } catch {
      continue;
    }
  }
  return snapshots;
}

function firewallServerType(serverRecord) {
  const explicit = String(serverRecord && serverRecord.server_type || "").trim().toLowerCase();
  if (explicit) {
    return explicit;
  }
  const component = String(process.env.NELOMAI_AGENT_COMPONENT || "").trim().toLowerCase();
  if (component === "tak-agent") {
    return "tak";
  }
  if (component === "tic-agent") {
    return "tic";
  }
  return "tic";
}

function desiredFirewallRules(state, serverRecord) {
  const type = firewallServerType(serverRecord);
  if (type === "tak") {
    return ["22/tcp", "40404/udp", "42001/udp"];
  }
  const interfaces = Array.isArray(state && state.interfaces) ? state.interfaces : [];
  const dynamicPorts = interfaces
    .filter((item) => item && item.is_enabled)
    .map((item) => Number(item.listen_port))
    .filter((value) => Number.isInteger(value) && value > 0)
    .sort((left, right) => left - right)
    .map((value) => `${value}/udp`);
  return Array.from(new Set(["22/tcp", "40404/udp", ...dynamicPorts]));
}

function buildFirewallReconcileCommands(state, serverRecord) {
  if (executionMode() !== "system") {
    return [];
  }
  if (!commandExists("ufw")) {
    return [];
  }
  const rules = desiredFirewallRules(state, serverRecord);
  return [
    "ufw --force reset",
    "ufw default deny incoming",
    "ufw default allow outgoing",
    ...rules.map((rule) => `ufw allow ${rule}`),
    "ufw --force enable",
    "ufw status",
  ];
}

function buildCreateInterfaceCommands(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  const runtimePeerDir = peersDirectory(interfaceRecord);
  return [
    `install -d -m 700 ${systemWireGuardRoot()}`,
    `install -d -m 700 ${systemPeersRoot()}`,
    `install -d -m 700 ${systemPeerDir}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if [ -d "${runtimePeerDir}" ]; then find "${runtimePeerDir}" -maxdepth 1 -type f -name '*.conf' -exec install -m 600 {} "${systemPeerDir}/" \\; ; fi`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; else wg-quick up ${interfaceName}; fi`,
    ...buildInterfaceNetworkingCommands(interfaceRecord),
  ];
}

function buildToggleInterfaceCommands(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  const runtimePeerDir = peersDirectory(interfaceRecord);

  if (interfaceRecord.is_enabled) {
    return [
      `install -d -m 700 ${systemWireGuardRoot()}`,
      `install -d -m 700 ${systemPeersRoot()}`,
      `install -d -m 700 ${systemPeerDir}`,
      `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
      `if [ -d "${runtimePeerDir}" ]; then find "${runtimePeerDir}" -maxdepth 1 -type f -name '*.conf' -exec install -m 600 {} "${systemPeerDir}/" \\; ; fi`,
      `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; else wg-quick up ${interfaceName}; fi`,
      ...buildInterfaceNetworkingCommands(interfaceRecord),
    ];
  }

  return [
    `if [ -f "${systemConfigPath}" ]; then wg-quick down ${interfaceName} || (ip link show dev ${interfaceName} >/dev/null 2>&1 && ip link delete dev ${interfaceName}) || true; elif ip link show dev ${interfaceName} >/dev/null 2>&1; then ip link delete dev ${interfaceName}; fi`
  ];
}

function buildTogglePeerCommands(interfaceRecord, peerRecord) {
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const interfaceName = systemInterfaceName(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  const runtimePeerPath = peerConfigPath(interfaceRecord, peerRecord);
  const systemPeerPath = systemPeerConfigPath(interfaceRecord, peerRecord);

  if (peerRecord.is_enabled) {
    return [
      `install -d -m 700 ${systemPeersRoot()}`,
      `install -d -m 700 ${systemPeerDir}`,
      `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
      `install -m 600 ${runtimePeerPath} ${systemPeerPath}`,
      `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; fi`,
      ...buildInterfaceNetworkingCommands(interfaceRecord),
    ];
  }

  return [
    `rm -f ${systemPeerPath}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; fi`
  ];
}

function buildRefreshInterfaceCommands(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  const runtimePeerDir = peersDirectory(interfaceRecord);
  const commands = [
    `install -d -m 700 ${systemWireGuardRoot()}`,
    `install -d -m 700 ${systemPeersRoot()}`,
    `install -d -m 700 ${systemPeerDir}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if [ -d "${runtimePeerDir}" ]; then find "${runtimePeerDir}" -maxdepth 1 -type f -name '*.conf' -exec install -m 600 {} "${systemPeerDir}/" \\; ; fi`
  ];
  if (interfaceRecord.is_enabled) {
    commands.push(`if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; else wg-quick up ${interfaceName}; fi`);
    commands.push(...buildInterfaceNetworkingCommands(interfaceRecord));
  }
  return commands;
}

function buildDeleteInterfaceCommands(interfaceRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  return [
    `if [ -f "${systemConfigPath}" ]; then wg-quick down ${interfaceName} || (ip link show dev ${interfaceName} >/dev/null 2>&1 && ip link delete dev ${interfaceName}) || true; elif ip link show dev ${interfaceName} >/dev/null 2>&1; then ip link delete dev ${interfaceName}; fi`,
    `rm -f ${systemConfigPath}`,
    `rm -rf ${systemPeerDir}`,
  ];
}

function buildRecreatePeerCommands(interfaceRecord, peerRecord) {
  const systemPeerDir = systemInterfacePeersRoot(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const interfaceName = systemInterfaceName(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  return [
    `# recreate peer slot ${peerRecord.slot} for ${interfaceRecord.agent_interface_id}`,
    `install -d -m 700 ${systemPeersRoot()}`,
    `install -d -m 700 ${systemPeerDir}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `install -m 600 ${peerConfigPath(interfaceRecord, peerRecord)} ${systemPeerConfigPath(interfaceRecord, peerRecord)}`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; fi`,
    ...buildInterfaceNetworkingCommands(interfaceRecord),
  ];
}

function buildDeletePeerCommands(interfaceRecord, peerRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  return [
    `rm -f ${systemPeerConfigPath(interfaceRecord, peerRecord)}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${interfaceName}) && ip link set up dev ${interfaceName}; fi`,
    ...buildInterfaceNetworkingCommands(interfaceRecord),
  ];
}

function buildAttachTunnelCommands(tunnelRecord) {
  if (executionMode() !== "system") {
    return [`# attach tunnel ${String(tunnelRecord.tunnel_id || "")} in filesystem mode`];
  }
  const quickCommand = resolveTunnelQuickCommand();
  if (!quickCommand) {
    throw new Error("System tunnel execution requires awg-quick, amneziawg-quick, or wg-quick in PATH");
  }
  const runtimeConfigPath = tunnelClientConfigPath(tunnelRecord);
  const systemConfigPath = systemTunnelConfigPath(tunnelRecord);
  const quickConfigPath = systemTunnelQuickConfigPath(tunnelRecord);
  const tunnelName = systemTunnelName(tunnelRecord);
  const userspaceImplementation = resolveTunnelUserspaceImplementation();
  const quickPrefix = userspaceImplementation
    ? `WG_QUICK_USERSPACE_IMPLEMENTATION=${userspaceImplementation} `
    : "";
  return [
    `install -d -m 700 ${systemTunnelRoot()}`,
    `install -d -m 700 ${systemTunnelQuickConfigRoot()}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `install -m 600 ${runtimeConfigPath} ${quickConfigPath}`,
    `if ip link show dev ${tunnelName} >/dev/null 2>&1; then ${quickPrefix}${quickCommand} down ${systemConfigPath} || true; ${quickPrefix}${quickCommand} up ${systemConfigPath}; else ${quickPrefix}${quickCommand} up ${systemConfigPath}; fi`
  ];
}

function buildProvisionTunnelCommands(tunnelRecord) {
  if (executionMode() !== "system") {
    return [`# provision tunnel ${String(tunnelRecord.tunnel_id || "")} in filesystem mode`];
  }
  const quickCommand = resolveTunnelQuickCommand();
  if (!quickCommand) {
    throw new Error("System tunnel execution requires awg-quick, amneziawg-quick, or wg-quick in PATH");
  }
  const runtimeConfigPath = tunnelServerConfigPath(tunnelRecord);
  const systemConfigPath = systemTunnelConfigPath(tunnelRecord);
  const quickConfigPath = systemTunnelQuickConfigPath(tunnelRecord);
  const tunnelName = systemTunnelName(tunnelRecord);
  return [
    `install -d -m 700 ${systemTunnelRoot()}`,
    `install -d -m 700 ${systemTunnelQuickConfigRoot()}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `install -m 600 ${runtimeConfigPath} ${quickConfigPath}`,
    `if ip link show dev ${tunnelName} >/dev/null 2>&1; then ${quickCommand} down ${systemConfigPath} || true; ${quickCommand} up ${systemConfigPath}; else ${quickCommand} up ${systemConfigPath}; fi`,
    ...buildTunnelServerNetworkingCommands(tunnelRecord),
  ];
}

function buildDetachTunnelCommands(tunnelRecord) {
  if (executionMode() !== "system") {
    return [`# detach tunnel ${String(tunnelRecord.tunnel_id || "")} in filesystem mode`];
  }
  const quickCommand = resolveTunnelQuickCommand();
  if (!quickCommand) {
    throw new Error("System tunnel execution requires awg-quick, amneziawg-quick, or wg-quick in PATH");
  }
  const systemConfigPath = systemTunnelConfigPath(tunnelRecord);
  const quickConfigPath = systemTunnelQuickConfigPath(tunnelRecord);
  const tunnelName = systemTunnelName(tunnelRecord);
  const userspaceImplementation = resolveTunnelUserspaceImplementation();
  const quickPrefix = userspaceImplementation
    ? `WG_QUICK_USERSPACE_IMPLEMENTATION=${userspaceImplementation} `
    : "";
  return [
    `if ip link show dev ${tunnelName} >/dev/null 2>&1; then ${quickPrefix}${quickCommand} down ${systemConfigPath} || true; fi`,
    `if ip link show dev ${tunnelName} >/dev/null 2>&1; then ip link delete dev ${tunnelName} || true; fi`,
    `rm -f ${systemConfigPath}`,
    `rm -f ${quickConfigPath}`
  ];
}

function maybeRunSystemCommands(commands) {
  if (executionMode() !== "system") {
    return {
      applied: false,
      mode: executionMode(),
      commands
    };
  }
  const environment = ensureSystemEnvironment();
  for (const command of commands) {
    const completed = childProcess.spawnSync("bash", ["-lc", command], {
      encoding: "utf8"
    });
    if (completed.status !== 0) {
      throw new Error((completed.stderr || completed.stdout || `Command failed: ${command}`).trim());
    }
  }
  return {
    applied: true,
    mode: executionMode(),
    commands,
    environment
  };
}

module.exports = {
  ensureWireGuardKeyMaterial,
  ensureSystemEnvironment,
  inspectRuntimeEnvironment,
  interfaceConfigPath,
  peerConfigPath,
  tunnelDirectory,
  tunnelMetaPath,
  tunnelServerConfigPath,
  tunnelClientConfigPath,
  tunnelClientPayloadPath,
  syncInterfaceArtifacts,
  syncAllPeerArtifacts,
  syncPeerArtifacts,
  syncTunnelArtifacts,
  inspectTunnelArtifacts,
  removeTunnelArtifacts,
  removeInterfaceArtifacts,
  removePeerArtifacts,
  collectInterfaceBundleEntries,
  buildCreateInterfaceCommands,
  buildDeleteInterfaceCommands,
  buildToggleInterfaceCommands,
  buildTogglePeerCommands,
  buildRefreshInterfaceCommands,
  buildRecreatePeerCommands,
  buildDeletePeerCommands,
  buildProvisionTunnelCommands,
  buildAttachTunnelCommands,
  buildDetachTunnelCommands,
  buildFirewallReconcileCommands,
  maybeRunSystemCommands,
  systemInterfaceConfigPath,
  systemPeerConfigPath,
  systemTunnelConfigPath,
  systemTunnelQuickConfigPath,
  systemTunnelName
};
