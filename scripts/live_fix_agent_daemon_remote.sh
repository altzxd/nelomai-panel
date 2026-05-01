set -e

ROOT=/opt/nelomai
AGENT_DIR="${ROOT}/current/agents/node-tic-agent"
TYPE="${NELOMAI_SERVER_TYPE:-tic}"
SERVICE_NAME="nelomai-${TYPE}-agent.service"

install -d -m 755 "${AGENT_DIR}/src"

cat > "${AGENT_DIR}/src/daemon.js" <<'EOF'
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { inspectRuntimeEnvironment } = require("./runtime");

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

  writeStatus("running", runtime, {
    started_at: new Date().toISOString()
  });
  log("Agent daemon started", {
    runtime_root: runtime.runtime_root,
    wireguard_root: runtime.wireguard_root
  });

  setInterval(() => {
    const currentRuntime = inspectRuntimeEnvironment();
    writeStatus("running", currentRuntime);
  }, heartbeatIntervalMs());

  process.on("SIGTERM", shutdown("SIGTERM"));
  process.on("SIGINT", shutdown("SIGINT"));
}

main();
EOF

cd "${AGENT_DIR}"
/usr/bin/node --check src/daemon.js
systemctl reset-failed "${SERVICE_NAME}" || true
systemctl restart "${SERVICE_NAME}"
sleep 3
systemctl --no-pager --full status "${SERVICE_NAME}"
echo "---"
cat "/opt/nelomai/state/${TYPE}-agent-daemon-status.json"
