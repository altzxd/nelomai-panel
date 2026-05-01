# Nelomai Node-agent Live Validation

This document fixes the first live validation flow for the Node Tic-agent on a
real blank Ubuntu 22.04 host.

## 1. Goal

The purpose of the first live validation is not to prove the whole production
story at once. It is to confirm that the current `safe-init` profile works
against a real remote host and that the panel can observe the result through
the existing bootstrap/job/diagnostics flow.

## 2. Target Host

Use a real host with these properties:

- Ubuntu 22.04;
- blank or near-blank machine;
- reachable over SSH;
- no assumption of preinstalled Node.js, WireGuard, `iproute2`, `iptables`,
  `nftables`, `git`, `curl`, or archive tooling.

## 3. Validation Scope

For the first live pass, validate only `safe-init`.

Expected `safe-init` outcomes:

- SSH transport reaches the host;
- `apt-get update` works;
- base packages are installed;
- networking packages are installed;
- WireGuard packages are installed;
- Node.js is installed;
- repository rollout works;
- `npm install --omit=dev` works for `agents/node-tic-agent`;
- systemd unit is written, enabled, restarted, and checked.

## 4. What To Observe In The Panel

During the live run, confirm in the panel:

- `/admin/servers` shows the bootstrap task;
- `/admin/jobs` shows the same task and step progress;
- `bootstrap_snapshot` advances by step;
- prompt flow is visible if SSH confirmation/password is required;
- errors, if any, are human-readable on the panel side.

## 5. Minimum Success Criteria

The first live validation is successful only if:

1. the server is added through the panel flow;
2. `safe-init` finishes with completed status;
3. the agent service is active under `systemd`;
4. the panel can poll bootstrap status to completion;
5. no step is failing only because of missing bootstrap dependencies.

## 6. If It Fails

If the first live validation fails, record at least:

- failing command;
- stderr/stdout from the step;
- SSH/auth/host-key status;
- whether the failure is environment-specific or contract-specific.

Then fix the issue in code or bootstrap assumptions before moving to the Tak
agent or broader rollout.
