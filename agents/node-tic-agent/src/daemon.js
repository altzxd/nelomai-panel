"use strict";

const fs = require("node:fs");
const path = require("node:path");

const {
  inspectRuntimeEnvironment,
  syncAllPeerArtifacts,
  syncTunnelArtifacts,
  buildAttachTunnelCommands,
  buildProvisionTunnelCommands,
  buildRefreshInterfaceCommands,
  buildFirewallReconcileCommands,
  maybeRunSystemCommands,
  systemInterfaceName,
  systemTunnelName
} = require("./runtime");
const { loadState } = require("./state");

function componentName() {
  return String(process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
}

function version() {
  return String(process.env.NELOMAI_AGENT_VERSION || "0.1.1").trim() || "0.1.1";
}

function stateFilePath() {
  return String(process.env.NELOMAI_AGENT_STATE_FILE || "").trim();
}

function statusFilePath() {
  const explicit = String(process.env.NELOMAI_AGENT_DAEMON_STATUS_FILE || "").trim();
  if (explicit) {
    return explicit;
  }
  const stateFile = stateFilePath();
  if (stateFile) {
    const stateDir = path.dirname(stateFile);
    const base = componentName().replace(/[^a-z0-9._-]+/gi, "-");
    return path.join(stateDir, `${base}-daemon-status.json`);
  }
  return path.join(process.cwd(), ".daemon-status.json");
}

function heartbeatIntervalMs() {
  const value = Number(process.env.NELOMAI_AGENT_DAEMON_HEARTBEAT_SEC || 30);
  if (Number.isFinite(value) && value >= 5) {
    return Math.floor(value * 1000);
  }
  return 30000;
}

function selfHealIntervalMs() {
  const value = Number(process.env.NELOMAI_AGENT_DAEMON_SELF_HEAL_SEC || 30);
  if (Number.isFinite(value) && value >= 5) {
    return Math.floor(value * 1000);
  }
  return heartbeatIntervalMs();
}

function selfHealEnabled() {
  return String(process.env.NELOMAI_AGENT_DAEMON_SELF_HEAL_ENABLED || "1").trim() !== "0";
}

function writeStatus(status, runtime, extra = {}) {
  const targetPath = statusFilePath();
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  const payload = {
    component: componentName(),
    version: version(),
    pid: process.pid,
    status,
    runtime,
    updated_at: new Date().toISOString(),
    ...extra
  };
  fs.writeFileSync(targetPath, JSON.stringify(payload, null, 2), "utf8");
}

function log(message, extra = null) {
  const line = {
    ts: new Date().toISOString(),
    component: componentName(),
    pid: process.pid,
    message
  };
  if (extra && typeof extra === "object") {
    line.extra = extra;
  }
  process.stdout.write(`${JSON.stringify(line)}\n`);
}

function shutdown(signal) {
  return () => {
    try {
      const runtime = inspectRuntimeEnvironment();
      writeStatus("stopping", runtime, { signal });
      log("Agent daemon stopping", { signal });
    } finally {
      process.exit(0);
    }
  };
}

function executionMode() {
  return String(process.env.NELOMAI_AGENT_EXEC_MODE || "filesystem").trim().toLowerCase();
}

function commandSucceeds(command) {
  const completed = require("node:child_process").spawnSync("bash", ["-lc", command], {
    encoding: "utf8"
  });
  return completed.status === 0;
}

function defaultTunnelLocalRole() {
  const component = String(process.env.NELOMAI_AGENT_COMPONENT || "").trim().toLowerCase();
  if (component === "tak-agent") {
    return "tak";
  }
  if (component === "tic-agent") {
    return "tic";
  }
  return "";
}

function tunnelLocalRole(tunnelRecord) {
  const explicit = String(tunnelRecord && tunnelRecord.local_role || "").trim().toLowerCase();
  return explicit || defaultTunnelLocalRole();
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

function viaTakInterfaces(state) {
  const interfaces = Array.isArray(state && state.interfaces) ? state.interfaces : [];
  return interfaces.filter((item) =>
    item &&
    item.is_enabled &&
    String(item.route_mode || "").trim() === "via_tak" &&
    Number(item.tak_server_id) > 0
  );
}

function tunnelsByPair(state) {
  const map = new Map();
  const tunnels = Array.isArray(state && state.tunnels) ? state.tunnels : [];
  for (const tunnelRecord of tunnels) {
    if (!tunnelRecord || typeof tunnelRecord !== "object") {
      continue;
    }
    const key = `${Number(tunnelRecord.tic_server_id) || 0}:${Number(tunnelRecord.tak_server_id) || 0}`;
    map.set(key, tunnelRecord);
  }
  return map;
}

function collectViaTakMismatches(state) {
  const issues = [];
  const tunnelMap = tunnelsByPair(state);
  for (const interfaceRecord of viaTakInterfaces(state)) {
    const key = `${Number(interfaceRecord.tic_server_id) || 0}:${Number(interfaceRecord.tak_server_id) || 0}`;
    const tunnelRecord = tunnelMap.get(key);
    if (!tunnelRecord || !shouldRestoreTunnel(tunnelRecord)) {
      issues.push({
        kind: "missing_tunnel_record",
        agent_interface_id: String(interfaceRecord.agent_interface_id || ""),
        tak_server_id: Number(interfaceRecord.tak_server_id) || 0
      });
      continue;
    }
    const interfaceName = systemInterfaceName(interfaceRecord);
    const tunnelName = systemTunnelName(tunnelRecord);
    const tableId = interfaceRouteTableId(interfaceRecord);
    const subnet = ipv4NetworkCidr(interfaceRecord.address_v4);
    const takGateway = String(tunnelRecord.tak_address_v4 || "").split("/", 1)[0];
    const hasTunnel = commandSucceeds(`ip link show dev ${tunnelName} >/dev/null 2>&1`);
    const hasInterface = commandSucceeds(`ip link show dev ${interfaceName} >/dev/null 2>&1`);
    const hasDefaultRoute = takGateway
      ? commandSucceeds(`ip route show table ${tableId} | grep -F "default via ${takGateway} dev ${tunnelName}" >/dev/null 2>&1`)
      : false;
    const hasSubnetRule = subnet
      ? commandSucceeds(`ip rule show | grep -F "from ${subnet} lookup ${tableId}" >/dev/null 2>&1`)
      : false;
    if (!hasTunnel || !hasInterface || !hasDefaultRoute || !hasSubnetRule) {
      issues.push({
        kind: "via_tak_datapath_drift",
        agent_interface_id: String(interfaceRecord.agent_interface_id || ""),
        tunnel_id: String(tunnelRecord.tunnel_id || ""),
        checks: {
          tunnel_up: hasTunnel,
          interface_up: hasInterface,
          default_route: hasDefaultRoute,
          subnet_rule: hasSubnetRule
        }
      });
    }
  }
  return issues;
}

function repairViaTakDatapath(state, issues) {
  const repairedTunnels = new Set();
  const repairedInterfaces = new Set();
  const tunnelMap = tunnelsByPair(state);
  for (const interfaceRecord of viaTakInterfaces(state)) {
    const key = `${Number(interfaceRecord.tic_server_id) || 0}:${Number(interfaceRecord.tak_server_id) || 0}`;
    const tunnelRecord = tunnelMap.get(key);
    if (!tunnelRecord || !shouldRestoreTunnel(tunnelRecord)) {
      continue;
    }
    syncTunnelArtifacts(tunnelRecord);
    syncAllPeerArtifacts(interfaceRecord);
    if (!repairedTunnels.has(String(tunnelRecord.tunnel_id || ""))) {
      maybeRunSystemCommands(buildAttachTunnelCommands(tunnelRecord));
      repairedTunnels.add(String(tunnelRecord.tunnel_id || ""));
    }
    maybeRunSystemCommands(buildRefreshInterfaceCommands(interfaceRecord));
    repairedInterfaces.add(String(interfaceRecord.agent_interface_id || ""));
  }
  maybeRunSystemCommands(buildFirewallReconcileCommands(state, {}));
  return {
    repaired: true,
    repaired_tunnels: Array.from(repairedTunnels),
    repaired_interfaces: Array.from(repairedInterfaces),
    issue_count: Array.isArray(issues) ? issues.length : 0
  };
}

function runPeriodicSelfHeal() {
  if (!selfHealEnabled() || executionMode() !== "system") {
    return {
      enabled: selfHealEnabled(),
      skipped: true,
      reason: executionMode() !== "system" ? "filesystem_mode" : "disabled"
    };
  }
  const state = loadState();
  const issues = collectViaTakMismatches(state);
  if (!issues.length) {
    return {
      enabled: true,
      skipped: true,
      reason: "healthy",
      issue_count: 0
    };
  }
  return {
    enabled: true,
    skipped: false,
    issues,
    repair: repairViaTakDatapath(state, issues)
  };
}

function shouldRestoreTunnel(tunnelRecord) {
  if (!tunnelRecord || typeof tunnelRecord !== "object") {
    return false;
  }
  const status = String(tunnelRecord.status || "").trim().toLowerCase();
  if (!status || status === "detached" || status === "missing") {
    return false;
  }
  const localRole = tunnelLocalRole(tunnelRecord);
  if (localRole === "tic") {
    return ["attached", "active", "recovered", "provisioned"].includes(status);
  }
  if (localRole === "tak") {
    return ["provisioned", "active", "attached", "recovered"].includes(status);
  }
  return ["attached", "active", "provisioned", "recovered"].includes(status);
}

function restoreRuntimeFromState() {
  if (executionMode() !== "system") {
    return {
      skipped: true,
      reason: "filesystem_mode",
      restored_tunnels: 0,
      restored_interfaces: 0,
      errors: []
    };
  }

  const state = loadState();
  const summary = {
    skipped: false,
    restored_tunnels: 0,
    restored_interfaces: 0,
    errors: []
  };

  const tunnels = Array.isArray(state.tunnels) ? state.tunnels : [];
  for (const tunnelRecord of tunnels) {
    try {
      syncTunnelArtifacts(tunnelRecord);
      if (!shouldRestoreTunnel(tunnelRecord)) {
        continue;
      }
      const localRole = tunnelLocalRole(tunnelRecord);
      const commands = localRole === "tak"
        ? buildProvisionTunnelCommands(tunnelRecord)
        : buildAttachTunnelCommands(tunnelRecord);
      maybeRunSystemCommands(commands);
      summary.restored_tunnels += 1;
    } catch (error) {
      summary.errors.push({
        kind: "tunnel",
        tunnel_id: String(tunnelRecord && tunnelRecord.tunnel_id || ""),
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }

  try {
    maybeRunSystemCommands(buildFirewallReconcileCommands(state, {}));
  } catch (error) {
    summary.errors.push({
      kind: "firewall_pre",
      error: error instanceof Error ? error.message : String(error)
    });
  }

  const interfaces = Array.isArray(state.interfaces) ? state.interfaces : [];
  for (const interfaceRecord of interfaces) {
    try {
      syncAllPeerArtifacts(interfaceRecord);
      if (!interfaceRecord || !interfaceRecord.is_enabled) {
        continue;
      }
      maybeRunSystemCommands(buildRefreshInterfaceCommands(interfaceRecord));
      summary.restored_interfaces += 1;
    } catch (error) {
      summary.errors.push({
        kind: "interface",
        agent_interface_id: String(interfaceRecord && interfaceRecord.agent_interface_id || ""),
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }

  try {
    maybeRunSystemCommands(buildFirewallReconcileCommands(state, {}));
  } catch (error) {
    summary.errors.push({
      kind: "firewall_post",
      error: error instanceof Error ? error.message : String(error)
    });
  }

  return summary;
}

function main() {
  const runtime = inspectRuntimeEnvironment();
  if (!runtime.ready) {
    writeStatus("failed", runtime, {
      reason: "runtime_not_ready"
    });
    log("Agent daemon runtime is not ready", { runtime });
    process.exit(1);
    return;
  }

  const restoreSummary = restoreRuntimeFromState();
  const postRestoreRuntime = inspectRuntimeEnvironment();

  writeStatus("running", postRestoreRuntime, {
    started_at: new Date().toISOString(),
    restore: restoreSummary
  });
  log("Agent daemon started", {
    runtime_root: postRestoreRuntime.runtime_root,
    wireguard_root: postRestoreRuntime.wireguard_root,
    restore: restoreSummary
  });

  let lastSelfHeal = {
    skipped: true,
    reason: "not_run_yet"
  };
  let selfHealInFlight = false;

  const selfHealTick = () => {
    if (selfHealInFlight) {
      return;
    }
    selfHealInFlight = true;
    try {
      lastSelfHeal = runPeriodicSelfHeal();
      if (!lastSelfHeal.skipped || (lastSelfHeal.reason && lastSelfHeal.reason !== "healthy")) {
        log("Agent daemon self-heal tick", lastSelfHeal);
      }
    } catch (error) {
      lastSelfHeal = {
        skipped: false,
        error: error instanceof Error ? error.message : String(error)
      };
      log("Agent daemon self-heal failed", lastSelfHeal);
    } finally {
      selfHealInFlight = false;
    }
  };

  selfHealTick();

  const timer = setInterval(() => {
    const currentRuntime = inspectRuntimeEnvironment();
    writeStatus("running", currentRuntime, { last_self_heal: lastSelfHeal });
  }, heartbeatIntervalMs());

  const selfHealTimer = setInterval(selfHealTick, selfHealIntervalMs());

  process.on("SIGTERM", shutdown("SIGTERM"));
  process.on("SIGINT", shutdown("SIGINT"));
}

main();
