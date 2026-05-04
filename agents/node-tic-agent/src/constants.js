"use strict";

const AGENT_CONTRACT_VERSION = "1.0";
const AGENT_SUPPORTED_CONTRACTS = ["1.0"];
const DEFAULT_COMPONENT = "tic-agent";

const ACTION_REGISTRY = {
  bootstrap_server: {
    components: ["server-agent"],
    capabilities: ["agent.bootstrap.v1", "agent.update.v1"]
  },
  bootstrap_server_status: {
    components: ["server-agent"],
    capabilities: ["agent.bootstrap.v1", "agent.update.v1"]
  },
  bootstrap_server_input: {
    components: ["server-agent"],
    capabilities: ["agent.bootstrap.v1", "agent.update.v1"]
  },
  restart_server_agent: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.lifecycle.v1"]
  },
  verify_server_status: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.status.v1"]
  },
  verify_server_runtime: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.runtime.v1"]
  },
  reboot_server: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.lifecycle.v1"]
  },
  check_server_agent_update: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.update.v1"]
  },
  update_server_agent: {
    components: ["server-agent", "tic-agent", "tak-agent", "storage-agent"],
    capabilities: ["agent.update.v1"]
  },
  create_server_backup: {
    components: ["tic-agent", "tak-agent"],
    capabilities: ["backup.server.v1"]
  },
  verify_server_backup_copy: {
    components: ["tic-agent", "tak-agent"],
    capabilities: ["backup.server.v1"]
  },
  cleanup_server_backups: {
    components: ["tic-agent", "tak-agent"],
    capabilities: ["backup.server.v1"]
  },
  provision_tak_tunnel: {
    components: ["tak-agent"],
    capabilities: ["tunnel.tak.provision.v1"]
  },
  attach_tak_tunnel: {
    components: ["tic-agent"],
    capabilities: ["tunnel.tak.attach.v1"]
  },
  verify_tak_tunnel_status: {
    components: ["tic-agent", "tak-agent"],
    capabilities: ["tunnel.tak.status.v1"]
  },
  detach_tak_tunnel: {
    components: ["tic-agent", "tak-agent"],
    capabilities: ["tunnel.tak.detach.v1"]
  },
  prepare_interface: {
    components: ["tic-agent"],
    capabilities: ["interface.create.v1"]
  },
  create_interface: {
    components: ["tic-agent"],
    capabilities: ["interface.create.v1"]
  },
  delete_interface: {
    components: ["tic-agent"],
    capabilities: ["interface.delete.v1"]
  },
  toggle_interface: {
    components: ["tic-agent"],
    capabilities: ["interface.state.v1"]
  },
  update_interface_route_mode: {
    components: ["tic-agent"],
    capabilities: ["interface.route_mode.v1"]
  },
  update_interface_tak_server: {
    components: ["tic-agent"],
    capabilities: ["interface.tak_server.v1"]
  },
  update_interface_exclusion_filters: {
    components: ["tic-agent"],
    capabilities: ["filters.exclusion.v1"]
  },
  toggle_peer: {
    components: ["tic-agent"],
    capabilities: ["peer.state.v1"]
  },
  recreate_peer: {
    components: ["tic-agent"],
    capabilities: ["peer.recreate.v1"]
  },
  delete_peer: {
    components: ["tic-agent"],
    capabilities: ["peer.delete.v1"]
  },
  download_peer_config: {
    components: ["tic-agent"],
    capabilities: ["peer.download.v1"]
  },
  download_interface_bundle: {
    components: ["tic-agent"],
    capabilities: ["peer.download_bundle.v1"]
  },
  update_peer_block_filters: {
    components: ["tic-agent"],
    capabilities: ["filters.block.v1"]
  }
};

module.exports = {
  ACTION_REGISTRY,
  AGENT_CONTRACT_VERSION,
  AGENT_SUPPORTED_CONTRACTS,
  DEFAULT_COMPONENT
};
