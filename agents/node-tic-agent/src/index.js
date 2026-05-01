"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { getActionSpec } = require("./registry");
const { validatePayload } = require("./validation");
const { ok, fail } = require("./response");
const {
  loadState,
  saveState,
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
} = require("./state");
const { createStoredZip } = require("./zip");
const {
  peerConfigPath,
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
  inspectRuntimeEnvironment,
  maybeRunSystemCommands,
  ensureSystemKeyMaterial
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
          command_profile: task.command_profile,
          bootstrap_mode: task.bootstrap_mode,
          packages: task.packages,
          safe_init_packages: task.safe_init_packages,
          full_only_packages: task.full_only_packages,
          commands: task.commands,
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
          command_profile: task.command_profile,
          bootstrap_mode: task.bootstrap_mode,
          packages: task.packages,
          safe_init_packages: task.safe_init_packages,
          full_only_packages: task.full_only_packages,
          commands: task.commands,
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
          command_profile: task.command_profile,
          bootstrap_mode: task.bootstrap_mode,
          packages: task.packages,
          safe_init_packages: task.safe_init_packages,
          full_only_packages: task.full_only_packages,
          commands: task.commands,
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

  if (action === "create_server_backup") {
    return ok({
      filename: "stub-server-snapshot.zip",
      content_type: "application/zip",
      content_base64: placeholderZipBase64("stub-server-snapshot")
    });
  }

  if (action === "verify_server_backup_copy") {
    return ok({
      matches: true,
      message: "Stub snapshot matches"
    });
  }

  if (action === "cleanup_server_backups") {
    return ok({
      deleted_count: 0,
      message: "Stub cleanup completed"
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
