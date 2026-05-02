# Nelomai Panel ↔ Node-agent Contract

Short implementation-oriented reference:
`docs/agent_contract_quickref.md`

This document fixes the panel-side contract for future Tic/Tak Node-agent work.
The panel sends one JSON payload to the configured agent command through stdin and
expects one JSON response on stdout.

## Common Response

Every request from the panel includes versioned contract metadata:

```json
{
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "panel_version": "0.1.0",
  "component": "tic-agent",
  "requested_capabilities": ["peer.recreate.v1"],
  "action": "recreate_peer"
}
```

`component` is one of:

- `tic-agent`
- `tak-agent`
- `storage-agent` for remote storage servers
- `server-agent` for bootstrap/status operations before the exact component is known

The panel currently accepts legacy responses without contract metadata so it can
still reach old agents and update them. Once an agent sends contract metadata,
there must be at least one shared value between `supported_contracts` and the
panel-supported contracts, otherwise the panel rejects the action before
persisting panel-side changes.

Successful responses:

```json
{
  "ok": true,
  "agent_version": "0.1.0",
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "capabilities": ["agent.update.v1", "peer.recreate.v1"]
}
```

Failed responses:

```json
{ "ok": false, "error": "human readable error" }
```

If the agent does not respond or returns invalid JSON, the panel must not persist
the requested panel-side change.

## Capability Matrix

Every action declares `requested_capabilities` so agents can reject unsupported
operations explicitly:

| Action | Component | Capability |
| --- | --- | --- |
| `bootstrap_server` | `server-agent` | `agent.bootstrap.v1`, `agent.update.v1` |
| `bootstrap_server_status` | `server-agent` | `agent.bootstrap.v1`, `agent.update.v1` |
| `bootstrap_server_input` | `server-agent` | `agent.bootstrap.v1`, `agent.update.v1` |
| `restart_server_agent` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.lifecycle.v1` |
| `verify_server_status` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.status.v1` |
| `verify_server_runtime` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.runtime.v1` |
| `reboot_server` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.lifecycle.v1` |
| `check_server_agent_update` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.update.v1` |
| `update_server_agent` | `tic-agent` / `tak-agent` / `storage-agent` | `agent.update.v1` |
| `create_server_backup` | `tic-agent` / `tak-agent` | `backup.server.v1` |
| `verify_server_backup_copy` | `tic-agent` / `tak-agent` | `backup.server.v1` |
| `cleanup_server_backups` | `tic-agent` / `tak-agent` | `backup.server.v1` |
| `provision_tak_tunnel` | `tak-agent` | `tunnel.tak.provision.v1` |
| `attach_tak_tunnel` | `tic-agent` | `tunnel.tak.attach.v1` |
| `verify_tak_tunnel_status` | `tic-agent` / `tak-agent` | `tunnel.tak.status.v1` |
| `detach_tak_tunnel` | `tic-agent` / `tak-agent` | `tunnel.tak.detach.v1` |
| `prepare_interface` | `tic-agent` | `interface.create.v1` |
| `create_interface` | `tic-agent` | `interface.create.v1` |
| `toggle_interface` | `tic-agent` | `interface.state.v1` |
| `update_interface_route_mode` | `tic-agent` | `interface.route_mode.v1` |
| `update_interface_tak_server` | `tic-agent` | `interface.tak_server.v1` |
| `update_interface_exclusion_filters` | `tic-agent` | `filters.exclusion.v1` |
| `toggle_peer` | `tic-agent` | `peer.state.v1` |
| `recreate_peer` | `tic-agent` | `peer.recreate.v1` |
| `delete_peer` | `tic-agent` | `peer.delete.v1` |
| `download_peer_config` | `tic-agent` | `peer.download.v1` |
| `download_interface_bundle` | `tic-agent` | `peer.download_bundle.v1` |
| `update_peer_block_filters` | `tic-agent` | `filters.block.v1` |

Interface and peer lifecycle are intentionally Tic-only. `tak-agent` currently
owns only server/bootstrap/runtime/update/backup actions and must reject
interface/peer-level actions.

## Common Interface Payload

Most interface/peer actions include:

```json
{
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "panel_version": "0.1.0",
  "component": "tic-agent",
  "requested_capabilities": ["interface.state.v1"],
  "action": "action_name",
  "tic_server": { "id": 1, "name": "1a", "host": "10.0.0.1" },
  "interface": {
    "id": 12,
    "name": "VPN101",
    "route_mode": "standalone",
    "listen_port": 10001,
    "address_v4": "10.8.1.1/24"
  },
  "tak_server": { "id": 2, "name": "2a", "host": "10.0.0.2" },
  "exclusion_filters": { "enabled": true },
  "block_filters": { "enabled": true },
  "target_state": {}
}
```

`tak_server` is omitted when the interface is standalone.

`peer_limit` is panel-owned and must not be required by Tic-agent for interface
creation. The panel creates/removes peer rows and enforces limits itself.

## Interface Actions

`prepare_interface`

Panel sends selected `tic_server`, optional `tak_server`, interface `name`, and
effective route/filter flags. The interface id is `0` because the interface is
not persisted yet.

The panel owns `interface.id`. Tic-agent may return its own stable
`agent_interface_id`; after that, all interface payloads include both panel id
and server-side identity:

```json
{
  "interface": {
    "id": 17,
    "agent_interface_id": "wg-1a-00017",
    "server_identity": {
      "tic_server_id": 1,
      "agent_interface_id": "wg-1a-00017",
      "name": "wg-client"
    }
  }
}
```

Expected response:

```json
{ "ok": true, "listen_port": 10001, "address_v4": "10.8.1.1/24" }
```

`create_interface`

Panel sends final `name`, `listen_port`, `address_v4`, selected Tic/Tak context,
and effective route/filter flags. The interface is persisted only after the
agent succeeds.

Expected optional response:

```json
{ "ok": true, "agent_interface_id": "wg-1a-00017" }
```

If `agent_interface_id` is omitted, the panel keeps it as `null` and continues
to identify the server-side interface by `tic_server_id + name`.

`toggle_interface`

Includes:

```json
{ "target_state": { "is_enabled": true } }
```

`update_interface_route_mode`

Includes:

```json
{ "target_state": { "route_mode": "standalone" } }
```

`update_interface_tak_server`

Includes:

```json
{ "target_state": { "tak_server_id": null, "route_mode": "standalone" } }
```

`update_interface_exclusion_filters`

Includes:

```json
{ "target_state": { "exclusion_filters_enabled": false } }
```

When exclusion filters are disabled, traffic must not stay on Tic because of
exclusion rules. It should follow normal route behavior.

## Peer Actions

Peer actions include:

```json
{
  "peer": { "id": 55, "slot": 3, "comment": "phone" }
}
```

`toggle_peer`

Includes:

```json
{ "target_state": { "is_enabled": false } }
```

`recreate_peer`

The agent should delete the old config and create a new config under the same
slot/name. The panel keeps the same slot/comment and resets handshake/traffic
after success.

`delete_peer`

The agent should remove the peer config from the Tic server. The panel deletes
the peer row after success.

Expired peer lifecycle uses the same `delete_peer` action. When a peer reaches
its panel-side `expires_at`, the panel initiates `delete_peer`; if the agent is
unavailable or rejects the action, the panel keeps the peer row and writes an
audit error instead of silently losing synchronization.

`download_peer_config`

Expected response:

```json
{
  "ok": true,
  "filename": "VPN101-peer-3.conf",
  "content_type": "text/plain; charset=utf-8",
  "content_base64": "..."
}
```

`download_interface_bundle`

Expected response:

```json
{
  "ok": true,
  "filename": "VPN101.zip",
  "content_type": "application/zip",
  "content_base64": "..."
}
```

`update_peer_block_filters`

Includes:

```json
{ "target_state": { "block_filters_enabled": false } }
```

Block filters are peer-level. When enabled, matching traffic must be dropped,
not routed through Tic or Tak.

## Server Actions

Server actions include SSH connection data because the panel-side bridge may
operate against a blank Ubuntu 22.04 host:

```json
{
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "panel_version": "0.1.0",
  "component": "tic-agent",
  "requested_capabilities": ["agent.lifecycle.v1"],
  "action": "restart_server_agent",
  "server": {
    "id": 1,
    "name": "1a",
    "server_type": "tic",
    "host": "10.0.0.1",
    "ssh_port": 22,
    "ssh_login": "root",
    "ssh_password": "secret"
  }
}
```

Actions:

- `bootstrap_server`
- `bootstrap_server_status`
- `bootstrap_server_input`
- `restart_server_agent`
- `verify_server_status`
- `reboot_server`
- `check_server_agent_update`
- `update_server_agent`
- `create_server_backup`
- `verify_server_backup_copy`
- `cleanup_server_backups`

`verify_server_status` may return:

```json
{ "ok": true, "is_active": true }
```

`verify_server_runtime` is an agent-side diagnostics action for the future
panel self-diagnostics path. It should not mutate server state. It may return:

```json
{
  "ok": true,
  "status": "ready",
  "runtime": {
    "mode": "filesystem",
    "runtime_root": "/srv/nelomai-agent/runtime",
    "wireguard_root": "/etc/wireguard",
    "peers_root": "/etc/wireguard/peers",
    "ready": true,
    "checks": [
      { "key": "platform_linux", "ok": true, "message": "Linux platform detected" }
    ]
  }
}
```

### Bootstrap actions

`bootstrap_server` starts provisioning for a blank Ubuntu 22.04 host. For Tic
and Tak servers this blank-host assumption is mandatory: bootstrap must install
all required software itself and must not assume `wireguard`, `iproute2`,
`iptables`/`nftables`, `curl`, `git`, `ca-certificates`, archive tooling, or
Node.js are already present. The panel does not store the server as active
infrastructure until the bootstrap task finishes successfully. Payload includes
the future server identity, SSH fields, single monorepo URL, and OS hints:

```json
{
  "action": "bootstrap_server",
  "server": {
    "name": "1a",
    "server_type": "tic",
    "host": "10.0.0.1",
    "ssh_port": 22,
    "ssh_login": "root",
    "ssh_password": "secret"
  },
  "repository_url": "https://github.com/example/nelomai",
  "os_family": "ubuntu",
  "os_version": "22.04"
}
```

Bootstrap responses drive the mini-terminal UI. The canonical task statuses
stored by the panel are `running`, `input_required`, `completed`, and `failed`.
The agent can complete immediately, keep running, fail, or request
input/confirmation:

```json
{
  "ok": true,
  "status": "input_required",
  "logs": ["Connected over SSH", "Repository cloned"],
  "input_prompt": "Enter sudo confirmation",
  "input_key": "sudo_confirm",
  "input_kind": "confirm"
}
```

`bootstrap_server_status` receives the same bootstrap context plus `task_id` and
returns the current status/logs. `bootstrap_server_input` receives the same
context plus:

```json
{
  "input": {
    "key": "sudo_confirm",
    "kind": "confirm",
    "value": "yes"
  }
}
```

`confirmation_required` is accepted as an agent response alias for
`input_required` with `input_kind: "confirm"`, but the panel stores and returns
the canonical status `input_required`.

Input kinds:

- `text`: the mini-terminal shows a text input and sends the entered value.
- `confirm`: the mini-terminal shows a confirmation button and sends `"yes"`.

If the agent returns `failed` or `{ "ok": false, "error": "..." }`, the panel
keeps the server out of active infrastructure and shows the error in the
bootstrap console. If the local bridge cannot start, times out, returns invalid
JSON, or reports an SSH error, the panel converts that into a readable
admin-facing message while preserving the task as `failed`.

### Agent lifecycle and status

Agent update actions include the single Nelomai monorepo URL. The agent chooses
what to update by `component` (`tic-agent` or `tak-agent`):

```json
{
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "panel_version": "0.1.0",
  "component": "tic-agent",
  "requested_capabilities": ["agent.update.v1"],
  "action": "check_server_agent_update",
  "server": { "id": 1, "name": "1a", "server_type": "tic" },
  "repository_url": "https://github.com/example/nelomai"
}
```

`check_server_agent_update` may return current/latest versions and whether an
update is available:

```json
{
  "ok": true,
  "status": "checked",
  "current_version": "0.1.0",
  "latest_version": "0.1.1",
  "update_available": true,
  "message": "Update is available"
}
```

`update_server_agent` applies the update on the target server and may return the
same shape with `status: "updated"`.

### Tic <-> Tak tunnel model

The current target model for `route_mode=via_tak` is:

- `Tic` acts as the tunnel client.
- `Tak` acts as the tunnel server.
- The inter-server tunnel uses `AmneziaWG 2.0`.
- Each `Tic -> Tak` binding gets its own dedicated inter-server tunnel.
- One `Tak` may serve multiple `Tic` servers.
- One `Tic` currently supports only one active `Tak` binding.
- `Tak` generates the `AmneziaWG 2.0` tunnel configuration and hands it to the
  panel; the panel passes it to the selected `Tic`.
- The production source of truth for that tunnel configuration should be the
  official `AmneziaWG` tooling/repository, not a panel-defined generator.
- The current structured `amnezia_config` payload is a temporary adapter format
  used until the official integration is wired in.
- The tunnel listen port on `Tak` is random/non-standard and not manually
  editable in the current phase.
- Only user traffic of interfaces running with `route_mode=via_tak` should
  traverse the inter-server tunnel.
- System traffic of the `Tic` host itself must stay local and must not be
  redirected through `Tak`.
- If the inter-server tunnel goes down, affected interfaces temporarily fall
  back to `standalone`.
- After the tunnel is restored, those interfaces return to `via_tak`.
- `Tak` performs outbound `SNAT/MASQUERADE` for traffic received from the
  `Tic` tunnel as the first-release default.

### Planned tunnel lifecycle actions

The first implementation slice should keep the tunnel lifecycle separate from
the existing interface/peer actions and use dedicated actions:

- `provision_tak_tunnel`
  - component: `tak-agent`
  - responsibility:
    - allocate a random/non-standard listen port;
    - allocate a dedicated point-to-point tunnel subnet, preferably `/30`;
    - generate the server-side `AmneziaWG 2.0` config and metadata;
    - persist the Tak-side tunnel runtime;
    - return the data package that the panel will hand to the selected `Tic`.

- `attach_tak_tunnel`
  - component: `tic-agent`
  - responsibility:
    - receive the Tak-generated tunnel package from the panel;
    - generate/persist the Tic-side client config;
    - bring the inter-server tunnel up;
    - mark the tunnel as active for the bound `Tak`.

- `verify_tak_tunnel_status`
  - component: `tic-agent` / `tak-agent`
  - responsibility:
    - report whether the inter-server tunnel exists and is active on that side;
    - expose enough state for panel diagnostics and automatic fallback.

- `detach_tak_tunnel`
  - component: `tic-agent` / `tak-agent`
  - responsibility:
    - tear the tunnel down cleanly;
    - remove or deactivate its runtime files;
    - ensure affected interfaces return to `standalone`.

Panel orchestration rules for this planned lifecycle:

- tunnel provisioning starts when a `Tak` server is bound to a `Tic`;
- the panel first calls `provision_tak_tunnel` on `tak-agent`;
- the panel then calls `attach_tak_tunnel` on `tic-agent`;
- normal `route_mode=via_tak` traffic is allowed only after both sides confirm
  that the tunnel is active;
- if either side reports the tunnel down, the panel and/or `tic-agent` should
  force affected interfaces back to `standalone`.

Planned payload highlights:

- `provision_tak_tunnel` should receive:
  - `server` (`Tak`);
  - `tic_server` target metadata;
  - desired tunnel purpose/version metadata.
- `provision_tak_tunnel` should return:
  - `tunnel_id`;
  - `listen_port`;
  - `network_cidr`;
  - `tak_address_v4`;
  - `tic_address_v4`;
  - `amnezia_config` as a temporary structured adapter payload for `Tic` with:
    - `protocol`, `version`;
    - `endpoint.host`, `endpoint.port`;
    - `addressing.network_cidr`, `addressing.tak_address_v4`, `addressing.tic_address_v4`, `addressing.allowed_ips`;
    - `keys.client_private_key`, `keys.client_public_key`, `keys.server_public_key`;
    - `awg_parameters.jitter_seed`;
    - `awg_parameters.header_obfuscation.H1-H4`;
    - `awg_parameters.session_noise.S1-S4`;
    - `awg_parameters.init_noise.I1-I5`.
- Long-term target:
  - `Tak` should call the official `AmneziaWG` generator/runtime and return the
    canonical server/client tunnel artifacts derived from that toolchain;
  - panel-side fields should describe tunnel intent and transport metadata, not
    reimplement the final `AmneziaWG` config format.
- `attach_tak_tunnel` should receive:
  - `server` (`Tic`);
  - `tak_server` metadata;
  - `tunnel_id`;
  - the `Tak`-generated `amnezia_config` payload.
- `verify_tak_tunnel_status` should return:
  - `exists`;
  - `is_active`;
  - `tunnel_id`;
  - optional last handshake / last error / endpoint metadata.

`create_server_backup` asks the target server agent to prepare a server-side
snapshot for a full panel backup. The response uses the same file payload shape
as downloads:

```json
{
  "ok": true,
  "filename": "1a-snapshot.zip",
  "content_type": "application/zip",
  "content_base64": "..."
}
```

The panel sends `backup_policy`. Agents should use it for local/remote retention
and size limits:

```json
{
  "backup_policy": {
    "fresh_retention_days": 90,
    "fresh_size_limit_mb": 5120,
    "monthly_retention_days": 365,
    "monthly_size_limit_mb": 3072,
    "remote_storage_server": {
      "id": 9,
      "name": "backup-storage",
      "host": "10.0.0.50",
      "ssh_port": 22
    }
  }
}
```

`remote_storage_server` is `null` when no dedicated storage server is selected.
Tic/Tak cross-copy handling lives in the Node-agent layer; the panel records
agent-reported copy metadata in the full backup manifest.

`verify_server_backup_copy` is used by the panel to check the newest completed
full backup stored on the panel. The panel compares each server only with its
own snapshot copy on that same server:

```json
{
  "action": "verify_server_backup_copy",
  "server": { "id": 1, "name": "1a", "server_type": "tic" },
  "backup_id": 42,
  "backup_filename": "nelomai-full-20260422-030000-42.zip",
  "snapshot": {
    "filename": "server_snapshots/1-1a-snapshot.zip",
    "size_bytes": 1048576,
    "sha256": "..."
  }
}
```

Expected response:

```json
{ "ok": true, "matches": true, "message": "snapshot matches local server copy" }
```

`cleanup_server_backups` is a panel-side command for Tic/Tak Node-agents. It is
used by the admin action "clear servers from backups". The panel does not delete
its own backup archives and does not send this command to a dedicated remote
storage server. Agents should delete local server backup copies while keeping
the newest copies requested by the panel:

```json
{
  "action": "cleanup_server_backups",
  "server": { "id": 1, "name": "1a", "server_type": "tic" },
  "keep_latest_count": 1,
  "backup_policy": {
    "fresh_retention_days": 90,
    "fresh_size_limit_mb": 5120,
    "monthly_retention_days": 365,
    "monthly_size_limit_mb": 3072,
    "remote_storage_server": null
  }
}
```

Expected response:

```json
{ "ok": true, "deleted_count": 3, "message": "old server backups deleted" }
```
