"use strict";

const { bootstrapTransportMode, buildTransport } = require("./transport");

class BootstrapExecutionError extends Error {
  constructor(message, executionResult, errorCode = null) {
    super(message);
    this.name = "BootstrapExecutionError";
    this.execution_result = executionResult;
    this.error_code = errorCode;
  }
}

function shellQuote(value) {
  return `'${String(value || "").replace(/'/g, `'\"'\"'`)}'`;
}

function serviceName(serverRecord) {
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  return `nelomai-${type}-agent.service`;
}

function installRoot() {
  return process.env.NELOMAI_AGENT_INSTALL_ROOT || "/opt/nelomai";
}

function bootstrapCommandProfile() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE || "safe-init").trim().toLowerCase();
  if (value === "safe-init" || value === "full") {
    return value;
  }
  return "safe-init";
}

function repositoryUrl(payload) {
  const direct = payload && payload.repository_url != null ? String(payload.repository_url).trim() : "";
  if (direct) {
    return direct;
  }
  return process.env.NELOMAI_AGENT_REPOSITORY_URL || "https://github.com/example/nelomai.git";
}

function bootstrapPackageList(serverRecord) {
  const serverType = String(serverRecord && serverRecord.server_type || "").trim().toLowerCase();
  const basePackages = [
    "bash",
    "ca-certificates",
    "curl",
    "git",
    "jq",
    "tar",
    "unzip",
    "zip"
  ];
  const networkRuntimePackages = [
    "iproute2",
    "iptables",
    "nftables"
  ];
  const wireguardPackages = [
    "wireguard",
    "wireguard-tools"
  ];
  if (serverType === "storage") {
    return basePackages;
  }
  if (serverType === "tic" || serverType === "tak") {
    return [...basePackages, ...networkRuntimePackages, ...wireguardPackages];
  }
  return [...basePackages, ...networkRuntimePackages, ...wireguardPackages];
}

function safeInitPackageList() {
  return [
    "ca-certificates",
    "curl",
    "git",
    "iproute2",
    "iptables",
    "jq",
    "nftables",
    "tar",
    "unzip",
    "wireguard",
    "wireguard-tools",
    "zip"
  ];
}

function additionalFullPackages(packages, safeInitPackages) {
  const safeSet = new Set(safeInitPackages.map((item) => String(item).trim()));
  return packages.filter((item) => !safeSet.has(String(item).trim()));
}

function renderSystemdUnit(serverRecord) {
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  const root = installRoot();
  const stateFile = `${root}/state/${type}-agent-state.json`;
  const envFile = `/etc/default/nelomai-${type}-agent`;
  return [
    "[Unit]",
    `Description=Nelomai ${type.toUpperCase()} Node Agent`,
    "After=network-online.target",
    "Wants=network-online.target",
    "",
    "[Service]",
    "Type=simple",
    `WorkingDirectory=${root}/current/agents/node-tic-agent`,
    `ExecStart=/usr/bin/node ${root}/current/agents/node-tic-agent/src/daemon.js`,
    "Restart=always",
    "RestartSec=3",
    `EnvironmentFile=-${envFile}`,
    `Environment=NELOMAI_AGENT_COMPONENT=${type}-agent`,
    `Environment=NELOMAI_AGENT_STATE_FILE=${stateFile}`,
    `Environment=NELOMAI_AGENT_DAEMON_STATUS_FILE=${root}/state/${type}-agent-daemon-status.json`,
    `Environment=NELOMAI_AGENT_RUNTIME_ROOT=${root}/runtime/${type}`,
    "Environment=NELOMAI_AGENT_EXEC_MODE=system",
    "User=root",
    "",
    "[Install]",
    "WantedBy=multi-user.target",
    ""
  ].join("\n");
}

function buildBootstrapPlan(serverRecord, payload) {
  const repoUrl = repositoryUrl(payload);
  const root = installRoot();
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  const packages = bootstrapPackageList(serverRecord);
  const safeInitPackages = safeInitPackageList();
  const fullOnlyPackages = additionalFullPackages(packages, safeInitPackages);
  const svcName = serviceName(serverRecord);
  const svcPath = `/etc/systemd/system/${svcName}`;
  const unitContent = renderSystemdUnit(serverRecord);
  const safeInitCommands = [
    "export DEBIAN_FRONTEND=noninteractive",
    "uname -a",
    "cat /etc/os-release",
    "id -u",
    "command -v bash",
    "command -v apt-get",
    "command -v ip",
    "command -v systemctl",
    "command -v wg >/dev/null 2>&1 || echo 'wg-not-installed-yet'",
    "apt-get update",
    `apt-get install -y ${safeInitPackages.join(" ")}`,
    "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
    "apt-get install -y nodejs",
    `install -d -m 755 ${root}`,
    `install -d -m 755 ${root}/releases`,
    `install -d -m 755 ${root}/current`,
    `install -d -m 700 ${root}/runtime/${type}`,
    `install -d -m 700 ${root}/state`,
    `if [ ! -d ${shellQuote(`${root}/current/.git`)} ]; then rm -rf ${shellQuote(`${root}/current`)} && git clone ${shellQuote(repoUrl)} ${shellQuote(`${root}/current`)}; else git -C ${shellQuote(`${root}/current`)} pull --ff-only; fi`,
    `cd ${shellQuote(`${root}/current/agents/node-tic-agent`)} && npm install --omit=dev`,
    `cat > ${shellQuote(svcPath)} <<'UNIT'\n${unitContent}\nUNIT`,
    "systemctl daemon-reload",
    `systemctl enable ${svcName}`,
    `systemctl restart ${svcName}`,
    `systemctl --no-pager --full status ${svcName}`
  ];
  const fullCommands = [
    ...safeInitCommands,
    ...(fullOnlyPackages.length > 0 ? [`apt-get install -y ${fullOnlyPackages.join(" ")}`] : [])
  ];
  const commandProfile = bootstrapCommandProfile();
  const commands = commandProfile === "full"
    ? fullCommands
    : safeInitCommands;
  const summary = [
    `Ubuntu 22.04 bootstrap plan for ${serverRecord.name}`,
    `Repository: ${repoUrl}`,
    `Packages: ${packages.join(", ")}`,
    `Safe-init packages: ${safeInitPackages.join(", ")}`,
    `Full-only packages: ${fullOnlyPackages.join(", ") || "none"}`,
    `Install root: ${root}`,
    `Service: ${svcName}`,
    `Bootstrap command profile: ${commandProfile}`
  ];
  return {
    os_family: "ubuntu",
    os_version: "22.04",
    repository_url: repoUrl,
    install_root: root,
    service_name: svcName,
    service_path: svcPath,
    command_profile: commandProfile,
    packages,
    safe_init_packages: safeInitPackages,
    full_only_packages: fullOnlyPackages,
    commands,
    systemd_unit: unitContent,
    summary
  };
}

function bootstrapExecutionMode() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_MODE || "dry-run").trim().toLowerCase();
  return value === "apply" ? "apply" : "dry-run";
}

function executeBootstrapPlan(plan, options = {}) {
  const mode = options.mode || bootstrapExecutionMode();
  const transportMode = options.transport || bootstrapTransportMode();
  const steps = Array.isArray(plan && plan.commands) ? plan.commands : [];
  const startIndex = Number(options.start_index) > 0 ? Math.floor(Number(options.start_index)) : 1;
  const interactive = options.inputs && typeof options.inputs === "object" ? options.inputs : {};
  const transport = buildTransport(transportMode);
  const result = {
    mode,
    transport: transport.name,
    applied: false,
    command_count: steps.length,
    logs: [],
    steps: [],
    start_index: startIndex
  };
  if (mode !== "apply") {
    result.logs.push(`Bootstrap executor mode: ${mode}`);
    result.logs.push(`Bootstrap transport: ${transport.name}`);
    result.logs.push("Dry-run only; commands were not executed.");
    result.steps = steps.map((command, index) => ({
      index: index + 1,
      command,
      status: "planned",
      transport: transport.name
    }));
    return result;
  }
  result.logs.push("Bootstrap executor mode: apply");
  result.logs.push(`Bootstrap transport: ${transport.name}`);
  for (let index = startIndex - 1; index < steps.length; index += 1) {
    const command = steps[index];
    const stepNumber = index + 1;
    let completed;
    try {
      completed = transport.execute(command, {
        plan,
        server: options.server || null,
        interactive,
        step_index: stepNumber
      });
    } catch (error) {
      const errorCode = String(error && typeof error === "object" && error.error_code ? error.error_code : "bootstrap_transport_exception");
      const errorMessage = error instanceof Error ? error.message : String(error);
      result.steps.push({
        index: stepNumber,
        command,
        transport: transport.name,
        status: "failed",
        error_code: errorCode,
        stdout: "",
        stderr: errorMessage,
        note: null
      });
      result.logs.push(`FAIL step ${stepNumber}: ${command}`);
      result.logs.push(errorMessage);
      result.last_error_code = errorCode;
      throw new BootstrapExecutionError(`[${errorCode}] ${errorMessage}`, result, errorCode);
    }
    const status = String(completed.status || "failed");
    const stdout = String(completed.stdout || "").trim();
    const stderr = String(completed.stderr || "").trim();
    result.steps.push({
      index: stepNumber,
      command,
      transport: transport.name,
      status,
      error_code: completed.error_code || null,
      stdout,
      stderr,
      note: completed.note || null
    });
    result.logs.push(`${status === "completed" ? "OK" : status === "input_required" ? "WAIT" : "FAIL"} step ${stepNumber}: ${command}`);
    if (completed.note) {
      result.logs.push(String(completed.note));
    }
    if (status === "input_required") {
      result.pending_input = {
        prompt: String(completed.input_prompt || "Additional input is required"),
        key: String(completed.input_key || ""),
        kind: String(completed.input_kind || "text"),
        note: completed.note || null,
        step_index: stepNumber
      };
      result.resume_from_step = stepNumber;
      result.logs.push(`Input required: ${result.pending_input.prompt}`);
      return result;
    }
    if (stdout) {
      result.logs.push(stdout);
    }
    if (stderr) {
      result.logs.push(stderr);
    }
    if (status !== "completed") {
      const errorCode = String(completed.error_code || "bootstrap_step_failed");
      result.last_error_code = errorCode;
      throw new BootstrapExecutionError(
        `[${errorCode}] ${stderr || stdout || completed.note || `Bootstrap command failed at step ${stepNumber}`}`,
        result,
        errorCode
      );
    }
  }
  result.applied = true;
  return result;
}

module.exports = {
  buildBootstrapPlan,
  bootstrapCommandProfile,
  bootstrapExecutionMode,
  bootstrapPackageList,
  executeBootstrapPlan,
  renderSystemdUnit
};
