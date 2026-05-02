"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { buildBootstrapPlan, bootstrapExecutionMode, executeBootstrapPlan } = require("./bootstrap");
const {
  buildCanonicalAmneziaConfig,
  extractCanonicalTunnelArtifacts,
  buildTunnelArtifactsFromConfig,
  buildLegacyAmneziaConfig
} = require("./amnezia_adapter");
const { randomBase64, buildTakTunnelClientPayload } = require("./render");

function deployedStateRoot() {
  const nodeAgentRoot = path.resolve(__dirname, "..");
  const agentsRoot = path.dirname(nodeAgentRoot);
  const currentRoot = path.dirname(agentsRoot);
  if (path.basename(currentRoot) !== "current") {
    return null;
  }
  return path.join(path.dirname(currentRoot), "state");
}

function stateFilePath() {
  const explicit = String(process.env.NELOMAI_AGENT_STATE_FILE || "").trim();
  if (explicit) {
    return explicit;
  }
  const deployedRoot = deployedStateRoot();
  if (deployedRoot) {
    const component = String(process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
    return path.join(deployedRoot, `${component}-state.json`);
  }
  return path.join(__dirname, "..", ".data", "state.json");
}

function daemonStatusFilePath() {
  const explicit = String(process.env.NELOMAI_AGENT_DAEMON_STATUS_FILE || "").trim();
  if (explicit) {
    return explicit;
  }
  const component = String(process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
  const deployedRoot = deployedStateRoot();
  if (deployedRoot) {
    return path.join(deployedRoot, `${component}-daemon-status.json`);
  }
  const stateDir = path.dirname(stateFilePath());
  const base = component.replace(/[^a-z0-9._-]+/gi, "-");
  return path.join(stateDir, `${base}-daemon-status.json`);
}

function readDaemonStatus() {
  const filePath = daemonStatusFilePath();
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return null;
  }
  try {
    const raw = fs.readFileSync(filePath, "utf8").trim();
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function ensureStateDir() {
  fs.mkdirSync(path.dirname(stateFilePath()), { recursive: true });
}

function defaultState() {
  return {
    interfaces: [],
    servers: [],
    bootstrap_tasks: [],
    tunnels: []
  };
}

function loadState() {
  const filePath = stateFilePath();
  if (!fs.existsSync(filePath)) {
    return defaultState();
  }
  const raw = fs.readFileSync(filePath, "utf8").trim();
  if (!raw) {
    return defaultState();
  }
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return defaultState();
  }
  if (!Array.isArray(parsed.interfaces)) {
    parsed.interfaces = [];
  }
  if (!Array.isArray(parsed.servers)) {
    parsed.servers = [];
  }
  if (!Array.isArray(parsed.bootstrap_tasks)) {
    parsed.bootstrap_tasks = [];
  }
  if (!Array.isArray(parsed.tunnels)) {
    parsed.tunnels = [];
  }
  return parsed;
}

function saveState(state) {
  ensureStateDir();
  fs.writeFileSync(stateFilePath(), JSON.stringify(state, null, 2), "utf8");
}

function touch(record) {
  record.updated_at = new Date().toISOString();
}

function applyPendingInput(task, executionResult) {
  const pending = executionResult && executionResult.pending_input ? executionResult.pending_input : null;
  task.input_prompt = pending ? String(pending.prompt || "Additional input is required") : null;
  task.input_key = pending ? String(pending.key || "") : null;
  task.input_kind = pending ? String(pending.kind || "text") : null;
  task.resume_from_step = pending && Number(pending.step_index) > 0 ? Number(pending.step_index) : 1;
}

function nextSequence(items) {
  const max = items.reduce((current, item) => Math.max(current, Number(item.id) || 0), 0);
  return max + 1;
}

function nextTunnelSequence(items) {
  const max = items.reduce((current, item) => Math.max(current, Number(item.sequence) || 0), 0);
  return max + 1;
}

function normalizeName(value) {
  return String(value || "").trim().toLowerCase();
}

function parseAddressSlot(addressV4) {
  const raw = String(addressV4 || "").trim();
  const ipPart = raw.split("/")[0];
  const parts = ipPart.split(".");
  if (parts.length !== 4) {
    return null;
  }
  const slot = Number(parts[2]);
  if (!Number.isInteger(slot) || slot < 1 || slot > 254) {
    return null;
  }
  return slot;
}

function interfaceNetworkPrefix(addressV4) {
  const raw = String(addressV4 || "").trim();
  const ipPart = raw.split("/")[0];
  const parts = ipPart.split(".");
  if (parts.length !== 4) {
    return null;
  }
  const octets = parts.map((value) => Number(value));
  if (octets.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) {
    return null;
  }
  return `${octets[0]}.${octets[1]}.${octets[2]}`;
}

function peerAddressForInterfaceSlot(interfaceRecord, slot) {
  const normalizedSlot = Number(slot);
  const prefix = interfaceNetworkPrefix(interfaceRecord && interfaceRecord.address_v4);
  if (!prefix || !Number.isInteger(normalizedSlot) || normalizedSlot <= 0) {
    return "10.200.255.2/32";
  }
  const hostOctet = Math.min(normalizedSlot + 1, 254);
  return `${prefix}.${hostOctet}/32`;
}

function interfacesForTicServer(state, ticServerId) {
  return state.interfaces.filter((item) => Number(item.tic_server_id) === Number(ticServerId));
}

function findServerRecord(state, serverPayload) {
  const id = Number(serverPayload && serverPayload.id);
  const name = String(serverPayload && serverPayload.name || "").trim();
  const host = String(serverPayload && serverPayload.host || "").trim();
  return (
    state.servers.find((item) => Number.isInteger(id) && id > 0 && Number(item.server_id) === id) ||
    state.servers.find((item) => name && host && normalizeName(item.name) === normalizeName(name) && String(item.host) === host) ||
    null
  );
}

function ensureServerRecord(state, serverPayload) {
  if (!serverPayload || typeof serverPayload !== "object") {
    throw new Error("Missing server payload");
  }
  const serverId = Number(serverPayload.id);
  const name = String(serverPayload.name || "").trim();
  const host = String(serverPayload.host || "").trim();
  const serverType = String(serverPayload.server_type || "").trim();
  if (!name || !host || !serverType) {
    throw new Error("Incomplete server payload");
  }
  let record = findServerRecord(state, serverPayload);
  if (!record) {
    record = {
      server_id: Number.isInteger(serverId) && serverId > 0 ? serverId : 0,
      name,
        server_type: serverType,
        host,
        ssh_port: Number(serverPayload.ssh_port) || 22,
        ssh_login: serverPayload.ssh_login == null ? null : String(serverPayload.ssh_login),
        ssh_password: serverPayload.ssh_password == null ? null : String(serverPayload.ssh_password),
        is_active: false,
        agent_installed: false,
        current_version: process.env.NELOMAI_AGENT_VERSION || "0.1.0",
      latest_version: process.env.NELOMAI_AGENT_LATEST_VERSION || process.env.NELOMAI_AGENT_VERSION || "0.1.0",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    state.servers.push(record);
  } else {
    if (Number.isInteger(serverId) && serverId > 0) {
      record.server_id = serverId;
    }
    record.name = name;
    record.server_type = serverType;
    record.host = host;
    if (serverPayload.ssh_port != null) {
      record.ssh_port = Number(serverPayload.ssh_port) || record.ssh_port;
    }
      if (serverPayload.ssh_login != null) {
        record.ssh_login = String(serverPayload.ssh_login);
      }
      if (serverPayload.ssh_password != null) {
        record.ssh_password = String(serverPayload.ssh_password);
      }
      touch(record);
    }
  return record;
}

function findTunnelRecord(state, payload) {
  const tunnelId = String((payload && payload.tunnel_id) || "").trim();
  const serverPayload = payload && payload.server && typeof payload.server === "object" ? payload.server : null;
  const ticServerPayload = payload && payload.tic_server && typeof payload.tic_server === "object" ? payload.tic_server : null;
  const takServerPayload = payload && payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : null;
  const selfServerId = Number(serverPayload && serverPayload.id);
  const ticServerId = Number(
    (ticServerPayload && ticServerPayload.id) ||
    (serverPayload && serverPayload.server_type === "tic" ? serverPayload.id : 0)
  );
  const takServerId = Number(
    (takServerPayload && takServerPayload.id) ||
    (serverPayload && serverPayload.server_type === "tak" ? serverPayload.id : 0)
  );
  const tunnels = Array.isArray(state && state.tunnels) ? state.tunnels : [];
  return (
    tunnels.find((item) => tunnelId && String(item.tunnel_id || "") === tunnelId) ||
    tunnels.find((item) =>
      Number.isInteger(ticServerId) && ticServerId > 0 &&
      Number.isInteger(takServerId) && takServerId > 0 &&
      Number(item.tic_server_id) === ticServerId &&
      Number(item.tak_server_id) === takServerId
    ) ||
    tunnels.find((item) =>
      Number.isInteger(selfServerId) && selfServerId > 0 &&
      (Number(item.tic_server_id) === selfServerId || Number(item.tak_server_id) === selfServerId)
    ) ||
    null
  );
}

function buildAmneziaConfig(record) {
  if (record.tunnel_artifacts && typeof record.tunnel_artifacts === "object") {
    return buildLegacyAmneziaConfig(record.tunnel_artifacts, record);
  }
  if (record.amnezia_config && typeof record.amnezia_config === "object") {
    return record.amnezia_config;
  }
  return buildTakTunnelClientPayload(record);
}

function buildTunnelArtifacts(record) {
  if (record.tunnel_artifacts && typeof record.tunnel_artifacts === "object") {
    return record.tunnel_artifacts;
  }
  return buildTunnelArtifactsFromConfig(buildAmneziaConfig(record), record);
}

function tunnelAwgParameters(sequence) {
  const normalized = Number(sequence) || 1;
  const headerBase = 1000000000 + normalized * 1000;
  return {
    awg_headers: {
      H1: `${headerBase}-${headerBase + 49}`,
      H2: `${headerBase + 100}-${headerBase + 149}`,
      H3: `${headerBase + 200}-${headerBase + 249}`,
      H4: `${headerBase + 300}-${headerBase + 349}`,
    },
    awg_session_noise: {
      S1: 24,
      S2: 16,
      S3: 12,
      S4: 8,
    },
    awg_init_noise: {
      I1: "<r 2><b 0x8580000100010000000004796162730679616e6465780272750000010001c00c000100010000026d000457fa27d1>",
      I2: "",
      I3: "",
      I4: "",
      I5: "",
    },
    awg_junk: {
      Jc: 3,
      Jmin: 64,
      Jmax: 96,
    },
  };
}

function buildTakTunnelPlan(state, payload) {
  const serverPayload = payload && payload.server && typeof payload.server === "object" ? payload.server : null;
  const ticServerPayload = payload && payload.tic_server && typeof payload.tic_server === "object" ? payload.tic_server : null;
  const takServerPayload = payload && payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : serverPayload;
  if (!ticServerPayload || !takServerPayload) {
    throw new Error("Tunnel payload must include Tic and Tak server metadata");
  }
  const ticServerId = Number(ticServerPayload.id);
  const takServerId = Number(takServerPayload.id);
  if (!Number.isInteger(ticServerId) || ticServerId <= 0 || !Number.isInteger(takServerId) || takServerId <= 0) {
    throw new Error("Tunnel payload must include valid Tic/Tak server ids");
  }
  const existing = findTunnelRecord(state, payload);
  if (existing) {
    return {
      ...existing,
      amnezia_config: buildAmneziaConfig(existing)
    };
  }

  const tunnels = Array.isArray(state.tunnels) ? state.tunnels : [];
  const sequence = nextTunnelSequence(tunnels);
  const subnetOctet = Math.min(10 + sequence, 254);
  const listenPort = 42000 + sequence;
  const record = {
    id: nextSequence(tunnels),
    sequence,
    tunnel_id: `tak-tunnel-${takServerId}-${String(sequence).padStart(5, "0")}`,
    protocol: "amneziawg-2.0",
    tic_server_id: ticServerId,
    tic_server_name: String(ticServerPayload.name || `tic-${ticServerId}`),
    tic_server_host: String(ticServerPayload.host || ""),
    tak_server_id: takServerId,
    tak_server_name: String(takServerPayload.name || `tak-${takServerId}`),
    tak_server_host: String(takServerPayload.host || ""),
    listen_port: listenPort,
    network_cidr: `172.27.${subnetOctet}.0/30`,
    tak_address_v4: `172.27.${subnetOctet}.1/30`,
    tic_address_v4: `172.27.${subnetOctet}.2/30`,
    server_private_key: randomBase64(),
    server_public_key: randomBase64(),
    client_private_key: randomBase64(),
    client_public_key: randomBase64(),
    ...tunnelAwgParameters(sequence),
    nat_mode: "masquerade",
    status: "planned",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString()
  };
  const amneziaConfig = buildCanonicalAmneziaConfig(record);
  const canonicalArtifacts = extractCanonicalTunnelArtifacts(amneziaConfig);
  const tunnelArtifacts = buildTunnelArtifactsFromConfig(amneziaConfig, record);
  return {
    ...record,
    tunnel_artifacts: tunnelArtifacts,
    amnezia_config: amneziaConfig,
    amnezia_source: canonicalArtifacts.source,
    server_config_text: canonicalArtifacts.server_config_text,
    client_config_text: canonicalArtifacts.client_config_text,
  };
}

function provisionTakTunnelRecord(state, payload) {
  if (!Array.isArray(state.tunnels)) {
    state.tunnels = [];
  }
  const existing = findTunnelRecord(state, payload);
  if (existing) {
    existing.status = "provisioned";
    touch(existing);
    saveState(state);
    return {
      ...existing,
      tunnel_artifacts: buildTunnelArtifacts(existing),
      amnezia_config: buildAmneziaConfig(existing)
    };
  }

  const plan = buildTakTunnelPlan(state, payload);
  const persisted = {
    id: plan.id,
    sequence: plan.sequence,
    tunnel_id: plan.tunnel_id,
    protocol: plan.protocol,
    tic_server_id: plan.tic_server_id,
    tic_server_name: plan.tic_server_name,
    tic_server_host: plan.tic_server_host,
    tak_server_id: plan.tak_server_id,
    tak_server_name: plan.tak_server_name,
    tak_server_host: plan.tak_server_host,
    listen_port: plan.listen_port,
    network_cidr: plan.network_cidr,
    tak_address_v4: plan.tak_address_v4,
    tic_address_v4: plan.tic_address_v4,
    server_private_key: plan.server_private_key,
    server_public_key: plan.server_public_key,
    client_private_key: plan.client_private_key,
    client_public_key: plan.client_public_key,
    awg_headers: plan.awg_headers,
    awg_session_noise: plan.awg_session_noise,
    awg_init_noise: plan.awg_init_noise,
    awg_junk: plan.awg_junk,
    nat_mode: plan.nat_mode,
    amnezia_source: plan.amnezia_source,
    tunnel_artifacts: plan.tunnel_artifacts,
    server_config_text: plan.server_config_text,
    client_config_text: plan.client_config_text,
    amnezia_config: plan.amnezia_config,
    status: "provisioned",
    created_at: plan.created_at,
    updated_at: new Date().toISOString()
  };
  state.tunnels.push(persisted);
  saveState(state);
  return {
    ...persisted,
    tunnel_artifacts: buildTunnelArtifacts(persisted),
    amnezia_config: buildAmneziaConfig(persisted)
  };
}

function getTakTunnelRecord(state, payload) {
  const existing = findTunnelRecord(state, payload);
  if (!existing) {
    return null;
  }
  return {
    ...existing,
    tunnel_artifacts: buildTunnelArtifacts(existing),
    amnezia_config: buildAmneziaConfig(existing)
  };
}

function attachTakTunnelRecord(state, payload) {
  if (!Array.isArray(state.tunnels)) {
    state.tunnels = [];
  }
  const serverPayload = payload && payload.server && typeof payload.server === "object" ? payload.server : null;
  const takServerPayload = payload && payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : null;
  const tunnelArtifactsPayload = payload && payload.tunnel_artifacts && typeof payload.tunnel_artifacts === "object"
    ? payload.tunnel_artifacts
    : null;
  const amneziaConfig = tunnelArtifactsPayload
    ? buildLegacyAmneziaConfig(tunnelArtifactsPayload, {})
    : payload && payload.amnezia_config && typeof payload.amnezia_config === "object"
      ? payload.amnezia_config
      : null;
  if (!serverPayload || String(serverPayload.server_type || "").trim() !== "tic") {
    throw new Error("attach_tak_tunnel requires Tic server payload");
  }
  if (!takServerPayload || String(takServerPayload.server_type || "").trim() !== "tak") {
    throw new Error("attach_tak_tunnel requires Tak server payload");
  }
  if (!amneziaConfig) {
    throw new Error("attach_tak_tunnel requires tunnel_artifacts or amnezia_config");
  }

  const tunnelId = String(amneziaConfig.tunnel_id || payload.tunnel_id || "").trim();
  if (!tunnelId) {
    throw new Error("attach_tak_tunnel requires tunnel_id");
  }

  let existing = state.tunnels.find((item) => String(item.tunnel_id || "") === tunnelId) || null;
  if (!existing) {
    existing = {
      id: nextSequence(state.tunnels),
      sequence: nextTunnelSequence(state.tunnels),
      tunnel_id: tunnelId,
      protocol: String(amneziaConfig.protocol || "amneziawg-2.0"),
      tic_server_id: Number(serverPayload.id) || 0,
      tic_server_name: String(serverPayload.name || ""),
      tic_server_host: String(serverPayload.host || ""),
      tak_server_id: Number(takServerPayload.id) || 0,
      tak_server_name: String(takServerPayload.name || ""),
      tak_server_host: String(takServerPayload.host || ""),
      created_at: new Date().toISOString(),
    };
    state.tunnels.push(existing);
  }

  existing.local_role = "tic";
  existing.protocol = String(amneziaConfig.protocol || existing.protocol || "amneziawg-2.0");
  existing.tic_server_id = Number(serverPayload.id) || existing.tic_server_id || 0;
  existing.tic_server_name = String(serverPayload.name || existing.tic_server_name || "");
  existing.tic_server_host = String(serverPayload.host || existing.tic_server_host || "");
  existing.tak_server_id = Number(takServerPayload.id) || existing.tak_server_id || 0;
  existing.tak_server_name = String(takServerPayload.name || existing.tak_server_name || "");
  existing.tak_server_host = String(takServerPayload.host || existing.tak_server_host || "");
  const canonicalArtifacts = extractCanonicalTunnelArtifacts(amneziaConfig);
  existing.listen_port = Number(amneziaConfig.endpoint?.port || amneziaConfig.endpoint_port) || Number(existing.listen_port) || 0;
  existing.network_cidr = String(amneziaConfig.addressing?.network_cidr || amneziaConfig.network_cidr || existing.network_cidr || "");
  existing.tak_address_v4 = String(amneziaConfig.addressing?.tak_address_v4 || amneziaConfig.tak_address_v4 || existing.tak_address_v4 || "");
  existing.tic_address_v4 = String(amneziaConfig.addressing?.tic_address_v4 || amneziaConfig.tic_address_v4 || existing.tic_address_v4 || "");
  existing.client_private_key = String(amneziaConfig.keys?.client_private_key || amneziaConfig.client_private_key || existing.client_private_key || "");
  existing.client_public_key = String(amneziaConfig.keys?.client_public_key || amneziaConfig.client_public_key || existing.client_public_key || "");
  existing.server_public_key = String(amneziaConfig.keys?.server_public_key || amneziaConfig.server_public_key || existing.server_public_key || "");
  existing.awg_headers = {
    H1: String(amneziaConfig.awg_parameters?.header_obfuscation?.H1 || existing.awg_headers?.H1 || "0"),
    H2: String(amneziaConfig.awg_parameters?.header_obfuscation?.H2 || existing.awg_headers?.H2 || "0"),
    H3: String(amneziaConfig.awg_parameters?.header_obfuscation?.H3 || existing.awg_headers?.H3 || "0"),
    H4: String(amneziaConfig.awg_parameters?.header_obfuscation?.H4 || existing.awg_headers?.H4 || "0"),
  };
  existing.awg_session_noise = {
    S1: Number(amneziaConfig.awg_parameters?.session_noise?.S1) || Number(existing.awg_session_noise?.S1) || 0,
    S2: Number(amneziaConfig.awg_parameters?.session_noise?.S2) || Number(existing.awg_session_noise?.S2) || 0,
    S3: Number(amneziaConfig.awg_parameters?.session_noise?.S3) || Number(existing.awg_session_noise?.S3) || 0,
    S4: Number(amneziaConfig.awg_parameters?.session_noise?.S4) || Number(existing.awg_session_noise?.S4) || 0,
  };
  existing.awg_init_noise = {
    I1: String(amneziaConfig.awg_parameters?.init_noise?.I1 || existing.awg_init_noise?.I1 || ""),
    I2: String(amneziaConfig.awg_parameters?.init_noise?.I2 || existing.awg_init_noise?.I2 || ""),
    I3: String(amneziaConfig.awg_parameters?.init_noise?.I3 || existing.awg_init_noise?.I3 || ""),
    I4: String(amneziaConfig.awg_parameters?.init_noise?.I4 || existing.awg_init_noise?.I4 || ""),
    I5: String(amneziaConfig.awg_parameters?.init_noise?.I5 || existing.awg_init_noise?.I5 || ""),
  };
  existing.awg_junk = {
    Jc: Number(amneziaConfig.awg_parameters?.junk_packets?.Jc) || Number(existing.awg_junk?.Jc) || 0,
    Jmin: Number(amneziaConfig.awg_parameters?.junk_packets?.Jmin) || Number(existing.awg_junk?.Jmin) || 0,
    Jmax: Number(amneziaConfig.awg_parameters?.junk_packets?.Jmax) || Number(existing.awg_junk?.Jmax) || 0,
  };
  existing.nat_mode = String(amneziaConfig.nat_mode || existing.nat_mode || "masquerade");
  existing.amnezia_source = canonicalArtifacts.source;
  existing.tunnel_artifacts = tunnelArtifactsPayload || buildTunnelArtifactsFromConfig(amneziaConfig, existing);
  existing.server_config_text = canonicalArtifacts.server_config_text || existing.server_config_text || "";
  existing.client_config_text = canonicalArtifacts.client_config_text || existing.client_config_text || "";
  existing.amnezia_config = {
    ...amneziaConfig
  };
  existing.status = "attached";
  touch(existing);
  saveState(state);
  return {
    ...existing,
    tunnel_artifacts: buildTunnelArtifacts(existing),
    amnezia_config: {
      ...amneziaConfig
    }
  };
}

function detachTakTunnelRecord(state, payload) {
  if (!Array.isArray(state.tunnels)) {
    state.tunnels = [];
  }
  const existing = findTunnelRecord(state, payload);
  if (!existing) {
    throw new Error("detach_tak_tunnel requires known tunnel");
  }
  existing.status = "detached";
  existing.last_error = null;
  existing.last_handshake_at = null;
  touch(existing);
  saveState(state);
  return {
    ...existing,
    tunnel_artifacts: buildTunnelArtifacts(existing),
    amnezia_config: buildAmneziaConfig(existing)
  };
}

function createBootstrapTaskRecord(state, payload) {
  const serverPayload = payload.server && typeof payload.server === "object" ? payload.server : null;
  const serverRecord = ensureServerRecord(state, serverPayload);
  const bootstrapPlan = buildBootstrapPlan(serverRecord, payload);
  const executionMode = bootstrapExecutionMode();
  let executionResult = null;
  let taskStatus = process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED === "1" ? "input_required" : "completed";
  let taskLogs = process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED === "1"
    ? [...bootstrapPlan.summary, "Waiting for admin confirmation"]
    : [...bootstrapPlan.summary];
  let lastError = null;
  let lastErrorCode = null;
  if (taskStatus !== "input_required") {
    try {
      executionResult = executeBootstrapPlan(bootstrapPlan, {
        mode: executionMode,
        server: serverRecord,
        inputs: {},
        start_index: 1
      });
      taskLogs = [...taskLogs, ...executionResult.logs, executionResult.applied ? "Bootstrap completed" : "Bootstrap plan prepared"];
      if (executionResult.pending_input) {
        taskStatus = "input_required";
      }
    } catch (error) {
      taskStatus = "failed";
      executionResult = error && typeof error === "object" && error.execution_result ? error.execution_result : null;
      if (executionResult && Array.isArray(executionResult.logs)) {
        taskLogs = [...taskLogs, ...executionResult.logs];
      }
      lastError = error instanceof Error ? error.message : String(error);
      lastErrorCode = error && typeof error === "object"
        ? String(error.error_code || executionResult?.last_error_code || "").trim() || null
        : null;
      taskLogs = [...taskLogs, lastError];
    }
  }
  const task = {
    id: nextSequence(state.bootstrap_tasks),
    server_id: serverRecord.server_id,
    server_name: serverRecord.name,
    server_type: serverRecord.server_type,
    repository_url: bootstrapPlan.repository_url,
    os_family: bootstrapPlan.os_family,
    os_version: bootstrapPlan.os_version,
    install_root: bootstrapPlan.install_root,
      service_name: bootstrapPlan.service_name,
      command_profile: bootstrapPlan.command_profile,
      packages: bootstrapPlan.packages,
      safe_init_packages: bootstrapPlan.safe_init_packages,
      full_only_packages: bootstrapPlan.full_only_packages,
      commands: bootstrapPlan.commands,
      systemd_unit: bootstrapPlan.systemd_unit,
    bootstrap_mode: executionMode,
    bootstrap_inputs: {},
    resume_from_step: 1,
    execution_result: executionResult,
    status: taskStatus,
    logs: taskLogs,
    last_error: lastError,
    last_error_code: lastErrorCode,
    input_prompt: process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED === "1" ? "Confirm agent install" : null,
    input_key: process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED === "1" ? "install_confirm" : null,
    input_kind: process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED === "1" ? "confirm" : null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString()
  };
  if (!process.env.NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED && executionResult && executionResult.pending_input) {
    applyPendingInput(task, executionResult);
  }
  state.bootstrap_tasks.push(task);
  if (task.status === "completed") {
    serverRecord.agent_installed = true;
    serverRecord.is_active = true;
    serverRecord.last_seen_at = new Date().toISOString();
    touch(serverRecord);
  }
  saveState(state);
  return { task, serverRecord };
}

function findBootstrapTaskRecord(state, taskId) {
  const normalizedTaskId = Number(taskId);
  return state.bootstrap_tasks.find((item) => Number(item.id) === normalizedTaskId) || null;
}

function getBootstrapTaskRecord(state, taskId) {
  const task = findBootstrapTaskRecord(state, taskId);
  if (!task) {
    throw new Error("Bootstrap task is not known to agent state");
  }
  return task;
}

function completeBootstrapTaskRecord(state, payload) {
  const taskId = Number(payload.task_id);
  if (!Number.isInteger(taskId) || taskId <= 0) {
    throw new Error("Missing valid task_id");
  }
  const task = getBootstrapTaskRecord(state, taskId);
  const input = payload.input && typeof payload.input === "object" ? payload.input : null;
  if (!input) {
    throw new Error("Missing input payload");
  }
  if (String(input.key || "") !== String(task.input_key || "")) {
    throw new Error("Unexpected input key");
  }
  if (String(input.kind || "") !== String(task.input_kind || "")) {
    throw new Error("Unexpected input kind");
  }
  if (String(task.input_kind || "") === "confirm" && String(input.value || "").trim().toLowerCase() !== "yes") {
    throw new Error("Bootstrap confirmation was rejected");
  }
  if (!task.bootstrap_inputs || typeof task.bootstrap_inputs !== "object") {
    task.bootstrap_inputs = {};
  }
  task.bootstrap_inputs[String(input.key || "")] = input.value == null ? "" : String(input.value);
  task.logs = [...task.logs, `Bootstrap input accepted: ${String(input.key || "unknown")}`];
  task.input_prompt = null;
  task.input_key = null;
  task.input_kind = null;
  task.last_error = null;
  task.last_error_code = null;
  const serverRecord = state.servers.find((item) => Number(item.server_id) === Number(task.server_id)) || null;
  try {
    const executionResult = executeBootstrapPlan(
      {
        repository_url: task.repository_url,
        os_family: task.os_family,
        os_version: task.os_version,
        install_root: task.install_root,
          service_name: task.service_name,
          command_profile: task.command_profile,
          packages: task.packages,
          safe_init_packages: task.safe_init_packages,
          full_only_packages: task.full_only_packages,
          commands: task.commands,
          systemd_unit: task.systemd_unit
      },
      {
        mode: task.bootstrap_mode || bootstrapExecutionMode(),
        server: serverRecord || null,
        inputs: task.bootstrap_inputs,
        start_index: Number(task.resume_from_step) > 0 ? Number(task.resume_from_step) : 1
      }
    );
    task.execution_result = executionResult;
    task.status = executionResult.pending_input ? "input_required" : "completed";
    task.logs = [...task.logs, ...executionResult.logs, executionResult.applied ? "Bootstrap completed" : "Bootstrap plan prepared"];
    applyPendingInput(task, executionResult);
  } catch (error) {
    task.status = "failed";
    task.execution_result = error && typeof error === "object" && error.execution_result ? error.execution_result : null;
    if (task.execution_result && Array.isArray(task.execution_result.logs)) {
      task.logs = [...task.logs, ...task.execution_result.logs];
    }
    task.last_error = error instanceof Error ? error.message : String(error);
    task.last_error_code = error && typeof error === "object"
      ? String(error.error_code || task.execution_result?.last_error_code || "").trim() || null
      : null;
    task.logs = [...task.logs, task.last_error];
  }
  touch(task);

  if (serverRecord && task.status === "completed") {
    serverRecord.agent_installed = true;
    serverRecord.is_active = true;
    serverRecord.last_seen_at = new Date().toISOString();
    touch(serverRecord);
  }
  saveState(state);
  return { task, serverRecord };
}

function refreshServerStatusRecord(state, payload) {
  const serverPayload = payload.server && typeof payload.server === "object" ? payload.server : null;
  const record = ensureServerRecord(state, serverPayload);
  const daemonStatus = readDaemonStatus();
  const daemonRunning = daemonStatus && String(daemonStatus.status || "").trim().toLowerCase() === "running";
  if (!record.agent_installed && !daemonRunning) {
    record.is_active = false;
    record.last_seen_at = null;
  } else {
    record.is_active = true;
    if (daemonRunning) {
      record.agent_installed = true;
      record.current_version = String(daemonStatus.version || record.current_version || process.env.NELOMAI_AGENT_VERSION || "0.1.0");
    }
    record.last_seen_at = new Date().toISOString();
  }
  touch(record);
  saveState(state);
  return record;
}

function checkServerUpdateRecord(state, payload) {
  const serverPayload = payload.server && typeof payload.server === "object" ? payload.server : null;
  const record = ensureServerRecord(state, serverPayload);
  const latestVersion = process.env.NELOMAI_AGENT_LATEST_VERSION || record.latest_version || record.current_version || "0.1.0";
  record.latest_version = latestVersion;
  touch(record);
  saveState(state);
  return {
    record,
    update_available: String(record.current_version || "") !== String(latestVersion || "")
  };
}

function applyServerUpdateRecord(state, payload) {
  const { record } = checkServerUpdateRecord(state, payload);
  record.current_version = record.latest_version || record.current_version || "0.1.0";
  record.agent_installed = true;
  record.is_active = true;
  record.last_seen_at = new Date().toISOString();
  touch(record);
  saveState(state);
  return record;
}

function findFirstFreePort(state, ticServerId) {
  const usedPorts = new Set(
    interfacesForTicServer(state, ticServerId)
      .map((item) => Number(item.listen_port))
      .filter((value) => Number.isInteger(value) && value > 0)
  );
  let port = 10001;
  while (usedPorts.has(port)) {
    port += 1;
  }
  return port;
}

function findFirstFreeAddress(state, ticServerId) {
  const usedSlots = new Set(
    interfacesForTicServer(state, ticServerId)
      .map((item) => parseAddressSlot(item.address_v4))
      .filter((value) => Number.isInteger(value))
  );
  let slot = 1;
  while (usedSlots.has(slot)) {
    slot += 1;
  }
  return `10.8.${slot}.1/24`;
}

function interfaceNameExists(state, name) {
  const normalized = normalizeName(name);
  return state.interfaces.some((item) => normalizeName(item.name) === normalized);
}

function networkValuesInUse(state, ticServerId, listenPort, addressV4) {
  return state.interfaces.find(
    (item) =>
      Number(item.tic_server_id) === Number(ticServerId) &&
      (Number(item.listen_port) === Number(listenPort) || String(item.address_v4).trim() === String(addressV4).trim())
  ) || null;
}

function nextAgentInterfaceId(state, ticServerId) {
  const used = new Set(
    interfacesForTicServer(state, ticServerId)
      .map((item) => String(item.agent_interface_id || ""))
      .filter(Boolean)
  );
  let counter = interfacesForTicServer(state, ticServerId).length + 1;
  let candidate = `wg-${ticServerId}-${String(counter).padStart(5, "0")}`;
  while (used.has(candidate)) {
    counter += 1;
    candidate = `wg-${ticServerId}-${String(counter).padStart(5, "0")}`;
  }
  return candidate;
}

function findInterfaceRecord(state, interfacePayload) {
  const panelId = Number(interfacePayload && interfacePayload.id);
  const agentInterfaceId = String(interfacePayload && interfacePayload.agent_interface_id || "").trim();
  const name = String(interfacePayload && interfacePayload.name || "").trim();
  const ticServerId =
    interfacePayload &&
    interfacePayload.server_identity &&
    interfacePayload.server_identity.tic_server_id != null
      ? Number(interfacePayload.server_identity.tic_server_id)
      : null;

  return (
    state.interfaces.find((item) => Number.isInteger(panelId) && panelId > 0 && Number(item.panel_interface_id) === panelId) ||
    state.interfaces.find((item) => agentInterfaceId && String(item.agent_interface_id) === agentInterfaceId) ||
    state.interfaces.find(
      (item) => name && ticServerId != null && Number(item.tic_server_id) === ticServerId && normalizeName(item.name) === normalizeName(name)
    ) ||
    null
  );
}

function ensureInterfaceRecord(state, payload) {
  const interfacePayload = payload.interface && typeof payload.interface === "object" ? payload.interface : {};
  const record = findInterfaceRecord(state, interfacePayload);
  if (!record) {
    throw new Error("Interface is not known to agent state");
  }
  if (!Array.isArray(record.peers)) {
    record.peers = [];
  }
  return record;
}

function ensureInterfaceRecordPersisted(state, payload) {
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  touch(interfaceRecord);
  saveState(state);
  return interfaceRecord;
}

function findPeerRecord(interfaceRecord, peerPayload) {
  const panelPeerId = Number(peerPayload && peerPayload.id);
  const slot = Number(peerPayload && peerPayload.slot);
  return (
    interfaceRecord.peers.find((item) => Number.isInteger(panelPeerId) && panelPeerId > 0 && Number(item.panel_peer_id) === panelPeerId) ||
    interfaceRecord.peers.find((item) => Number.isInteger(slot) && slot > 0 && Number(item.slot) === slot) ||
    null
  );
}

function ensurePeerRecord(interfaceRecord, peerPayload) {
  if (!peerPayload || typeof peerPayload !== "object") {
    throw new Error("Missing peer payload");
  }
  const panelPeerId = Number(peerPayload.id);
  const slot = Number(peerPayload.slot);
  if (!Number.isInteger(slot) || slot <= 0) {
    throw new Error("Missing valid peer.slot");
  }
  const peerAddressV4 = peerAddressForInterfaceSlot(interfaceRecord, slot);

  let record = findPeerRecord(interfaceRecord, peerPayload);
  if (!record) {
    record = {
      panel_peer_id: Number.isInteger(panelPeerId) && panelPeerId > 0 ? panelPeerId : 0,
      slot,
      address_v4: peerAddressV4,
      comment: peerPayload.comment == null ? null : String(peerPayload.comment),
      is_enabled: false,
      config_revision: 0,
      config_exists: false,
      block_filters_enabled: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    interfaceRecord.peers.push(record);
  } else {
    if (Number.isInteger(panelPeerId) && panelPeerId > 0) {
      record.panel_peer_id = panelPeerId;
    }
    record.slot = slot;
    record.address_v4 = peerAddressV4;
    if (peerPayload.comment !== undefined) {
      record.comment = peerPayload.comment == null ? null : String(peerPayload.comment);
    }
    touch(record);
  }
  return record;
}

function ensurePeerRecordPersisted(state, payload) {
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  const peerPayload = payload.peer && typeof payload.peer === "object" ? payload.peer : null;
  const peerRecord = ensurePeerRecord(interfaceRecord, peerPayload);
  touch(peerRecord);
  touch(interfaceRecord);
  saveState(state);
  return { interfaceRecord, peerRecord };
}

function createInterfaceRecord(state, payload) {
  const interfacePayload = payload.interface && typeof payload.interface === "object" ? payload.interface : {};
  const ticServer = payload.tic_server && typeof payload.tic_server === "object" ? payload.tic_server : {};
  const takServer = payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : null;

  const ticServerId = Number(ticServer.id);
  const listenPort = Number(interfacePayload.listen_port);
  const addressV4 = String(interfacePayload.address_v4 || "").trim();
  const name = String(interfacePayload.name || "").trim();

  if (!Number.isInteger(ticServerId) || ticServerId <= 0) {
    throw new Error("Missing valid tic_server.id");
  }
  if (!name) {
    throw new Error("Missing interface.name");
  }
  if (!Number.isInteger(listenPort) || listenPort <= 0) {
    throw new Error("Missing valid interface.listen_port");
  }
  if (!addressV4) {
    throw new Error("Missing valid interface.address_v4");
  }
  if (interfaceNameExists(state, name)) {
    throw new Error(`Interface name already exists: ${name}`);
  }
  const occupied = networkValuesInUse(state, ticServerId, listenPort, addressV4);
  if (occupied) {
    throw new Error("listen_port or address_v4 is already used on this Tic server");
  }

  const agentInterfaceId = nextAgentInterfaceId(state, ticServerId);
  const record = {
    panel_interface_id: Number(interfacePayload.id) || 0,
    agent_interface_id: agentInterfaceId,
    tic_server_id: ticServerId,
    tic_server_name: String(ticServer.name || "").trim() || null,
    tic_server_host: String(ticServer.host || "").trim() || null,
    tak_server_id: takServer && takServer.id != null ? Number(takServer.id) : null,
    tak_server_name: takServer ? String(takServer.name || "").trim() || null : null,
    tak_server_host: takServer ? String(takServer.host || "").trim() || null : null,
    name,
    route_mode: String(interfacePayload.route_mode || "standalone"),
    listen_port: listenPort,
    address_v4: addressV4,
    is_enabled: false,
    exclusion_filters_enabled: Boolean(payload.exclusion_filters && payload.exclusion_filters.enabled),
    block_filters_enabled: Boolean(payload.block_filters && payload.block_filters.enabled),
    peers: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString()
  };
  state.interfaces.push(record);
  saveState(state);
  return record;
}

function toggleInterfaceRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  if (typeof targetState.is_enabled !== "boolean") {
    throw new Error("Missing target_state.is_enabled");
  }
  const record = ensureInterfaceRecord(state, payload);
  record.is_enabled = targetState.is_enabled;
  touch(record);
  saveState(state);
  return record;
}

function updateInterfaceRouteModeRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  const routeMode = String(targetState.route_mode || "").trim();
  if (!routeMode) {
    throw new Error("Missing target_state.route_mode");
  }
  const record = ensureInterfaceRecord(state, payload);
  record.route_mode = routeMode;
  touch(record);
  saveState(state);
  return record;
}

function updateInterfaceTakServerRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  if (!Object.prototype.hasOwnProperty.call(targetState, "tak_server_id")) {
    throw new Error("Missing target_state.tak_server_id");
  }
  const record = ensureInterfaceRecord(state, payload);
  record.tak_server_id = targetState.tak_server_id == null ? null : Number(targetState.tak_server_id);
  const takServer = payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : null;
  record.tak_server_name = takServer ? String(takServer.name || "").trim() || null : null;
  record.tak_server_host = takServer ? String(takServer.host || "").trim() || null : null;
  if (targetState.route_mode !== undefined) {
    const routeMode = String(targetState.route_mode || "").trim();
    if (!routeMode) {
      throw new Error("Invalid target_state.route_mode");
    }
    record.route_mode = routeMode;
  }
  touch(record);
  saveState(state);
  return record;
}

function updateInterfaceExclusionFiltersRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  if (typeof targetState.exclusion_filters_enabled !== "boolean") {
    throw new Error("Missing target_state.exclusion_filters_enabled");
  }
  const record = ensureInterfaceRecord(state, payload);
  record.exclusion_filters_enabled = targetState.exclusion_filters_enabled;
  touch(record);
  saveState(state);
  return record;
}

function togglePeerRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  if (typeof targetState.is_enabled !== "boolean") {
    throw new Error("Missing target_state.is_enabled");
  }
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  const peerPayload = payload.peer && typeof payload.peer === "object" ? payload.peer : null;
  const peerRecord = ensurePeerRecord(interfaceRecord, peerPayload);
  peerRecord.is_enabled = targetState.is_enabled;
  peerRecord.config_exists = true;
  touch(peerRecord);
  touch(interfaceRecord);
  saveState(state);
  return peerRecord;
}

function recreatePeerRecord(state, payload) {
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  const peerPayload = payload.peer && typeof payload.peer === "object" ? payload.peer : null;
  const peerRecord = ensurePeerRecord(interfaceRecord, peerPayload);
  peerRecord.config_revision = Number(peerRecord.config_revision || 0) + 1;
  peerRecord.config_exists = true;
  touch(peerRecord);
  touch(interfaceRecord);
  saveState(state);
  return peerRecord;
}

function updatePeerBlockFiltersRecord(state, payload) {
  const targetState = payload.target_state && typeof payload.target_state === "object" ? payload.target_state : {};
  if (typeof targetState.block_filters_enabled !== "boolean") {
    throw new Error("Missing target_state.block_filters_enabled");
  }
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  const peerPayload = payload.peer && typeof payload.peer === "object" ? payload.peer : null;
  const peerRecord = ensurePeerRecord(interfaceRecord, peerPayload);
  peerRecord.block_filters_enabled = targetState.block_filters_enabled;
  touch(peerRecord);
  touch(interfaceRecord);
  saveState(state);
  return peerRecord;
}

function deletePeerRecord(state, payload) {
  const interfaceRecord = ensureInterfaceRecord(state, payload);
  const peerPayload = payload.peer && typeof payload.peer === "object" ? payload.peer : null;
  if (!peerPayload || typeof peerPayload !== "object") {
    throw new Error("Missing peer payload");
  }
  const peerRecord = findPeerRecord(interfaceRecord, peerPayload);
  if (!peerRecord) {
    throw new Error("Peer is not known to agent state");
  }
  interfaceRecord.peers = interfaceRecord.peers.filter((item) => item !== peerRecord);
  touch(interfaceRecord);
  saveState(state);
  return peerRecord;
}

module.exports = {
  loadState,
  saveState,
  findTunnelRecord,
  buildTakTunnelPlan,
  provisionTakTunnelRecord,
  getTakTunnelRecord,
  attachTakTunnelRecord,
  detachTakTunnelRecord,
  findFirstFreePort,
  findFirstFreeAddress,
  createInterfaceRecord,
  createBootstrapTaskRecord,
  findBootstrapTaskRecord,
  completeBootstrapTaskRecord,
  refreshServerStatusRecord,
  checkServerUpdateRecord,
  applyServerUpdateRecord,
  ensureInterfaceRecordPersisted,
  ensurePeerRecordPersisted,
  toggleInterfaceRecord,
  updateInterfaceRouteModeRecord,
  updateInterfaceTakServerRecord,
  updateInterfaceExclusionFiltersRecord,
  togglePeerRecord,
  recreatePeerRecord,
  updatePeerBlockFiltersRecord,
  deletePeerRecord
};
