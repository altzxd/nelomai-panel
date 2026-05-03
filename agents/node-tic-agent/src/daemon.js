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
  maybeRunSystemCommands
} = require("./runtime");
const { loadState } = require("./state");

function componentName() {
  return String(process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
}

function version() {
  return String(process.env.NELOMAI_AGENT_VERSION || "0.1.0").trim() || "0.1.0";
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

  const timer = setInterval(() => {
    const currentRuntime = inspectRuntimeEnvironment();
    writeStatus("running", currentRuntime);
  }, heartbeatIntervalMs());

  process.on("SIGTERM", shutdown("SIGTERM"));
  process.on("SIGINT", shutdown("SIGINT"));
}

main();
