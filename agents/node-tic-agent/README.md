# Nelomai Node Tic-agent Skeleton

This folder contains the first Node.js skeleton for the future Tic server
agent. It is intentionally isolated from the panel runtime and does not execute
real server operations yet.

## Current Scope

Implemented:

- stdin -> JSON request parsing;
- contract version and component validation;
- action registry with capability expectations;
- structured success/error responses;
- local state-backed `prepare_interface` and `create_interface`;
- local state-backed `toggle_interface`, `toggle_peer`, `recreate_peer`, `delete_peer`;
- local state-backed route/Tak/filter updates for interfaces and peers;
- state-backed `download_peer_config` and `download_interface_bundle`;
- local state-backed bootstrap/status/update lifecycle for server actions;
- Ubuntu 22.04 bootstrap plan generation with package list, install commands,
  and systemd service scaffolding;
- internal bootstrap executor with `dry-run` / gated `apply` modes;
- transport abstraction for bootstrap execution: `noop / local / ssh`;
- first non-interactive SSH bootstrap transport for key-based auth;
- interactive bootstrap prompt channel for SSH host key, SSH password auth via `sshpass` or Windows `plink`, and per-step confirmations;
- real filesystem artifact lifecycle for interfaces and peers;
- Linux command scaffolding for `create_interface`, `recreate_peer`, `delete_peer`;
- dedicated runtime diagnostics action: `verify_server_runtime`;
- optional stub success mode for local contract experiments;
- optional request logging to a JSONL file.

Not implemented yet:

- full WireGuard key/config generation;
- real backup/snapshot handling;
- full remote bootstrap orchestration with prompts and confirmations.

Bootstrap assumption:

- target Tic server starts as blank `Ubuntu 22.04`;
- the same bootstrap assumption will be used for Tak servers;
- required software must be installed by bootstrap itself;
- do not assume preinstalled `wireguard`, `iproute2`, `iptables`, `curl`,
  `git`, `ca-certificates`, `zip`, `tar`, or Node.js.
- current full bootstrap package baseline for Tic/Tak:
  - `bash`
  - `ca-certificates`
  - `curl`
  - `git`
  - `jq`
  - `tar`
  - `unzip`
  - `zip`
  - `iproute2`
  - `iptables`
  - `nftables`
  - `wireguard`
  - `wireguard-tools`

## Run

```bash
node src/index.js < request.json
```

## Daemon

`src/index.js` is the request/response CLI entrypoint for panel-side agent calls.
It is not a long-running service process.

For `systemd`, use:

```bash
node src/daemon.js
```

The daemon:

- runs runtime readiness checks on startup;
- writes heartbeat status to JSON;
- stays alive under `systemd`;
- does not replace the CLI contract entrypoint.

## Bridge Check

There is a dedicated panel-side developer check for this skeleton:

```bash
python scripts/node_agent_bridge_check.py
```

Behavior:

- if Node.js is not installed, the script prints `SKIP`;
- if Node.js is available, it syntax-checks the skeleton files and performs one
  real stdin/stdout bootstrap bridge call against `src/index.js`.

## Environment Variables

- `NELOMAI_AGENT_COMPONENT`
  default: `tic-agent`
- `NELOMAI_AGENT_VERSION`
  default: `0.1.0`
- `NELOMAI_AGENT_SUPPORTED_CONTRACTS`
  default: `1.0`
- `NELOMAI_AGENT_LOG`
  optional JSONL path for incoming payload logging
- `NELOMAI_AGENT_STATE_FILE`
  optional path to the local JSON state used by `prepare_interface` and
  `create_interface`
- `NELOMAI_AGENT_DAEMON_STATUS_FILE`
  optional path to the daemon heartbeat/status JSON file
- `NELOMAI_AGENT_DAEMON_HEARTBEAT_SEC`
  default: `30`
  heartbeat interval for the daemon status file
- `NELOMAI_AGENT_RUNTIME_ROOT`
  filesystem root where the agent stores interface and peer artifacts
- `NELOMAI_AGENT_STUB_MODE`
  if set to `success`, recognized actions return deterministic stub success
  payloads instead of `not implemented`
- `NELOMAI_AGENT_EXEC_MODE`
  default: `filesystem`
  available values:
  - `filesystem`: update artifacts and return planned Linux commands
  - `system`: also execute planned Linux commands through `bash -lc`
- `NELOMAI_AGENT_SYSTEM_WG_ROOT`
  default: `/etc/wireguard`
  system-mode root for WireGuard-compatible files
- `NELOMAI_AGENT_BOOTSTRAP_MODE`
  default: `dry-run`
  available values:
  - `dry-run`: build bootstrap plan and execution report without running commands
  - `apply`: execute bootstrap commands locally through `bash -lc`
- `NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE`
  default: `safe-init`
  available values:
  - `safe-init`: remote sanity checks, `apt-get update`, install of base, networking, and first WireGuard packages (`ca-certificates`, `curl`, `git`, `iproute2`, `iptables`, `jq`, `nftables`, `tar`, `unzip`, `wireguard`, `wireguard-tools`, `zip`), NodeSource bootstrap, `nodejs` install, directory preparation, `git clone/pull` of the monorepo, `npm install --omit=dev` for `agents/node-tic-agent`, systemd unit generation, `daemon-reload`, `systemctl enable`, `systemctl restart`, and service status check on a blank Ubuntu 22.04 host
  - `full`: same bootstrap path plus only the package delta that is still missing for the selected server type
- `NELOMAI_AGENT_BOOTSTRAP_ALLOW_LOCAL`
  default: unset
  must be `1` before `apply` mode can execute commands locally
- `NELOMAI_AGENT_BOOTSTRAP_TRANSPORT`
  default: `noop`
  available values:
  - `noop`: only planned execution metadata
  - `local`: executes locally through `bash -lc` when local apply is allowed
  - `ssh`: executes through non-interactive `ssh` using key-based auth
- `NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH`
  default: unset
  must be `1` before `ssh` transport can execute commands
- `NELOMAI_AGENT_BOOTSTRAP_SSHPASS_BIN`
  default: `sshpass`
  optional override for the `sshpass` binary used for password-auth SSH bootstrap
- `NELOMAI_AGENT_BOOTSTRAP_PLINK_BIN`
  default: `plink`
  optional override for the Windows PuTTY `plink` binary used for password-auth SSH bootstrap
- `NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE`
  default: `auto`
  available values:
  - `auto`: use password auth when a password is available, otherwise key auth
  - `key`: ignore stored SSH password and force key-based SSH transport
  - `password`: force password-auth SSH transport
- `NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING`
  default: `accept-new`
  forwarded to `ssh -o StrictHostKeyChecking=...`
- `NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY`
  optional pinned SSH host key fingerprint, required for Windows `plink` batch mode
- `NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT`
  default: `10`
  forwarded to `ssh -o ConnectTimeout=...`
- `NELOMAI_AGENT_BOOTSTRAP_SSH_KNOWN_HOSTS_FILE`
  optional `ssh -o UserKnownHostsFile=...` override
- `NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM`
  default: unset
  if set to `1`, SSH transport pauses bootstrap and requests `ssh_host_key_confirm`
- `NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM`
  default: unset
  if set to `1`, SSH transport pauses before each step and requests `bootstrap_step_<n>_confirm`

## Notes

- stdout must contain only one JSON response.
- stderr may be used for debug output if needed later.
- This skeleton follows:
  - `docs/agent_contract.md`
  - `docs/agent_contract_quickref.md`
  - `docs/node_agent_runtime_model.md`
- Current allocation logic:
  - per Tic server, first free `listen_port` starting from `10001`
  - per Tic server, first free IPv4 slot in `10.8.X.1/24`
  - per peer slot, stable peer tunnel address inside the interface subnet:
    `10.8.X.(slot+1)/32`
- Current real vertical:
  - `create_interface` writes interface artifacts
  - `create_interface` in `system` mode installs `/etc/wireguard/<agent_interface_id>.conf` and applies it through `wg-quick up` or `wg syncconf`
  - `toggle_interface` in `system` mode performs real `wg-quick up/down` lifecycle while keeping configs on disk
  - `toggle_peer` in `system` mode installs or removes peer artifacts and reapplies live config through `wg syncconf`
  - `update_interface_route_mode` and `update_interface_tak_server` in `system` mode refresh the installed config and reapply live interface state when enabled
  - `recreate_peer` now changes peer key material deterministically through `config_revision`, not only the revision counter
  - `renderInterfaceConfig()` now produces a real `wg-quick`-style interface config with `[Peer]` sections
  - `renderPeerConfig()` now produces a more realistic client config with peer-specific tunnel address, DNS, MTU, and resolved endpoint host
  - peer tunnel addresses are stored in agent state, not recalculated ad hoc in render only
  - disabled peers are excluded from live interface config rendering
  - `recreate_peer` writes peer config artifacts
  - `delete_peer` removes peer config artifacts
  - `download_*` reads runtime artifacts instead of pure inline placeholders
  - `verify_server_runtime` reports whether the agent host is ready for Linux/WireGuard execution
  - `bootstrap_server` now returns a concrete Ubuntu 22.04 install/service plan for the future live bootstrap path
  - bootstrap is designed under the assumption that Tic/Tak hosts begin as blank Ubuntu 22.04 machines and therefore must receive all runtime dependencies during provisioning
- bootstrap executor can now return a dry-run execution report or a gated local apply report
- bootstrap transport is separated from bootstrap logic, and `ssh` now works as the first real remote transport
  - key-based mode uses `BatchMode=yes`
  - password-auth mode uses `sshpass -e ssh` on Linux-like hosts
  - password-auth mode uses PuTTY `plink` in Windows environments when it is available
- bootstrap tasks can now pause with `input_required` on SSH host key confirm, password capture, or explicit per-step command confirmation, then resume from the interrupted step
  - bootstrap responses now include a compact `bootstrap_snapshot` with transport, applied/planned state, current step, and resume pointer
- Runtime layout is now closer to `/etc/wireguard`:
  - one interface directory per `agent_interface_id`
  - `wg0.conf` for interface-level config
  - `peers/<slot>.conf` for peer-level configs
  - `interface.json` for local agent metadata
- `system` mode converges toward the production layout:
  - `/etc/wireguard/<agent_interface_id>.conf`
  - `/etc/wireguard/peers/<agent_interface_id>/<slot>.conf`
- `system` mode now performs an explicit environment preflight before executing commands:
  - Linux platform
  - `bash` present
  - `ip` present
  - `wg` present
  - `wg-quick` present
  - runtime root writable
  - WireGuard-compatible roots prepared
