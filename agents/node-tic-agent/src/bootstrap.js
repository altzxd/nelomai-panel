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

function environmentFilePath(serverRecord) {
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  return `/etc/default/nelomai-${type}-agent`;
}

function sshHardeningConfigPath() {
  return "/etc/ssh/sshd_config.d/90-nelomai-hardening.conf";
}

function thirdPartyRoot() {
  return `${installRoot()}/third_party`;
}

function amneziawgToolsRepositoryUrl() {
  return process.env.NELOMAI_AMNEZIAWG_TOOLS_REPO || "https://github.com/amnezia-vpn/amneziawg-tools.git";
}

function amneziawgGoRepositoryUrl() {
  return process.env.NELOMAI_AMNEZIAWG_GO_REPO || "https://github.com/amnezia-vpn/amneziawg-go.git";
}

function bootstrapCommandProfile() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE || "safe-init").trim().toLowerCase();
  if (value === "safe-init" || value === "full") {
    return value;
  }
  return "safe-init";
}

function bootstrapAdminPublicKey() {
  return String(process.env.NELOMAI_AGENT_BOOTSTRAP_ADMIN_PUBKEY || "").trim();
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
    "build-essential",
    "ca-certificates",
    "curl",
    "git",
    "jq",
    "python3",
    "tar",
    "unzip",
    "ufw",
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

function safeInitPackageList(serverRecord) {
  const serverType = String(serverRecord && serverRecord.server_type || "").trim().toLowerCase();
  const commonPackages = [
    "bash",
    "build-essential",
    "ca-certificates",
    "curl",
    "git",
    "iproute2",
    "iptables",
    "jq",
    "nftables",
    "python3",
    "tar",
    "unzip",
    "ufw",
    "wireguard",
    "wireguard-tools",
    "zip"
  ];
  if (serverType === "storage") {
    return [
      "bash",
      "build-essential",
      "ca-certificates",
      "curl",
      "git",
      "jq",
      "python3",
      "tar",
      "unzip",
      "ufw",
      "zip"
    ];
  }
  return commonPackages;
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

function renderEnvironmentFile(serverRecord) {
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  const root = installRoot();
  const lines = [
    `NELOMAI_AGENT_COMPONENT=${type}-agent`,
    `NELOMAI_AGENT_STATE_FILE=${root}/state/${type}-agent-state.json`,
    `NELOMAI_AGENT_DAEMON_STATUS_FILE=${root}/state/${type}-agent-daemon-status.json`,
    `NELOMAI_AGENT_RUNTIME_ROOT=${root}/runtime/${type}`,
    "NELOMAI_AGENT_EXEC_MODE=system",
  ];
  if (type === "tak") {
    lines.push(`NELOMAI_AMNEZIAWG_TOOL_CMD=/usr/bin/python3 ${root}/current/scripts/official_amnezia_tool_bridge.py`);
  }
  if (type === "tic") {
    lines.push("NELOMAI_AGENT_TUNNEL_QUICK_CMD=/usr/bin/awg-quick");
    lines.push("NELOMAI_AGENT_TUNNEL_USERSPACE_IMPLEMENTATION=/usr/local/bin/amneziawg-go");
  }
  return `${lines.join("\n")}\n`;
}

function renderSshHardeningConfig() {
  return [
    "PubkeyAuthentication yes",
    "PasswordAuthentication no",
    "KbdInteractiveAuthentication no",
    "PermitRootLogin prohibit-password",
    "X11Forwarding no",
    "AllowTcpForwarding no",
    "MaxAuthTries 3",
    "LoginGraceTime 30"
  ].join("\n");
}

function firewallRules(serverRecord) {
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  if (type === "tak") {
    return [
      "22/tcp",
      "40404/udp",
      "42001/udp"
    ];
  }
  if (type === "tic") {
    return [
      "22/tcp",
      "40404/udp",
      "10001:10007/udp"
    ];
  }
  return [];
}

function buildBootstrapPlan(serverRecord, payload) {
  const repoUrl = repositoryUrl(payload);
  const root = installRoot();
  const type = String(serverRecord && serverRecord.server_type || "tic").trim().toLowerCase();
  const packages = bootstrapPackageList(serverRecord);
  const safeInitPackages = safeInitPackageList(serverRecord);
  const fullOnlyPackages = additionalFullPackages(packages, safeInitPackages);
  const svcName = serviceName(serverRecord);
  const svcPath = `/etc/systemd/system/${svcName}`;
  const envPath = environmentFilePath(serverRecord);
  const thirdParty = thirdPartyRoot();
  const awgToolsDir = `${thirdParty}/amneziawg-tools`;
  const awgGoDir = `${thirdParty}/amneziawg-go`;
  const needsAmneziaRuntime = type === "tic" || type === "tak";
  const adminPublicKey = bootstrapAdminPublicKey();
  const sshConfigPath = sshHardeningConfigPath();
  const sshHardeningConfig = renderSshHardeningConfig();
  const ufwRules = firewallRules(serverRecord);
  const unitContent = renderSystemdUnit(serverRecord);
  const envContent = renderEnvironmentFile(serverRecord);
  const amneziaRuntimeCommands = needsAmneziaRuntime ? [
    `install -d -m 755 ${thirdParty}`,
    "GO_VERSION=$(curl -fsSL https://go.dev/VERSION?m=text | head -n 1 | tr -d '\\r'); echo \"$GO_VERSION\" > /tmp/nelomai-go-version",
    "GO_VERSION=$(cat /tmp/nelomai-go-version); curl -fsSL \"https://dl.google.com/go/${GO_VERSION}.linux-amd64.tar.gz\" -o /tmp/nelomai-go.tar.gz",
    "rm -rf /usr/local/go",
    "tar -C /usr/local -xzf /tmp/nelomai-go.tar.gz",
    "/usr/local/go/bin/go version",
    `if [ ! -d ${shellQuote(`${awgToolsDir}/.git`)} ]; then rm -rf ${shellQuote(awgToolsDir)} && git clone ${shellQuote(amneziawgToolsRepositoryUrl())} ${shellQuote(awgToolsDir)}; else git -C ${shellQuote(awgToolsDir)} pull --ff-only; fi`,
    `make -C ${shellQuote(`${awgToolsDir}/src`)}`,
    `make -C ${shellQuote(`${awgToolsDir}/src`)} install PREFIX=/usr WITH_WGQUICK=yes WITH_SYSTEMDUNITS=yes`,
    `if [ ! -d ${shellQuote(`${awgGoDir}/.git`)} ]; then rm -rf ${shellQuote(awgGoDir)} && git clone ${shellQuote(amneziawgGoRepositoryUrl())} ${shellQuote(awgGoDir)}; else git -C ${shellQuote(awgGoDir)} pull --ff-only; fi`,
    `cd ${shellQuote(awgGoDir)} && PATH=/usr/local/go/bin:$PATH make`,
    `install -m 755 ${shellQuote(`${awgGoDir}/amneziawg-go`)} /usr/local/bin/amneziawg-go`,
  ] : [];
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
    "apt-get upgrade -y",
    `apt-get install -y ${safeInitPackages.join(" ")}`,
    "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
    "apt-get install -y nodejs",
    ...amneziaRuntimeCommands,
    "command -v python3",
    "command -v node",
    "command -v npm",
    "command -v wg",
    `command -v ${needsAmneziaRuntime ? "awg-quick" : "wg-quick"}`,
    ...(needsAmneziaRuntime ? [
      "command -v awg",
      "command -v amneziawg-go"
    ] : []),
    `install -d -m 755 ${root}`,
    `install -d -m 755 ${root}/releases`,
    `install -d -m 755 ${root}/current`,
    `install -d -m 700 ${root}/runtime/${type}`,
    `install -d -m 700 ${root}/state`,
    `if [ ! -d ${shellQuote(`${root}/current/.git`)} ]; then rm -rf ${shellQuote(`${root}/current`)} && git clone ${shellQuote(repoUrl)} ${shellQuote(`${root}/current`)}; else git -C ${shellQuote(`${root}/current`)} pull --ff-only; fi`,
    `cd ${shellQuote(`${root}/current/agents/node-tic-agent`)} && npm install --omit=dev`,
    `cat > ${shellQuote(envPath)} <<'ENV'\n${envContent}ENV`,
    `chmod 600 ${shellQuote(envPath)}`,
    ...(adminPublicKey ? [
      "install -d -m 700 /root/.ssh",
      `touch ${shellQuote("/root/.ssh/authorized_keys")}`,
      `grep -qxF ${shellQuote(adminPublicKey)} ${shellQuote("/root/.ssh/authorized_keys")} || printf '%s\\n' ${shellQuote(adminPublicKey)} >> ${shellQuote("/root/.ssh/authorized_keys")}`,
      `chmod 600 ${shellQuote("/root/.ssh/authorized_keys")}`
    ] : []),
    `cat > ${shellQuote(sshConfigPath)} <<'SSHD'\n${sshHardeningConfig}\nSSHD`,
    "sshd -t",
    "systemctl restart ssh",
    `cat > ${shellQuote(svcPath)} <<'UNIT'\n${unitContent}\nUNIT`,
    "find /etc/wireguard -type f \\( -name '*.conf' -o -name '*.key' -o -name '*private*' \\) -exec chmod 600 {} + 2>/dev/null || true",
    "install -d -m 700 /etc/amnezia/amneziawg 2>/dev/null || true",
    "find /etc/amnezia/amneziawg -type f \\( -name '*.conf' -o -name '*.key' -o -name '*private*' \\) -exec chmod 600 {} + 2>/dev/null || true",
    ...(ufwRules.length > 0 ? [
      "ufw --force reset",
      "ufw default deny incoming",
      "ufw default allow outgoing",
      ...ufwRules.map((rule) => `ufw allow ${rule}`),
      "ufw --force enable",
      "ufw status"
    ] : []),
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
    `Bootstrap command profile: ${commandProfile}`,
    `SSH hardening: ${needsAmneziaRuntime ? "enabled" : "disabled"}`,
    `Firewall rules: ${ufwRules.join(", ") || "none"}`
  ];
  return {
    os_family: "ubuntu",
    os_version: "22.04",
    repository_url: repoUrl,
    install_root: root,
    service_name: svcName,
    service_path: svcPath,
    environment_file_path: envPath,
    command_profile: commandProfile,
    packages,
    safe_init_packages: safeInitPackages,
    full_only_packages: fullOnlyPackages,
    commands,
    environment_file: envContent,
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
