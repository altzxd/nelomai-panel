"use strict";

const fs = require("node:fs");
const path = require("node:path");
const childProcess = require("node:child_process");

const {
  peerFileName,
  peerLinuxFileName,
  renderInterfaceConfig,
  renderPeerConfig
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

function removePeerArtifacts(interfaceRecord, peerRecord) {
  const peerPath = peerConfigPath(interfaceRecord, peerRecord);
  if (fs.existsSync(peerPath) && fs.statSync(peerPath).isFile()) {
    fs.unlinkSync(peerPath);
  }
  writeJsonFile(interfaceMetaPath(interfaceRecord), interfaceRecord);
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
  return String(interfaceRecord.agent_interface_id || interfaceRecord.name || "interface").trim();
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

function ensureSystemKeyMaterial(interfaceRecord, options = {}) {
  if (executionMode() !== "system") {
    return false;
  }
  ensureSystemEnvironment();
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

  return {
    mode,
    runtime_root,
    wireguard_root,
    peers_root,
    ready: checks.every((item) => item.ok),
    checks
  };
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
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; else wg-quick up ${systemConfigPath}; fi`
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
      `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; else wg-quick up ${systemConfigPath}; fi`
    ];
  }

  return [
    `if [ -f "${systemConfigPath}" ]; then wg-quick down ${systemConfigPath} || (ip link show dev ${interfaceName} >/dev/null 2>&1 && ip link delete dev ${interfaceName}) || true; elif ip link show dev ${interfaceName} >/dev/null 2>&1; then ip link delete dev ${interfaceName}; fi`
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
      `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; fi`
    ];
  }

  return [
    `rm -f ${systemPeerPath}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; fi`
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
    commands.push(`if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; else wg-quick up ${systemConfigPath}; fi`);
  }
  return commands;
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
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; fi`
  ];
}

function buildDeletePeerCommands(interfaceRecord, peerRecord) {
  const interfaceName = systemInterfaceName(interfaceRecord);
  const systemConfigPath = systemInterfaceConfigPath(interfaceRecord);
  const runtimeConfigPath = interfaceConfigPath(interfaceRecord);
  return [
    `rm -f ${systemPeerConfigPath(interfaceRecord, peerRecord)}`,
    `install -m 600 ${runtimeConfigPath} ${systemConfigPath}`,
    `if ip link show dev ${interfaceName} >/dev/null 2>&1; then wg syncconf ${interfaceName} <(wg-quick strip ${systemConfigPath}) && ip link set up dev ${interfaceName}; fi`
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
  ensureSystemKeyMaterial,
  ensureSystemEnvironment,
  inspectRuntimeEnvironment,
  interfaceConfigPath,
  peerConfigPath,
  syncInterfaceArtifacts,
  syncPeerArtifacts,
  removePeerArtifacts,
  collectInterfaceBundleEntries,
  buildCreateInterfaceCommands,
  buildToggleInterfaceCommands,
  buildTogglePeerCommands,
  buildRefreshInterfaceCommands,
  buildRecreatePeerCommands,
  buildDeletePeerCommands,
  maybeRunSystemCommands,
  systemInterfaceConfigPath,
  systemPeerConfigPath
};
