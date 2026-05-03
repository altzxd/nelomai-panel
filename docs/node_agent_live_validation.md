# Nelomai Node-agent Live Validation

This document fixes the live validation flow for the Node `Tic/Tak` tunnel path
and the Node Tic-agent on real blank Ubuntu 22.04 hosts.

## 1. Goal

The purpose of live validation is not to prove the whole production story at
once. It is to confirm that the current `safe-init` profile works against real
remote hosts, that the panel can observe the result through the existing
bootstrap/job/diagnostics flow, and that the `Tic ↔ Tak` orchestration behaves
correctly under failure, recovery, and artifact rotation.

## 2. Target Hosts

Use real hosts with these properties:

- Ubuntu 22.04;
- blank or near-blank machine;
- reachable over SSH;
- no assumption of preinstalled Node.js, WireGuard, `iproute2`, `iptables`,
  `nftables`, `git`, `curl`, or archive tooling;
- one `Tic` host and one `Tak` host for tunnel validation.

## 3. Validation Scope

For the live pass, validate:

- `safe-init` on both hosts;
- tunnel attach/verify path between `Tic` and `Tak`;
- panel-side tunnel artifact rotation without dropping active `via_tak`;
- panel-side fallback `via_tak -> standalone -> via_tak`;
- panel-side retry policy `failure_count -> cooldown -> manual_attention_required`;
- panel-side `clear-backoff` path that removes degraded state without forcing full manual repair;
- panel-side manual exit from `manual_attention_required` through the diagnostics repair action;
- active `via_tak` switch from one `Tak` to another without entering fallback.

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

Expected tunnel/health outcomes:

- `Tic ↔ Tak` tunnel can be provisioned and attached;
- tunnel artifacts can be rotated while the pair stays active;
- panel-side fallback `via_tak -> standalone -> via_tak` works;
- repeated failed repair attempts increment `failure_count`;
- cooldown blocks immediate repeated repair attempts;
- persistent failures move the pair into `manual_attention_required`;
- `clear-backoff` removes stored degraded state and lets the next normal reconcile start from the first retry again;
- partial repair reattaches from existing `Tak` tunnel artifacts without reprovision;
- manual repair clears `manual_attention_required`, resets `failure_count`, and restores `via_tak`;
- `Tak` switch attaches the new pair before detaching the old one.

Recommended live workflow order:

1. `scripts/live_tunnel_remote_check.py`
2. `scripts/live_panel_tak_rotation_check.py`
3. `scripts/live_panel_tak_fallback_check.py`
4. `scripts/live_panel_tak_backoff_check.py`
5. `scripts/live_panel_tak_clear_backoff_check.py`
6. `scripts/live_panel_tak_partial_repair_check.py`
7. `scripts/live_panel_tak_manual_repair_check.py`

Optional multi-`Tak` scenario:

8. `scripts/live_panel_tak_switch_check.py`

This scenario requires a second Tak host via:

- `NELOMAI_TAK2_HOST`
- `NELOMAI_TAK2_SSH_PORT`
- `NELOMAI_TAK2_SSH_PASSWORD`
- `NELOMAI_TAK2_SSH_HOST_KEY`

For a single operator entrypoint, use:

```powershell
C:\Users\alter\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\live_panel_tak_health_workflow_check.py
```

## 4. What To Observe In The Panel

During the live run, confirm in the panel:

- `/admin/servers` shows the bootstrap task;
- `/admin/jobs` shows the same task and step progress;
- `bootstrap_snapshot` advances by step;
- prompt flow is visible if SSH confirmation/password is required;
- errors, if any, are human-readable on the panel side;
- diagnostics show `Tic ↔ Tak` tunnel health;
- diagnostics can trigger tunnel artifact rotation for a focused pair;
- diagnostics show `failure_count`, `cooldown_until`, and `manual_attention_required` when repair is degraded;
- diagnostics `Снять backoff` action clears degraded pair state and leaves the next retry to the normal reconcile path;
- diagnostics/audit can distinguish partial and full manual repair strategies;
- diagnostics repair action can take the pair out of `manual_attention_required`;
- interface switch to a second `Tak` does not force fallback if the new tunnel is healthy.

## 5. Minimum Success Criteria

The live validation is successful only if:

1. the server is added through the panel flow;
2. `safe-init` finishes with completed status;
3. the agent service is active under `systemd`;
4. the panel can poll bootstrap status to completion;
5. no step is failing only because of missing bootstrap dependencies;
6. tunnel artifact rotation keeps the pair active and increments `artifact_revision`;
7. the `Tic ↔ Tak` path can enter fallback and recover;
8. repeated failed repairs enter cooldown and then `manual_attention_required`;
9. `clear-backoff` clears degraded state and the next normal retry starts from the first failure step again;
10. partial repair can restore the pair without changing `tunnel_id`;
11. manual repair clears degraded state and returns the pair to a healthy route path;
12. `Tak` switch keeps the interface on a healthy `via_tak` path without transient fallback.

## 6. If It Fails

If the first live validation fails, record at least:

- failing command;
- stderr/stdout from the step;
- SSH/auth/host-key status;
- whether the failure is environment-specific or contract-specific;
- whether artifact rotation kept the same `tunnel_id` and increased `artifact_revision`;
- whether tunnel failure reached cooldown;
- whether tunnel failure reached `manual_attention_required`;
- whether `clear-backoff` really removed stored degraded state before the next retry;
- whether partial repair reused the existing tunnel identity;
- whether manual repair reset the pair state and restored `via_tak`.

Then fix the issue in code or bootstrap assumptions before moving to the Tak
agent or broader rollout.
