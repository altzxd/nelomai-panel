"use strict";

const { listCapabilities } = require("./registry");
const { agentRuntimeMeta } = require("./validation");

function baseEnvelope(extra) {
  const meta = agentRuntimeMeta();
  return {
    agent_version: meta.agentVersion,
    contract_version: meta.contractVersion,
    supported_contracts: meta.supportedContracts,
    capabilities: listCapabilities(),
    ...extra
  };
}

function ok(extra) {
  return baseEnvelope({ ok: true, ...extra });
}

function fail(error, extra) {
  return baseEnvelope({ ok: false, error, ...(extra || {}) });
}

module.exports = {
  ok,
  fail
};
