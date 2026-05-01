"use strict";

const childProcess = require("node:child_process");

function shellQuote(value) {
  return `'${String(value || "").replace(/'/g, `'\"'\"'`)}'`;
}

function normalizedValue(value) {
  return String(value == null ? "" : value).trim().toLowerCase();
}

function confirmAccepted(value) {
  return ["y", "yes", "true", "1", "confirm"].includes(normalizedValue(value));
}

function bootstrapTransportMode() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_TRANSPORT || "").trim().toLowerCase();
  if (value === "local" || value === "ssh" || value === "noop") {
    return value;
  }
  return "noop";
}

function localApplyAllowed() {
  return String(process.env.NELOMAI_AGENT_BOOTSTRAP_ALLOW_LOCAL || "").trim() === "1";
}

function sshApplyAllowed() {
  return String(process.env.NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH || "").trim() === "1";
}

function sshStrictHostKeyChecking() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING || "accept-new").trim();
  return value || "accept-new";
}

function sshConnectTimeout() {
  const timeout = Number(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT || 10);
  return Number.isFinite(timeout) && timeout > 0 ? Math.floor(timeout) : 10;
}

function sshKnownHostsFile() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_KNOWN_HOSTS_FILE || "").trim();
  return value || null;
}

function sshpassBinary() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSHPASS_BIN || "sshpass").trim();
  return value || "sshpass";
}

function plinkBinary() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_PLINK_BIN || "plink").trim();
  return value || "plink";
}

function sshPinnedHostKey() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY || "").trim();
  return value || null;
}

function sshAuthMode() {
  const value = String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE || "auto").trim().toLowerCase();
  if (value === "auto" || value === "key" || value === "password") {
    return value;
  }
  return "auto";
}

function commandConfirmRequired() {
  return String(process.env.NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM || "").trim() === "1";
}

function hostKeyConfirmRequired() {
  return String(process.env.NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM || "").trim() === "1";
}

function inputRequired(prompt, key, kind, note = null) {
  return {
    status: "input_required",
    stdout: "",
    stderr: "",
    exit_code: 0,
    input_prompt: prompt,
    input_key: key,
    input_kind: kind,
    note
  };
}

function firstNonEmpty(...values) {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) {
      return text;
    }
  }
  return "";
}

function classifySshExecutionFailure(stdout, stderr, exitCode) {
  const detail = firstNonEmpty(stderr, stdout);
  const normalized = detail.toLowerCase();
  if (normalized.includes("host key is not cached") || normalized.includes("cannot confirm a host key in batch mode")) {
    return {
      error_code: "ssh_host_key_mismatch",
      error_message: `SSH host key verification failed: ${detail}`
    };
  }
  if (normalized.includes("host key verification failed")) {
    return {
      error_code: "ssh_host_key_mismatch",
      error_message: `SSH host key verification failed: ${detail}`
    };
  }
  if (normalized.includes("permission denied")) {
    return {
      error_code: "ssh_auth_failed",
      error_message: `SSH authentication failed: ${detail}`
    };
  }
  if (normalized.includes("connection refused")) {
    return {
      error_code: "ssh_connection_refused",
      error_message: `SSH connection refused: ${detail}`
    };
  }
  if (
    normalized.includes("could not resolve hostname") ||
    normalized.includes("name or service not known") ||
    normalized.includes("temporary failure in name resolution") ||
    normalized.includes("no such host is known") ||
    normalized.includes("no route to host") ||
    normalized.includes("network is unreachable")
  ) {
    return {
      error_code: "ssh_host_unreachable",
      error_message: `SSH host is unreachable: ${detail}`
    };
  }
  if (
    normalized.includes("connection timed out") ||
    normalized.includes("operation timed out") ||
    normalized.includes("timed out")
  ) {
    return {
      error_code: "ssh_timeout",
      error_message: `SSH connection timed out: ${detail}`
    };
  }
  if (normalized.includes("sshpass")) {
    return {
      error_code: "sshpass_missing",
      error_message: `sshpass is required for SSH password bootstrap: ${detail}`
    };
  }
  if (Number(exitCode) === 255) {
    return {
      error_code: "ssh_transport_failed",
      error_message: `SSH transport failed: ${detail || `ssh exited with code ${exitCode}`}`
    };
  }
  return {
    error_code: "ssh_command_failed",
    error_message: detail || `SSH bootstrap command failed with exit code ${exitCode}`
  };
}

function classifyLocalExecutionFailure(stdout, stderr, exitCode) {
  const detail = firstNonEmpty(stderr, stdout);
  return {
    error_code: "local_command_failed",
    error_message: detail || `Local bootstrap command failed with exit code ${exitCode}`
  };
}

function resolvedSshPassword(server = {}, interactive = {}) {
  const interactivePassword = String(interactive.ssh_password || "").trim();
  if (interactivePassword) {
    return interactivePassword;
  }
  const serverPassword = String(server.ssh_password || "").trim();
  return serverPassword || "";
}

function sshpassAvailable() {
  const probe = childProcess.spawnSync(sshpassBinary(), ["-V"], {
    encoding: "utf8"
  });
  if (probe.error) {
    return false;
  }
  return probe.status === 0 || probe.status === 1;
}

function plinkAvailable() {
  const probe = childProcess.spawnSync(plinkBinary(), ["-V"], {
    encoding: "utf8"
  });
  if (probe.error) {
    return false;
  }
  return probe.status === 0;
}

function buildSshArgs(command, context = {}, options = {}) {
  const server = context && context.server ? context.server : {};
  const host = String(server.host || "").trim();
  const login = String(server.ssh_login || "").trim() || "root";
  const sshPort = Number(server.ssh_port) || 22;
  const passwordAuth = Boolean(options.password_auth);
  if (!host) {
    throw new Error("SSH bootstrap transport requires server.host");
  }
  if (!Number.isInteger(sshPort) || sshPort <= 0) {
    throw new Error("SSH bootstrap transport requires valid server.ssh_port");
  }
  const destination = `${login}@${host}`;
  const args = [
    "-o", `StrictHostKeyChecking=${sshStrictHostKeyChecking()}`,
    "-o", `ConnectTimeout=${sshConnectTimeout()}`,
    "-p", String(sshPort)
  ];
  if (passwordAuth) {
    args.unshift("-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no");
  } else {
    args.unshift("-o", "BatchMode=yes");
  }
  const knownHosts = sshKnownHostsFile();
  if (knownHosts) {
    args.push("-o", `UserKnownHostsFile=${knownHosts}`);
  }
  args.push(destination, "--", "bash", "-lc", shellQuote(command));
  return {
    args,
    destination,
    summary: `${passwordAuth ? "sshpass ssh" : "ssh"} ${destination} -p ${sshPort}`
  };
}

function buildPlinkArgs(command, context = {}) {
  const server = context && context.server ? context.server : {};
  const interactive = context && context.interactive ? context.interactive : {};
  const host = String(server.host || "").trim();
  const login = String(server.ssh_login || "").trim() || "root";
  const sshPort = Number(server.ssh_port) || 22;
  const password = resolvedSshPassword(server, interactive);
  const hostKey = sshPinnedHostKey();
  if (!host) {
    throw new Error("Plink bootstrap transport requires server.host");
  }
  if (!Number.isInteger(sshPort) || sshPort <= 0) {
    throw new Error("Plink bootstrap transport requires valid server.ssh_port");
  }
  if (!password) {
    throw new Error("Plink bootstrap transport requires ssh_password");
  }
  if (!hostKey) {
    const error = new Error("Plink bootstrap transport requires NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY");
    error.error_code = "ssh_host_key_mismatch";
    throw error;
  }
  const destination = `${login}@${host}`;
  return {
    args: [
      "-batch",
      "-ssh",
      destination,
      "-P",
      String(sshPort),
      "-pw",
      password,
      "-hostkey",
      hostKey,
      "bash",
      "-lc",
      command
    ],
    destination,
    summary: `plink ${destination} -P ${sshPort}`
  };
}

function buildTransport(transportMode = bootstrapTransportMode()) {
  if (transportMode === "noop") {
    return {
      name: "noop",
      execute(command) {
        return {
          status: "planned",
          stdout: "",
          stderr: "",
          exit_code: 0,
          command
        };
      }
    };
  }

  if (transportMode === "local") {
    return {
      name: "local",
      execute(command) {
        if (!localApplyAllowed()) {
          throw new Error("Local bootstrap execution is blocked until NELOMAI_AGENT_BOOTSTRAP_ALLOW_LOCAL=1 is set");
        }
        const completed = childProcess.spawnSync("bash", ["-lc", command], {
          encoding: "utf8"
        });
        return {
          ...(
            completed.status === 0
              ? {}
              : classifyLocalExecutionFailure(completed.stdout, completed.stderr, completed.status)
          ),
          status: completed.status === 0 ? "completed" : "failed",
          stdout: String(completed.stdout || "").trim(),
          stderr: String(completed.stderr || "").trim(),
          exit_code: Number(completed.status ?? 1),
          command
        };
      }
    };
  }

  if (transportMode === "ssh") {
    return {
      name: "ssh",
      execute(command, context = {}) {
        const server = context && context.server ? context.server : {};
        const interactive = context && context.interactive ? context.interactive : {};
        const stepIndex = Number(context && context.step_index) || 1;
        const host = String(server.host || "").trim();
        const login = String(server.ssh_login || "").trim() || "root";
        const destination = `${login}@${host || "unknown-host"}`;
        const authMode = sshAuthMode();
        const sshPassword = authMode === "key" ? "" : resolvedSshPassword(server, interactive);
        const passwordAuth = authMode === "password" ? true : authMode === "key" ? false : Boolean(sshPassword);

        if (hostKeyConfirmRequired() && !confirmAccepted(interactive.ssh_host_key_confirm)) {
          return inputRequired(
            `Подтвердите новый SSH host key для ${destination}`,
            "ssh_host_key_confirm",
            "confirm",
            `SSH bootstrap waiting for host key confirmation for ${destination}`
          );
        }
        if (authMode !== "key" && !sshPassword) {
          return inputRequired(
            `Введите SSH пароль для ${destination}`,
            "ssh_password",
            "password",
            "Пароль будет использован для SSH bootstrap через sshpass, если он доступен на хосте агента"
          );
        }
        if (commandConfirmRequired()) {
          const commandKey = `bootstrap_step_${stepIndex}_confirm`;
          if (!confirmAccepted(interactive[commandKey])) {
            return inputRequired(
              `Подтвердите выполнение bootstrap шага ${stepIndex}`,
              commandKey,
              "confirm",
              command
            );
          }
        }

        if (!sshApplyAllowed()) {
          const error = new Error("SSH bootstrap execution is blocked until NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH=1 is set");
          error.error_code = "ssh_execution_blocked";
          throw error;
        }
        let spawnCommand = "ssh";
        let spawnArgs = [];
        let summary = "";
        let spawnEnv = process.env;
        if (passwordAuth) {
          if (process.platform === "win32" && plinkAvailable()) {
            const plink = buildPlinkArgs(command, context);
            spawnCommand = plinkBinary();
            spawnArgs = plink.args;
            summary = plink.summary;
          } else {
            if (!sshpassAvailable()) {
              const error = new Error("SSH password bootstrap requires sshpass on the agent host");
              error.error_code = "sshpass_missing";
              throw error;
            }
            const ssh = buildSshArgs(command, context, { password_auth: true });
            spawnCommand = sshpassBinary();
            spawnArgs = ["-e", "ssh", ...ssh.args];
            summary = ssh.summary;
            spawnEnv = { ...process.env, SSHPASS: sshPassword };
          }
        } else {
          const ssh = buildSshArgs(command, context, { password_auth: false });
          spawnCommand = "ssh";
          spawnArgs = ssh.args;
          summary = ssh.summary;
        }
        const completed = childProcess.spawnSync(spawnCommand, spawnArgs, {
          encoding: "utf8",
          env: spawnEnv
        });
        const stdout = String(completed.stdout || "").trim();
        const stderr = String(completed.stderr || "").trim();
        const combined = Array.isArray(completed.output)
          ? completed.output
              .filter((item) => item != null && String(item).trim())
              .map((item) => String(item).trim())
              .join("\n")
          : "";
        const failure = completed.status === 0
          ? {}
          : classifySshExecutionFailure(stdout, stderr || combined, completed.status);
        return {
          ...failure,
          status: completed.status === 0 ? "completed" : "failed",
          stdout,
          stderr: stderr || combined,
          exit_code: Number(completed.status ?? 1),
          command,
          note: summary
        };
      }
    };
  }

  throw new Error(`Unsupported bootstrap transport: ${transportMode}`);
}

module.exports = {
  bootstrapTransportMode,
  buildTransport
};
