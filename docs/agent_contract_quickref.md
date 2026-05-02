# Nelomai Panel -> Node-agent Quick Reference

This is the short developer-oriented contract reference for implementing the
future Tic/Tak Node-agent. The full source of truth remains
`docs/agent_contract.md`.

Target production runtime model:
`docs/node_agent_runtime_model.md`

## 1. Transport

- Panel runs the configured agent command locally.
- Panel writes exactly one JSON payload to stdin.
- Agent must write exactly one JSON response to stdout.
- Logs/debug noise must not be mixed into stdout JSON.
- stderr is allowed for debugging, but panel treats startup/timeout/process
  failures as operation errors.

## 2. Versioning

Every request includes:

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

Rules:

- Agent may reply in legacy mode without contract metadata.
- If agent does send `contract_version` or `supported_contracts`, there must be
  at least one shared supported contract with the panel.
- Unsupported contract is treated as operation failure.

## 3. Success / Failure Response

Minimal success:

```json
{ "ok": true }
```

Recommended success:

```json
{
  "ok": true,
  "agent_version": "0.1.0",
  "contract_version": "1.0",
  "supported_contracts": ["1.0"],
  "capabilities": ["peer.recreate.v1"]
}
```

Minimal failure:

```json
{ "ok": false, "error": "human readable error" }
```

Panel behavior:

- invalid JSON -> failure, no panel-side state change;
- process startup failure -> failure;
- timeout -> failure;
- `ok: false` -> failure;
- success without required payload fields -> failure.

## 4. Non-negotiable Ownership Rules

- `peer_limit` is panel-owned. Agent must not allocate or validate peer limits.
- Panel owns `interface.id`, `peer.id`, and peer slot lifecycle in DB.
- Agent may return `agent_interface_id` as server-side stable identity.
- Peer recreate keeps the same slot/name from the panel perspective.
- On failed agent action, panel must not silently commit the requested state
  change.

## 5. Core Payload Blocks

Typical interface payload:

```json
{
  "tic_server": { "id": 1, "name": "1a", "host": "10.0.0.1" },
  "interface": {
    "id": 12,
    "name": "VPN101",
    "agent_interface_id": "wg-1a-00012",
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

Typical peer payload:

```json
{
  "peer": {
    "id": 55,
    "slot": 3,
    "comment": "phone"
  }
}
```

## 6. Action Semantics

### Interface lifecycle

These actions are supported only by `tic-agent`. `tak-agent` must reject them.

`prepare_interface`
- input: selected Tic server, optional Tak server, interface name;
- output: suggested `listen_port`, `address_v4`;
- agent must not persist panel DB state.

`create_interface`
- input: final name, port, IPv4, route/filter effective state;
- output: optional `agent_interface_id`;
- panel persists interface only after agent success.

`toggle_interface`
- `target_state.is_enabled`

`update_interface_route_mode`
- `target_state.route_mode`

`update_interface_tak_server`
- `target_state.tak_server_id`
- `target_state.route_mode`

`update_interface_exclusion_filters`
- `target_state.exclusion_filters_enabled`

### Peer lifecycle

These actions are supported only by `tic-agent`. `tak-agent` must reject them.

`toggle_peer`
- `target_state.is_enabled`

`recreate_peer`
- agent deletes old config and creates new config under the same logical slot;
- panel resets handshake and traffic only after success.

`delete_peer`
- agent removes peer config;
- panel deletes DB row only after success.

`download_peer_config`
- must return file payload

`download_interface_bundle`
- must return zip file payload

`update_peer_block_filters`
- `target_state.block_filters_enabled`

### Server / infrastructure

`bootstrap_server`
- used before server becomes active infrastructure;
- blank Ubuntu 22.04 target;
- for Tic/Tak servers bootstrap must install all required software itself;
- payload contains SSH credentials and monorepo URL.

`bootstrap_server_status`
- poll current bootstrap state

`bootstrap_server_input`
- send interactive input back to bootstrap task

`restart_server_agent`
- restart target agent service/process

`verify_server_status`
- may return `{ "ok": true, "is_active": true }`

`verify_server_runtime`
- agent-side runtime diagnostics for Linux/WireGuard readiness;
- should report whether execution mode, runtime root, and required commands are ready.

`reboot_server`
- reboot host

`check_server_agent_update`
- check whether monorepo-based agent update is available

`update_server_agent`
- apply agent update for selected component

`create_server_backup`
- return server snapshot archive

`verify_server_backup_copy`
- compare server-local snapshot copy against latest panel full backup metadata

`cleanup_server_backups`
- delete old server-local backups but keep latest copies requested by panel

## 6.1 Tic <-> Tak tunnel

- `route_mode=via_tak` uses a dedicated `AmneziaWG 2.0` tunnel between `Tic`
  and `Tak`.
- `Tic` is always the client and initiates/reconnects the tunnel itself.
- `Tak` generates the tunnel config and passes it through the panel to the
  bound `Tic`.
- One `Tak` can serve many `Tic` servers, but one `Tic` currently uses only one
  active `Tak`.
- The tunnel listen port on `Tak` is random/non-standard.
- Traffic of `via_tak` interfaces goes into the tunnel; system traffic of the
  `Tic` host does not.
- If the tunnel is down, affected interfaces temporarily fall back to
  `standalone`; after recovery they return to `via_tak`.
- `Tak` should perform outbound `SNAT/MASQUERADE` for tunneled traffic in the
  first release.

Planned first action set:

- `provision_tak_tunnel` on `tak-agent`
- `attach_tak_tunnel` on `tic-agent`
- `verify_tak_tunnel_status` on both sides
- `detach_tak_tunnel` on the side being unbound/stopped

Planned first payload/result minimums:

- `provision_tak_tunnel` -> `tunnel_id`, `listen_port`, `network_cidr`,
  `tak_address_v4`, `tic_address_v4`, `amnezia_config`
- `attach_tak_tunnel` -> `ok`, `tunnel_id`, optional `is_active`
- `verify_tak_tunnel_status` -> `ok`, `exists`, `is_active`, `tunnel_id`
- `detach_tak_tunnel` -> `ok`, `tunnel_id`, optional `detached`

Planned ownership split:

- `tak-agent` owns server-side tunnel allocation and `AmneziaWG 2.0` server
  config generation;
- `tic-agent` owns client-side attach/reconnect and runtime failover back to
  `standalone`.

## 7. File Payload Shape

Used by:

- `download_peer_config`
- `download_interface_bundle`
- `create_server_backup`

Response shape:

```json
{
  "ok": true,
  "filename": "VPN101-peer-3.conf",
  "content_type": "text/plain; charset=utf-8",
  "content_base64": "..."
}
```

Requirements:

- `content_base64` must decode successfully;
- panel rejects malformed file payloads.

## 8. Bootstrap Interactive Flow

Canonical panel task statuses:

- `running`
- `input_required`
- `completed`
- `failed`

Agent may request input like this:

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

Common bootstrap prompt keys now expected by the Node scaffold:

- `ssh_host_key_confirm`
- `ssh_password`
- `bootstrap_step_<n>_confirm`

Accepted alias:

- `confirmation_required` is normalized by the panel to `input_required`

Input block sent back by panel:

```json
{
  "input": {
    "key": "sudo_confirm",
    "kind": "confirm",
    "value": "yes"
  }
}
```

## 9. Panel-facing Error Classes

The panel treats these as operationally important agent-side failures:

- agent command not configured;
- process failed to start;
- timeout;
- invalid JSON;
- unsupported contract version;
- `ok: false` with error string;
- missing required response fields;
- malformed file payload.

Practical recommendation:

- always return a short human-readable `error`;
- for recoverable failures, keep the message actionable;
- do not return HTML, logs, or multi-part mixed output through stdout.

## 10. First Tic-agent Milestone

The minimum useful Tic-agent implementation should support:

- `prepare_interface`
- `create_interface`
- `toggle_interface`
- `toggle_peer`
- `recreate_peer`
- `delete_peer`
- `download_peer_config`
- `download_interface_bundle`
- `update_interface_route_mode`
- `update_interface_tak_server`
- `update_interface_exclusion_filters`
- `update_peer_block_filters`
- `verify_server_status`

Everything else can be added on top once this base path is stable.
