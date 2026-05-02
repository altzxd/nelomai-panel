"use strict";

const childProcess = require("node:child_process");
const { renderTakTunnelServerConfig, renderTicTunnelClientConfig } = require("./render");

function officialAmneziaCommand() {
  return String(process.env.NELOMAI_AMNEZIAWG_TOOL_CMD || "").trim();
}

function officialAmneziaModule() {
  return String(process.env.NELOMAI_AMNEZIAWG_TOOL_MODULE || "").trim();
}

function loadOfficialModule(modulePath) {
  try {
    return require(modulePath);
  } catch (error) {
    throw new Error(`Official AmneziaWG adapter module load failed: ${error.message}`);
  }
}

function splitCommandLine(command) {
  const parts = [];
  const source = String(command || "");
  let current = "";
  let quote = null;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === quote) {
        quote = null;
      } else {
        current += character;
      }
      continue;
    }
    if (character === "\"" || character === "'") {
      quote = character;
      continue;
    }
    if (/\s/.test(character)) {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }
    current += character;
  }
  if (current) {
    parts.push(current);
  }
  return parts;
}

function fallbackAmneziaConfig(plan) {
  const config = {
    protocol: String(plan.protocol || "amneziawg-2.0"),
    tunnel_id: String(plan.tunnel_id || ""),
    version: "2.0",
    source: "built-in-fallback",
    endpoint: {
      host: String(plan.tak_server_host || ""),
      port: Number(plan.listen_port) || 0
    },
    addressing: {
      network_cidr: String(plan.network_cidr || ""),
      tak_address_v4: String(plan.tak_address_v4 || ""),
      tic_address_v4: String(plan.tic_address_v4 || ""),
      allowed_ips: [String(plan.network_cidr || "")].filter(Boolean)
    },
    keys: {
      client_private_key: String(plan.client_private_key || ""),
      client_public_key: String(plan.client_public_key || ""),
      server_public_key: String(plan.server_public_key || "")
    },
    awg_parameters: {
      header_obfuscation: {
        H1: String(plan.awg_headers?.H1 || "0"),
        H2: String(plan.awg_headers?.H2 || "0"),
        H3: String(plan.awg_headers?.H3 || "0"),
        H4: String(plan.awg_headers?.H4 || "0")
      },
      session_noise: {
        S1: Number(plan.awg_session_noise?.S1) || 0,
        S2: Number(plan.awg_session_noise?.S2) || 0,
        S3: Number(plan.awg_session_noise?.S3) || 0,
        S4: Number(plan.awg_session_noise?.S4) || 0
      },
      init_noise: {
        I1: String(plan.awg_init_noise?.I1 || ""),
        I2: String(plan.awg_init_noise?.I2 || ""),
        I3: String(plan.awg_init_noise?.I3 || ""),
        I4: String(plan.awg_init_noise?.I4 || ""),
        I5: String(plan.awg_init_noise?.I5 || "")
      },
      junk_packets: {
        Jc: Number(plan.awg_junk?.Jc) || 0,
        Jmin: Number(plan.awg_junk?.Jmin) || 0,
        Jmax: Number(plan.awg_junk?.Jmax) || 0
      },
    },
    nat_mode: String(plan.nat_mode || "masquerade"),
    generated_at: String(plan.updated_at || plan.created_at || ""),
  };
  config.canonical_artifacts = {
    server_config_text: renderTakTunnelServerConfig(plan),
    client_config_text: renderTicTunnelClientConfig(plan),
  };
  return config;
}

function normalizeOfficialResult(result, plan) {
  const rawConfig = result && typeof result.amnezia_config === "object" ? result.amnezia_config : result;
  if (!rawConfig || typeof rawConfig !== "object") {
    throw new Error("Official AmneziaWG adapter returned no amnezia_config object");
  }
  const config = {
    ...rawConfig,
    protocol: String(rawConfig.protocol || plan.protocol || "amneziawg-2.0"),
    tunnel_id: String(rawConfig.tunnel_id || plan.tunnel_id || ""),
    version: String(rawConfig.version || "2.0"),
    source: String(rawConfig.source || "official-tooling"),
    endpoint: typeof rawConfig.endpoint === "object" && rawConfig.endpoint ? rawConfig.endpoint : {
      host: String(plan.tak_server_host || ""),
      port: Number(plan.listen_port) || 0
    },
    addressing: typeof rawConfig.addressing === "object" && rawConfig.addressing ? rawConfig.addressing : {
      network_cidr: String(plan.network_cidr || ""),
      tak_address_v4: String(plan.tak_address_v4 || ""),
      tic_address_v4: String(plan.tic_address_v4 || ""),
      allowed_ips: [String(plan.network_cidr || "")].filter(Boolean)
    },
    keys: typeof rawConfig.keys === "object" && rawConfig.keys ? rawConfig.keys : {
      client_private_key: String(plan.client_private_key || ""),
      client_public_key: String(plan.client_public_key || ""),
      server_public_key: String(plan.server_public_key || "")
    },
    awg_parameters: typeof rawConfig.awg_parameters === "object" && rawConfig.awg_parameters ? rawConfig.awg_parameters : fallbackAmneziaConfig(plan).awg_parameters,
    nat_mode: String(rawConfig.nat_mode || plan.nat_mode || "masquerade"),
    generated_at: String(rawConfig.generated_at || plan.updated_at || plan.created_at || ""),
  };
  const rawArtifacts = rawConfig.canonical_artifacts && typeof rawConfig.canonical_artifacts === "object"
    ? rawConfig.canonical_artifacts
    : {};
  config.canonical_artifacts = {
    server_config_text: String(rawArtifacts.server_config_text || renderTakTunnelServerConfig(plan)),
    client_config_text: String(rawArtifacts.client_config_text || renderTicTunnelClientConfig(plan)),
  };
  return config;
}

function buildOfficialRequest(plan) {
  return {
    request_type: "provision_tak_tunnel",
    protocol: "amneziawg-2.0",
    tunnel: {
      tunnel_id: String(plan.tunnel_id || ""),
      listen_port: Number(plan.listen_port) || 0,
      network_cidr: String(plan.network_cidr || ""),
      tak_address_v4: String(plan.tak_address_v4 || ""),
      tic_address_v4: String(plan.tic_address_v4 || ""),
      nat_mode: String(plan.nat_mode || "masquerade"),
    },
    servers: {
      tic: {
        id: Number(plan.tic_server_id) || 0,
        name: String(plan.tic_server_name || ""),
        host: String(plan.tic_server_host || ""),
      },
      tak: {
        id: Number(plan.tak_server_id) || 0,
        name: String(plan.tak_server_name || ""),
        host: String(plan.tak_server_host || ""),
      }
    },
    provisional_keys: {
      server_private_key: String(plan.server_private_key || ""),
      server_public_key: String(plan.server_public_key || ""),
      client_private_key: String(plan.client_private_key || ""),
      client_public_key: String(plan.client_public_key || ""),
    },
    provisional_awg_parameters: fallbackAmneziaConfig(plan).awg_parameters,
  };
}

function buildCanonicalAmneziaConfig(plan) {
  const modulePath = officialAmneziaModule();
  if (modulePath) {
    const adapter = loadOfficialModule(modulePath);
    if (typeof adapter !== "function") {
      throw new Error("Official AmneziaWG adapter module must export a function");
    }
    return normalizeOfficialResult(adapter(buildOfficialRequest(plan)), plan);
  }
  const command = officialAmneziaCommand();
  if (!command) {
    return fallbackAmneziaConfig(plan);
  }
  const commandParts = splitCommandLine(command);
  if (!commandParts.length) {
    return fallbackAmneziaConfig(plan);
  }
  const completed = childProcess.spawnSync(commandParts[0], commandParts.slice(1), {
    input: JSON.stringify(buildOfficialRequest(plan)),
    encoding: "utf8",
  });
  if (completed.status !== 0) {
    const detail = String(completed.error ? completed.error.message : (completed.stderr || completed.stdout || "Official AmneziaWG adapter failed")).trim();
    throw new Error(detail);
  }
  let parsed;
  try {
    parsed = JSON.parse((completed.stdout || "").trim() || "{}");
  } catch (error) {
    throw new Error("Official AmneziaWG adapter returned invalid JSON");
  }
  return normalizeOfficialResult(parsed, plan);
}

function extractCanonicalTunnelArtifacts(config) {
  const artifacts = config && typeof config.canonical_artifacts === "object" ? config.canonical_artifacts : {};
  return {
    source: String(config && config.source || "unknown"),
    server_config_text: String(artifacts.server_config_text || ""),
    client_config_text: String(artifacts.client_config_text || ""),
  };
}

module.exports = {
  officialAmneziaCommand,
  buildCanonicalAmneziaConfig,
  extractCanonicalTunnelArtifacts,
};
