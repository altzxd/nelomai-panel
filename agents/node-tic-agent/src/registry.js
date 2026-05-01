"use strict";

const { ACTION_REGISTRY } = require("./constants");

function getActionSpec(action) {
  return ACTION_REGISTRY[action] || null;
}

function listCapabilities() {
  const values = new Set();
  for (const spec of Object.values(ACTION_REGISTRY)) {
    for (const capability of spec.capabilities) {
      values.add(capability);
    }
  }
  return Array.from(values).sort();
}

function listActions() {
  return Object.keys(ACTION_REGISTRY).sort();
}

module.exports = {
  getActionSpec,
  listActions,
  listCapabilities
};
