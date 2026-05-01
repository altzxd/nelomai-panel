"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const { createStoredZip } = require("./zip");

function componentName() {
  return String(process.env.NELOMAI_AGENT_COMPONENT || "tic-agent").trim() || "tic-agent";
}

function deployedStateRoot() {
  const nodeAgentRoot = path.resolve(__dirname, "..");
  const agentsRoot = path.dirname(nodeAgentRoot);
  const currentRoot = path.dirname(agentsRoot);
  if (path.basename(currentRoot) !== "current") {
    return null;
  }
  return path.join(path.dirname(currentRoot), "state");
}

function stateFilePath() {
  const explicit = String(process.env.NELOMAI_AGENT_STATE_FILE || "").trim();
  if (explicit) {
    return explicit;
  }
  const deployedRoot = deployedStateRoot();
  if (deployedRoot) {
    return path.join(deployedRoot, `${componentName()}-state.json`);
  }
  return path.join(__dirname, "..", ".data", "state.json");
}

function daemonStatusFilePath() {
  const explicit = String(process.env.NELOMAI_AGENT_DAEMON_STATUS_FILE || "").trim();
  if (explicit) {
    return explicit;
  }
  const deployedRoot = deployedStateRoot();
  if (deployedRoot) {
    return path.join(deployedRoot, `${componentName()}-daemon-status.json`);
  }
  const stateDir = path.dirname(stateFilePath());
  const base = componentName().replace(/[^a-z0-9._-]+/gi, "-");
  return path.join(stateDir, `${base}-daemon-status.json`);
}

function runtimeRoot() {
  return process.env.NELOMAI_AGENT_RUNTIME_ROOT || path.join(__dirname, "..", ".runtime");
}

function systemWireGuardRoot() {
  return process.env.NELOMAI_AGENT_SYSTEM_WG_ROOT || "/etc/wireguard";
}

function backupRoot() {
  const explicit = String(process.env.NELOMAI_AGENT_BACKUP_ROOT || "").trim();
  if (explicit) {
    return explicit;
  }
  const deployedRoot = deployedStateRoot();
  if (deployedRoot) {
    return path.join(deployedRoot, "backups", componentName());
  }
  return path.join(__dirname, "..", ".backups", componentName());
}

function ensureDir(targetPath) {
  fs.mkdirSync(targetPath, { recursive: true });
}

function safeName(value) {
  return String(value || "server")
    .trim()
    .replace(/[^a-z0-9._-]+/gi, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase() || "server";
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function timestampLabel(date = new Date()) {
  const parts = [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, "0"),
    String(date.getUTCDate()).padStart(2, "0"),
    String(date.getUTCHours()).padStart(2, "0"),
    String(date.getUTCMinutes()).padStart(2, "0"),
    String(date.getUTCSeconds()).padStart(2, "0")
  ];
  return `${parts[0]}${parts[1]}${parts[2]}-${parts[3]}${parts[4]}${parts[5]}`;
}

function collectFileEntries(sourcePath, prefix, entries) {
  if (!fs.existsSync(sourcePath)) {
    return;
  }
  const stat = fs.statSync(sourcePath);
  if (stat.isFile()) {
    entries.push({
      name: prefix,
      content: fs.readFileSync(sourcePath)
    });
    return;
  }
  if (!stat.isDirectory()) {
    return;
  }
  const walk = (baseDir, relativePrefix = "") => {
    for (const fileName of fs.readdirSync(baseDir).sort()) {
      const fullPath = path.join(baseDir, fileName);
      const childStat = fs.statSync(fullPath);
      const relativeName = relativePrefix ? `${relativePrefix}/${fileName}` : fileName;
      if (childStat.isDirectory()) {
        walk(fullPath, relativeName);
        continue;
      }
      if (!childStat.isFile()) {
        continue;
      }
      entries.push({
        name: `${prefix}/${relativeName}`,
        content: fs.readFileSync(fullPath)
      });
    }
  };
  walk(sourcePath);
}

function collectWireGuardEntries(state, entries) {
  const root = systemWireGuardRoot();
  const interfaces = Array.isArray(state && state.interfaces) ? state.interfaces : [];
  for (const interfaceRecord of interfaces) {
    if (!interfaceRecord || typeof interfaceRecord !== "object") {
      continue;
    }
    const interfaceId = String(interfaceRecord.agent_interface_id || "").trim();
    if (!interfaceId) {
      continue;
    }
    collectFileEntries(path.join(root, `${interfaceId}.conf`), `wireguard/${interfaceId}.conf`, entries);
    collectFileEntries(path.join(root, "peers", interfaceId), `wireguard/peers/${interfaceId}`, entries);
  }
}

function loadStateFromDisk() {
  const filePath = stateFilePath();
  if (!fs.existsSync(filePath)) {
    return { interfaces: [], servers: [], bootstrap_tasks: [] };
  }
  const raw = fs.readFileSync(filePath, "utf8").trim();
  if (!raw) {
    return { interfaces: [], servers: [], bootstrap_tasks: [] };
  }
  const parsed = JSON.parse(raw);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : { interfaces: [], servers: [], bootstrap_tasks: [] };
}

function buildServerSnapshot(serverPayload) {
  const state = loadStateFromDisk();
  const createdAt = new Date();
  const entries = [];

  collectFileEntries(stateFilePath(), "state/agent-state.json", entries);
  collectFileEntries(daemonStatusFilePath(), "state/daemon-status.json", entries);
  collectFileEntries(runtimeRoot(), "runtime", entries);
  collectWireGuardEntries(state, entries);

  const manifest = {
    created_at: createdAt.toISOString(),
    component: componentName(),
    server: serverPayload || null,
    runtime_root: runtimeRoot(),
    wireguard_root: systemWireGuardRoot(),
    state_file: stateFilePath(),
    daemon_status_file: daemonStatusFilePath(),
    interface_count: Array.isArray(state.interfaces) ? state.interfaces.length : 0
  };
  entries.push({
    name: "manifest.json",
    content: JSON.stringify(manifest, null, 2)
  });

  const archive = createStoredZip(entries);
  const filename = `${Number(serverPayload && serverPayload.id) || 0}-${safeName(serverPayload && serverPayload.name)}-snapshot-${timestampLabel(createdAt)}.zip`;
  const digest = sha256(archive);
  const storageDir = backupRoot();
  ensureDir(storageDir);
  const storagePath = path.join(storageDir, filename);
  fs.writeFileSync(storagePath, archive);
  fs.writeFileSync(`${storagePath}.json`, JSON.stringify({
    filename,
    sha256: digest,
    size_bytes: archive.length,
    created_at: createdAt.toISOString(),
    component: componentName()
  }, null, 2));

  return {
    filename,
    content_type: "application/zip",
    content_base64: archive.toString("base64"),
    sha256: digest,
    size_bytes: archive.length,
    storage_path: storagePath
  };
}

function findLocalSnapshot(snapshot) {
  const filename = String(snapshot && snapshot.filename || "").trim();
  if (!filename) {
    return null;
  }
  const baseName = path.basename(filename);
  const storagePath = path.join(backupRoot(), baseName);
  if (!fs.existsSync(storagePath) || !fs.statSync(storagePath).isFile()) {
    return null;
  }
  const buffer = fs.readFileSync(storagePath);
  return {
    filename: baseName,
    storage_path: storagePath,
    sha256: sha256(buffer),
    size_bytes: buffer.length
  };
}

function verifyLocalServerSnapshotCopy(snapshot) {
  const local = findLocalSnapshot(snapshot);
  if (!local) {
    return {
      matches: false,
      message: "snapshot copy is missing on server"
    };
  }
  const expectedSha = String(snapshot && snapshot.sha256 || "").trim();
  const expectedSize = Number(snapshot && snapshot.size_bytes);
  const sameSha = !!expectedSha && local.sha256 === expectedSha;
  const sameSize = Number.isFinite(expectedSize) && expectedSize > 0 && local.size_bytes === expectedSize;
  return {
    matches: sameSha && sameSize,
    message: sameSha && sameSize ? "snapshot matches local server copy" : "snapshot differs from local server copy",
    local_snapshot: local
  };
}

function cleanupLocalServerSnapshots(keepLatestCount) {
  const keepCount = Number.isInteger(Number(keepLatestCount)) ? Math.max(0, Number(keepLatestCount)) : 0;
  const root = backupRoot();
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    return {
      deleted_count: 0,
      message: "no local server backups to delete"
    };
  }
  const backups = fs.readdirSync(root)
    .filter((name) => name.toLowerCase().endsWith(".zip"))
    .map((name) => {
      const fullPath = path.join(root, name);
      const stat = fs.statSync(fullPath);
      return {
        name,
        path: fullPath,
        mtime_ms: stat.mtimeMs
      };
    })
    .sort((left, right) => right.mtime_ms - left.mtime_ms || right.name.localeCompare(left.name));

  const toDelete = backups.slice(keepCount);
  for (const item of toDelete) {
    if (fs.existsSync(item.path)) {
      fs.unlinkSync(item.path);
    }
    const metadataPath = `${item.path}.json`;
    if (fs.existsSync(metadataPath) && fs.statSync(metadataPath).isFile()) {
      fs.unlinkSync(metadataPath);
    }
  }
  return {
    deleted_count: toDelete.length,
    message: toDelete.length > 0 ? "old server backups deleted" : "no old server backups deleted"
  };
}

module.exports = {
  buildServerSnapshot,
  verifyLocalServerSnapshotCopy,
  cleanupLocalServerSnapshots
};
