"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { getActionSpec } = require("./registry");
const { validatePayload } = require("./validation");
const { ok, fail } = require("./response");
const {
  loadState,
  saveState,
  findTunnelRecord,
  findFirstFreePort,
  findFirstFreeAddress,
  createInterfaceRecord,
  createBootstrapTaskRecord,
  findBootstrapTaskRecord,
  completeBootstrapTaskRecord,
  buildTakTunnelPlan,
  provisionTakTunnelRecord,
  attachTakTunnelRecord,
  detachTakTunnelRecord,
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
} = require("./state");
const { createStoredZip } = require("./zip");
const {
  buildServerSnapshot,
  verifyLocalServerSnapshotCopy,
  cleanupLocalServerSnapshots
} = require("./backup");
const {
  peerConfigPath,
  tunnelDirectory,
  tunnelMetaPath,
  tunnelServerConfigPath,
  tunnelClientConfigPath,
  tunnelClientPayloadPath,
  syncInterfaceArtifacts,
  syncAllPeerArtifacts,
  syncPeerArtifacts,
  removePeerArtifacts,
  collectInterfaceBundleEntries,
  buildCreateInterfaceCommands,
  buildToggleInterfaceCommands,
  buildTogglePeerCommands,
  buildRefreshInterfaceCommands,
  buildRecreatePeerCommands,
  buildDeletePeerCommands,
  buildAttachTunnelCommands,
  buildDetachTunnelCommands,
  inspectRuntimeEnvironment,
  maybeRunSystemCommands,
  ensureSystemKeyMaterial,
  syncTunnelArtifacts,
  inspectTunnelArtifacts,
  removeTunnelArtifacts
} = require("./runtime");

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function writeLogLine(payload) {
  const logPath = process.env.NELOMAI_AGENT_LOG;
  if (!logPath) {
    return;
  }
  const dir = path.dirname(logPath);
  fs.mkdirSync(dir, { recursive: true });
  fs.appendFileSync(logPath, `${JSON.stringify(payload)}\n`, "utf8");
}

function placeholderZipBase64(label) {
  return Buffer.from(`PK\u0003\u0004${label}`, "utf8").toString("base64");
}

function bootstrapExecutionSnapshot(task) {
  const execution = task && task.execution_result && typeof task.execution_result === "object" ? task.execution_result : null;
  const steps = Array.isArray(execution && execution.steps) ? execution.steps : [];
  const lastStep = steps.length > 0 ? steps[steps.length - 1] : null;
  const pendingInput = execution && execution.pending_input && typeof execution.pending_input === "object"
    ? execution.pending_input
    : null;
  const resumeFromStep = Number(task && task.resume_from_step);
  return {
    mode: execution ? execution.mode || null : task?.bootstrap_mode || null,
    transport: execution ? execution.transport || null : null,
    applied: Boolean(execution && execution.applied),
    planned: !(execution && execution.applied),
    command_count: Number(execution && execution.command_count) || (Array.isArray(task?.commands) ? task.commands.length : 0),
    executed_step_count: steps.length,
    current_step_index: lastStep ? Number(lastStep.index) || null : null,
    current_step_status: lastStep ? String(lastStep.status || "") : null,
    resume_from_step: Number.isInteger(resumeFromStep) && resumeFromStep > 0 ? resumeFromStep : 1,
    waiting_for_input: Boolean(task && task.status === "input_required"),
    pending_input: pendingInput ? {
      key: pendingInput.key || null,
      kind: pendingInput.kind || null,
      prompt: pendingInput.prompt || null,
      step_index: Number(pendingInput.step_index) || null,
    } : null,
  };
}

function pathExistsAs(targetPath, kind) {
  if (!targetPath || !fs.existsSync(targetPath)) {
    return false;
  }
  const stat = fs.statSync(targetPath);
  if (kind === "file") {
    return stat.isFile();
  }
  if (kind === "dir") {
    return stat.isDirectory();
  }
  return true;
}

function tunnelArtifactSnapshot(tunnelRecord) {
  if (!tunnelRecord || typeof tunnelRecord !== "object") {
    return {
      directory: null,
      meta_path: null,
      server_config_path: null,
      client_config_path: null,
      client_payload_path: null,
      system_config_path: null,
      system_interface_name: null,
      directory_exists: false,
      meta_exists: false,
      server_config_exists: false,
      client_config_exists: false,
      client_payload_exists: false,
      system_config_exists: false,
      system_interface_exists: false
    };
  }
  return inspectTunnelArtifacts(tunnelRecord);
}

function tunnelStatusEnvelope(state, payload) {
  const component = String(payload.component || process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
  const record = findTunnelRecord(state, payload);
  const artifacts = tunnelArtifactSnapshot(record);
  const requestedTunnelId = String((payload && payload.tunnel_id) || "").trim() || null;
  const serverPayload = payload && payload.server && typeof payload.server === "object" ? payload.server : null;
  const ticServerPayload = payload && payload.tic_server && typeof payload.tic_server === "object" ? payload.tic_server : null;
  const takServerPayload = payload && payload.tak_server && typeof payload.tak_server === "object" ? payload.tak_server : null;
  const inferredTicServerId = Number((ticServerPayload && ticServerPayload.id) || (serverPayload && serverPayload.server_type === "tic" ? serverPayload.id : 0));
  const inferredTakServerId = Number((takServerPayload && takServerPayload.id) || (serverPayload && serverPayload.server_type === "tak" ? serverPayload.id : 0));
  const recordStatus = record ? String(record.status || "").trim().toLowerCase() : "";
  const exists = Boolean(
    record ||
    artifacts.directory_exists ||
    artifacts.meta_exists ||
    artifacts.server_config_exists ||
    artifacts.client_payload_exists
  );
  const localRole = record ? String(record.local_role || "").trim().toLowerCase() : "";
  const isActive =
    localRole === "tic"
      ? Boolean(artifacts.system_interface_exists)
      : ["active", "attached", "provisioned"].includes(recordStatus);
  const computedStatus =
    !exists
      ? "missing"
      : localRole === "tic" && ["active", "attached"].includes(recordStatus) && !artifacts.system_interface_exists
        ? "degraded"
        : record
          ? String(record.status || "present")
          : "present";

  return {
    tunnel_id: record ? String(record.tunnel_id || "") || requestedTunnelId : requestedTunnelId,
    protocol: record ? String(record.protocol || "amneziawg-2.0") : "amneziawg-2.0",
    exists,
    is_active: isActive,
    status: computedStatus,
    component,
    tic_server_id: record
      ? Number(record.tic_server_id) || null
      : Number.isInteger(inferredTicServerId) && inferredTicServerId > 0
        ? inferredTicServerId
        : null,
    tak_server_id: record
      ? Number(record.tak_server_id) || null
      : Number.isInteger(inferredTakServerId) && inferredTakServerId > 0
        ? inferredTakServerId
        : null,
    tic_server_name: record
      ? String(record.tic_server_name || "") || null
      : ticServerPayload
        ? String(ticServerPayload.name || "") || null
        : null,
    tak_server_name: record
      ? String(record.tak_server_name || "") || null
      : takServerPayload
        ? String(takServerPayload.name || "") || null
        : null,
    listen_port: record ? Number(record.listen_port) || null : null,
    network_cidr: record ? String(record.network_cidr || "") || null : null,
    last_handshake_at: record && record.last_handshake_at ? String(record.last_handshake_at) : null,
    last_error: record && record.last_error ? String(record.last_error) : null,
    runtime_artifacts: artifacts
  };
}

function realInterfaceResponse(payload) {
  const action = String(payload.action || "");
  const interfaceActions = new Set([
    "prepare_interface",
    "create_interface",
    "toggle_interface",
    "update_interface_route_mode",
    "update_interface_tak_server",
    "update_interface_exclusion_filters",
    "toggle_peer",
    "update_peer_block_filters",
    "recreate_peer",
    "delete_peer",
    "download_peer_config",
    "download_interface_bundle"
  ]);
  if (!interfaceActions.has(action)) {
    return null;
  }

  const ticServer = payload.tic_server && typeof payload.tic_server === "object" ? payload.tic_server : {};
  const ticServerId = Number(ticServer.id);
  if (!Number.isInteger(ticServerId) || ticServerId <= 0) {
    return fail("Missing valid tic_server.id");
  }

  const state = loadState();
  if (action === "prepare_interface") {
    return ok({
      listen_port: findFirstFreePort(state, ticServerId),
      address_v4: findFirstFreeAddress(state, ticServerId)
    });
  }

  if (action === "create_interface") {
    try {
      const record = createInterfaceRecord(state, payload);
      if (ensureSystemKeyMaterial(record)) {
        saveState(state);
      }
      syncInterfaceArtifacts(record);
      const execution = maybeRunSystemCommands(buildCreateInterfaceCommands(record));
      return ok({
        agent_interface_id: record.agent_interface_id,
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "toggle_interface") {
    try {
      const record = toggleInterfaceRecord(state, payload);
      if (record.is_enabled && ensureSystemKeyMaterial(record)) {
        saveState(state);
      }
      syncInterfaceArtifacts(record);
      const execution = maybeRunSystemCommands(buildToggleInterfaceCommands(record));
      return ok({
        status: "updated",
        interface: {
          panel_interface_id: record.panel_interface_id,
          agent_interface_id: record.agent_interface_id,
          is_enabled: record.is_enabled
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "update_interface_route_mode") {
    try {
      const record = updateInterfaceRouteModeRecord(state, payload);
      if (ensureSystemKeyMaterial(record)) {
        saveState(state);
      }
      syncAllPeerArtifacts(record);
      const execution = maybeRunSystemCommands(buildRefreshInterfaceCommands(record));
      return ok({
        status: "updated",
        interface: {
          panel_interface_id: record.panel_interface_id,
          agent_interface_id: record.agent_interface_id,
          route_mode: record.route_mode
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "update_interface_tak_server") {
    try {
      const record = updateInterfaceTakServerRecord(state, payload);
      if (ensureSystemKeyMaterial(record)) {
        saveState(state);
      }
      syncAllPeerArtifacts(record);
      const execution = maybeRunSystemCommands(buildRefreshInterfaceCommands(record));
      return ok({
        status: "updated",
        interface: {
          panel_interface_id: record.panel_interface_id,
          agent_interface_id: record.agent_interface_id,
          tak_server_id: record.tak_server_id,
          route_mode: record.route_mode
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "update_interface_exclusion_filters") {
    try {
      const record = updateInterfaceExclusionFiltersRecord(state, payload);
      syncInterfaceArtifacts(record);
      return ok({
        status: "updated",
        interface: {
          panel_interface_id: record.panel_interface_id,
          agent_interface_id: record.agent_interface_id,
          exclusion_filters_enabled: record.exclusion_filters_enabled
        }
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "toggle_peer") {
    try {
      const record = togglePeerRecord(state, payload);
      const persisted = ensurePeerRecordPersisted(state, payload);
      if (ensureSystemKeyMaterial(persisted.interfaceRecord)) {
        saveState(state);
      }
      syncPeerArtifacts(persisted.interfaceRecord, persisted.peerRecord);
      const execution = maybeRunSystemCommands(buildTogglePeerCommands(persisted.interfaceRecord, persisted.peerRecord));
      return ok({
        status: "updated",
        peer: {
          panel_peer_id: record.panel_peer_id,
          slot: record.slot,
          is_enabled: record.is_enabled,
          config_exists: record.config_exists
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "update_peer_block_filters") {
    try {
      const record = updatePeerBlockFiltersRecord(state, payload);
      const persisted = ensurePeerRecordPersisted(state, payload);
      syncPeerArtifacts(persisted.interfaceRecord, persisted.peerRecord);
      return ok({
        status: "updated",
        peer: {
          panel_peer_id: record.panel_peer_id,
          slot: record.slot,
          block_filters_enabled: record.block_filters_enabled
        }
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "recreate_peer") {
    try {
      const record = recreatePeerRecord(state, payload);
      const persisted = ensurePeerRecordPersisted(state, payload);
      if (ensureSystemKeyMaterial(persisted.interfaceRecord, { rotate_peer_slots: [record.slot] })) {
        saveState(state);
      }
      syncPeerArtifacts(persisted.interfaceRecord, record);
      const execution = maybeRunSystemCommands(buildRecreatePeerCommands(persisted.interfaceRecord, record));
      return ok({
        status: "recreated",
        peer: {
          panel_peer_id: record.panel_peer_id,
          slot: record.slot,
          config_revision: record.config_revision,
          config_exists: record.config_exists
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "delete_peer") {
    try {
      const persisted = ensurePeerRecordPersisted(state, payload);
      const interfaceRecord = persisted.interfaceRecord;
      const record = deletePeerRecord(state, payload);
      removePeerArtifacts(interfaceRecord, record);
      const execution = maybeRunSystemCommands(buildDeletePeerCommands(interfaceRecord, record));
      return ok({
        status: "deleted",
        peer: {
          panel_peer_id: record.panel_peer_id,
          slot: record.slot
        },
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "download_peer_config") {
    try {
      const { interfaceRecord, peerRecord } = ensurePeerRecordPersisted(state, payload);
      syncPeerArtifacts(interfaceRecord, peerRecord);
      const content = fs.readFileSync(peerConfigPath(interfaceRecord, peerRecord), "utf8");
      return ok({
        filename: `${interfaceRecord.name}-peer-${peerRecord.slot}.conf`,
        content_type: "text/plain; charset=utf-8",
        content_base64: Buffer.from(content, "utf8").toString("base64")
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "download_interface_bundle") {
    try {
      const interfaceRecord = ensureInterfaceRecordPersisted(state, payload);
      syncInterfaceArtifacts(interfaceRecord);
      const peers = Array.isArray(interfaceRecord.peers) ? [...interfaceRecord.peers] : [];
      peers.sort((left, right) => Number(left.slot) - Number(right.slot));
      for (const peerRecord of peers) {
        syncPeerArtifacts(interfaceRecord, peerRecord);
      }
      const entries = collectInterfaceBundleEntries(interfaceRecord);
      entries.push({
        name: "README.txt",
        content: [
          "Nelomai interface bundle placeholder",
          `Interface: ${interfaceRecord.name}`,
          `Agent interface id: ${interfaceRecord.agent_interface_id}`,
          `Peers in bundle: ${peers.length}`
        ].join("\n")
      });

      const archive = createStoredZip(entries);
      return ok({
        filename: `${interfaceRecord.name}.zip`,
        content_type: "application/zip",
        content_base64: archive.toString("base64")
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  return null;
}

function realServerResponse(payload) {
  const action = String(payload.action || "");
  const state = loadState();

  if (action === "provision_tak_tunnel") {
    try {
      const tunnel = provisionTakTunnelRecord(state, payload);
      syncTunnelArtifacts(tunnel);
      return ok({
        status: "provisioned",
        tunnel_id: tunnel.tunnel_id,
        protocol: tunnel.protocol,
        listen_port: tunnel.listen_port,
        network_cidr: tunnel.network_cidr,
        tak_address_v4: tunnel.tak_address_v4,
        tic_address_v4: tunnel.tic_address_v4,
        nat_mode: tunnel.nat_mode,
        tunnel_artifacts: tunnel.tunnel_artifacts,
        amnezia_config: tunnel.amnezia_config
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "attach_tak_tunnel") {
    try {
      const tunnel = attachTakTunnelRecord(state, payload);
      syncTunnelArtifacts(tunnel);
      const execution = maybeRunSystemCommands(buildAttachTunnelCommands(tunnel));
      return ok({
        status: "attached",
        tunnel_id: tunnel.tunnel_id,
        protocol: tunnel.protocol,
        listen_port: tunnel.listen_port,
        network_cidr: tunnel.network_cidr,
        tak_address_v4: tunnel.tak_address_v4,
        tic_address_v4: tunnel.tic_address_v4,
        nat_mode: tunnel.nat_mode,
        tunnel_artifacts: tunnel.tunnel_artifacts,
        amnezia_config: tunnel.amnezia_config,
        execution_mode: execution.mode,
        system_commands_applied: execution.applied
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "verify_tak_tunnel_status") {
    try {
      return ok({
        status: "checked",
        action,
        component: payload.component,
        tunnel_status: tunnelStatusEnvelope(state, payload)
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "detach_tak_tunnel") {
    try {
      const tunnel = detachTakTunnelRecord(state, payload);
      const execution = maybeRunSystemCommands(buildDetachTunnelCommands(tunnel));
      removeTunnelArtifacts(tunnel);
      return ok({
        status: "detached",
        tunnel_id: tunnel.tunnel_id,
        protocol: tunnel.protocol,
        execution_mode: execution.mode,
        system_commands_applied: execution.applied,
        tunnel_status: {
          tunnel_id: tunnel.tunnel_id,
          protocol: tunnel.protocol,
          detached: true,
          is_active: false
        }
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "bootstrap_server") {
    try {
      const { task, serverRecord } = createBootstrapTaskRecord(state, payload);
      return ok({
        status: task.status,
        logs: task.logs,
        task_id: task.id,
        bootstrap_plan: {
          repository_url: task.repository_url,
          os_family: task.os_family,
          os_version: task.os_version,
            install_root: task.install_root,
            service_name: task.service_name,
            environment_file_path: task.environment_file_path,
            command_profile: task.command_profile,
            bootstrap_mode: task.bootstrap_mode,
            packages: task.packages,
            safe_init_packages: task.safe_init_packages,
            full_only_packages: task.full_only_packages,
            commands: task.commands,
            environment_file: task.environment_file,
            systemd_unit: task.systemd_unit,
            execution_result: task.execution_result
        },
        bootstrap_snapshot: bootstrapExecutionSnapshot(task),
        input_prompt: task.input_prompt,
        input_key: task.input_key,
        input_kind: task.input_kind,
        last_error_code: task.last_error_code,
        last_error: task.last_error,
        server: {
          id: serverRecord.server_id,
          name: serverRecord.name,
          server_type: serverRecord.server_type,
          host: serverRecord.host
        }
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "bootstrap_server_status") {
    try {
      const taskId = Number(payload.task_id);
      if (!Number.isInteger(taskId) || taskId <= 0) {
        throw new Error("Missing valid task_id");
      }
      const task = findBootstrapTaskRecord(state, taskId);
      if (!task) {
        throw new Error("Bootstrap task is not known to agent state");
      }
      return ok({
        status: task.status,
        logs: task.logs,
        task_id: task.id,
        bootstrap_plan: {
          repository_url: task.repository_url,
          os_family: task.os_family,
          os_version: task.os_version,
            install_root: task.install_root,
            service_name: task.service_name,
            environment_file_path: task.environment_file_path,
            command_profile: task.command_profile,
            bootstrap_mode: task.bootstrap_mode,
            packages: task.packages,
            safe_init_packages: task.safe_init_packages,
            full_only_packages: task.full_only_packages,
            commands: task.commands,
            environment_file: task.environment_file,
            systemd_unit: task.systemd_unit,
            execution_result: task.execution_result
        },
        bootstrap_snapshot: bootstrapExecutionSnapshot(task),
        input_prompt: task.input_prompt,
        input_key: task.input_key,
        input_kind: task.input_kind,
        last_error_code: task.last_error_code,
        last_error: task.last_error
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "bootstrap_server_input") {
    try {
      const { task, serverRecord } = completeBootstrapTaskRecord(state, payload);
      return ok({
        status: task.status,
        logs: task.logs,
        task_id: task.id,
        bootstrap_plan: {
          repository_url: task.repository_url,
          os_family: task.os_family,
          os_version: task.os_version,
            install_root: task.install_root,
            service_name: task.service_name,
            environment_file_path: task.environment_file_path,
            command_profile: task.command_profile,
            bootstrap_mode: task.bootstrap_mode,
            packages: task.packages,
            safe_init_packages: task.safe_init_packages,
            full_only_packages: task.full_only_packages,
            commands: task.commands,
            environment_file: task.environment_file,
            systemd_unit: task.systemd_unit,
            execution_result: task.execution_result
        },
        bootstrap_snapshot: bootstrapExecutionSnapshot(task),
        input_prompt: task.input_prompt,
        input_key: task.input_key,
        input_kind: task.input_kind,
        last_error_code: task.last_error_code,
        last_error: task.last_error,
        server: serverRecord
          ? {
              id: serverRecord.server_id,
              name: serverRecord.name,
              server_type: serverRecord.server_type,
              host: serverRecord.host
            }
          : null
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "verify_server_status") {
    try {
      const record = refreshServerStatusRecord(state, payload);
      return ok({
        is_active: record.is_active,
        server: {
          id: record.server_id,
          name: record.name,
          server_type: record.server_type,
          current_version: record.current_version
        }
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "verify_server_runtime") {
    try {
      const runtime = inspectRuntimeEnvironment();
      return ok({
        status: runtime.ready ? "ready" : "not_ready",
        runtime
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "check_server_agent_update") {
    try {
      const result = checkServerUpdateRecord(state, payload);
      return ok({
        status: "checked",
        current_version: result.record.current_version,
        latest_version: result.record.latest_version,
        update_available: result.update_available,
        message: result.update_available ? "Update is available" : "No update available"
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "update_server_agent") {
    try {
      const record = applyServerUpdateRecord(state, payload);
      return ok({
        status: "updated",
        current_version: record.current_version,
        latest_version: record.latest_version,
        update_available: false,
        message: "Agent updated"
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "create_server_backup") {
    try {
      const snapshot = buildServerSnapshot(payload.server && typeof payload.server === "object" ? payload.server : null);
      return ok({
        filename: snapshot.filename,
        content_type: snapshot.content_type,
        content_base64: snapshot.content_base64,
        sha256: snapshot.sha256,
        size_bytes: snapshot.size_bytes
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "verify_server_backup_copy") {
    try {
      const result = verifyLocalServerSnapshotCopy(payload.snapshot && typeof payload.snapshot === "object" ? payload.snapshot : null);
      return ok({
        matches: result.matches,
        message: result.message,
        local_snapshot: result.local_snapshot || null
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  if (action === "cleanup_server_backups") {
    try {
      const result = cleanupLocalServerSnapshots(payload.keep_latest_count);
      return ok({
        deleted_count: result.deleted_count,
        message: result.message
      });
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error));
    }
  }

  return null;
}

function stubSuccessResponse(payload) {
  const action = payload.action;
  const realInterfaceResult = realInterfaceResponse(payload);
  if (realInterfaceResult) {
    return realInterfaceResult;
  }

  if (action === "verify_server_status") {
    return ok({
      is_active: true
    });
  }

  if (action === "check_server_agent_update") {
    return ok({
      status: "checked",
      current_version: "0.1.0",
      latest_version: "0.1.0",
      update_available: false,
      message: "No update available in stub mode"
    });
  }

  if (action === "update_server_agent") {
    return ok({
      status: "updated",
      current_version: "0.1.0",
      latest_version: "0.1.0",
      update_available: false,
      message: "Stub update completed"
    });
  }

  if (action === "download_peer_config") {
    return ok({
      filename: "stub-peer.conf",
      content_type: "text/plain; charset=utf-8",
      content_base64: Buffer.from("# stub peer config\n", "utf8").toString("base64")
    });
  }

  if (action === "download_interface_bundle") {
    return ok({
      filename: "stub-interface.zip",
      content_type: "application/zip",
      content_base64: placeholderZipBase64("stub-interface")
    });
  }

  if (action === "bootstrap_server") {
    return ok({
      status: "completed",
      logs: ["Stub bootstrap completed"]
    });
  }

  if (action === "bootstrap_server_status") {
    return ok({
      status: "completed",
      logs: ["Stub bootstrap status completed"]
    });
  }

  if (action === "bootstrap_server_input") {
    return ok({
      status: "completed",
      logs: ["Stub bootstrap input accepted"]
    });
  }

  return ok({});
}

function notImplementedResponse(payload) {
  const action = String(payload.action || "");
  const spec = getActionSpec(action);
  return fail(`Action ${action} is recognized but not implemented yet`, {
    status: "not_implemented",
    action,
    component: payload.component,
    expected_capabilities: spec ? spec.capabilities : []
  });
}

async function main() {
  const raw = await readStdin();
  let payload;
  try {
    payload = JSON.parse(raw || "{}");
  } catch (error) {
    process.stdout.write(`${JSON.stringify(fail("Invalid JSON payload"))}\n`);
    process.exitCode = 1;
    return;
  }

  writeLogLine(payload);

  const validationError = validatePayload(payload);
  if (validationError) {
    process.stdout.write(`${JSON.stringify(fail(validationError))}\n`);
    process.exitCode = 1;
    return;
  }

  const implementedResponse = realInterfaceResponse(payload);
  if (implementedResponse) {
    process.stdout.write(`${JSON.stringify(implementedResponse)}\n`);
    process.exitCode = implementedResponse.ok ? 0 : 1;
    return;
  }

  const implementedServerResponse = realServerResponse(payload);
  if (implementedServerResponse) {
    process.stdout.write(`${JSON.stringify(implementedServerResponse)}\n`);
    process.exitCode = implementedServerResponse.ok ? 0 : 1;
    return;
  }

  const stubMode = process.env.NELOMAI_AGENT_STUB_MODE === "success";
  const response = stubMode ? stubSuccessResponse(payload) : notImplementedResponse(payload);
  process.stdout.write(`${JSON.stringify(response)}\n`);
  process.exitCode = response.ok ? 0 : 1;
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify(fail(`Unhandled agent error: ${error instanceof Error ? error.message : String(error)}`))}\n`);
  process.exitCode = 1;
});
