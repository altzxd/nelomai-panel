"use strict";

const {
  ACTION_REGISTRY,
  AGENT_CONTRACT_VERSION,
  AGENT_SUPPORTED_CONTRACTS,
  DEFAULT_COMPONENT
} = require("./constants");

function parseSupportedContractsFromEnv() {
  const raw = process.env.NELOMAI_AGENT_SUPPORTED_CONTRACTS;
  if (!raw || !raw.trim()) {
    return [...AGENT_SUPPORTED_CONTRACTS];
  }
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function agentRuntimeMeta() {
  return {
    agentVersion: process.env.NELOMAI_AGENT_VERSION || "0.1.0",
    contractVersion: AGENT_CONTRACT_VERSION,
    supportedContracts: parseSupportedContractsFromEnv(),
    component: process.env.NELOMAI_AGENT_COMPONENT || DEFAULT_COMPONENT
  };
}

function validatePayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "Payload must be a JSON object";
  }

  const action = typeof payload.action === "string" ? payload.action : "";
  if (!action) {
    return "Missing action";
  }

  const spec = ACTION_REGISTRY[action];
  if (!spec) {
    return `Unsupported action: ${action}`;
  }

  const component = typeof payload.component === "string" ? payload.component : "";
  if (!component) {
    return "Missing component";
  }
  if (!spec.components.includes(component)) {
    return `Action ${action} is not allowed for component ${component}`;
  }

  const requestedCapabilities = Array.isArray(payload.requested_capabilities)
    ? payload.requested_capabilities.map((item) => String(item))
    : [];
  for (const capability of spec.capabilities) {
    if (!requestedCapabilities.includes(capability)) {
      return `Missing requested capability ${capability} for action ${action}`;
    }
  }

  const panelSupported = Array.isArray(payload.supported_contracts)
    ? payload.supported_contracts.map((item) => String(item))
    : [];
  const sharedContracts = panelSupported.filter((item) => agentRuntimeMeta().supportedContracts.includes(item));
  const panelContractVersion = payload.contract_version == null ? null : String(payload.contract_version);

  if (panelContractVersion && agentRuntimeMeta().supportedContracts.includes(panelContractVersion)) {
    return null;
  }
  if (sharedContracts.length > 0) {
    return null;
  }
  return `Unsupported contract version: panel=${panelContractVersion || "unknown"}, agent=${agentRuntimeMeta().supportedContracts.join(",")}`;
}

module.exports = {
  agentRuntimeMeta,
  validatePayload
};
