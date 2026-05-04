# Nelomai Node-agent Runtime Model

This document fixes the target production model for the future Tic server
agent. The same bootstrap assumption must later be mirrored for the Tak agent:
both Tic and Tak servers are treated as blank Ubuntu 22.04 hosts at the start
of provisioning. It is the reference point for replacing the current
filesystem/runtime scaffold with real Ubuntu 22.04 and WireGuard operations.

## 1. Responsibility Split

Panel:

- owns users, interfaces, peers, peer limits, filters, assignments, audit,
  jobs, backups orchestration, diagnostics, and access control;
- decides when an agent action must run;
- persists panel-side state only after successful agent response.

Node Tic-agent:

- executes server-side operations for WireGuard and related system tasks;
- prepares and validates runtime environment on the target server;
- writes and updates WireGuard-compatible files;
- invokes system commands such as `wg`, `wg-quick`, `ip`, and `systemctl`;
- returns structured stdout JSON according to the panel contract.

Node Tak-agent:

- handles server bootstrap, runtime diagnostics, update lifecycle, and
  server-backup operations for Tak hosts;
- owns the server side of the shared `AmneziaWG 2.0` inter-server tunnel used
  by bound `Tic` servers;
- does not own interface or peer lifecycle in the current architecture.

WireGuard itself is not reimplemented inside the agent. The agent is only an
orchestrator around the official Linux WireGuard tooling.

## 2. Target Host

Target server profile:

- OS: `Ubuntu 22.04`
- role: `Tic server`
- initial state: blank host, reachable over SSH

The same initial-state rule must be used for Tak servers:

- OS: `Ubuntu 22.04`
- role: `Tak server`
- initial state: blank host, reachable over SSH

The bootstrap path must assume the host may not yet have:

- Node.js
- Python 3
- WireGuard
- WireGuard userspace helper
- official `AmneziaWG` tools
- iptables/nftables tooling
- zip/tar/archive tooling
- curl/git/ca-certificates
- runtime directories
- systemd service files for the agent

Bootstrap must install every required runtime dependency itself. It must not
assume that a fresh Ubuntu 22.04 host already contains networking tools,
WireGuard, Python 3, Node.js, archive utilities, userspace tunnel helpers, or
agent-specific directories.

If the deployed agent or its bridge scripts call a binary at runtime, bootstrap
must make that binary available before the agent service starts.

The first safe bootstrap slice may install only the base operating packages
needed for later provisioning, for example:

- `bash`
- `build-essential`
- `ca-certificates`
- `curl`
- `git`
- `iproute2`
- `iptables`
- `jq`
- `nftables`
- `python3`
- `tar`
- `unzip`
- `ufw`
- `wireguard`
- `wireguard-tools`
- `zip`

For the current `Tic <-> Tak` production path this safe slice must also
install or build:

- latest Go toolchain from the official `go.dev` distribution;
- official `amneziawg-tools` from `amnezia-vpn/amneziawg-tools`;
- official `amneziawg-go` from `amnezia-vpn/amneziawg-go`.

This safe slice is intentionally smaller than the final full bootstrap.
It may already include installation of `nodejs` as the last package/runtime
layer before repository rollout, and may also perform the first `git clone` /
`git pull` into the working tree, `npm install` for the agent module,
environment file generation, installation of an admin public key, SSH
hardening, secret permissions hardening, role-specific `ufw` rules, systemd
unit file generation with `daemon-reload`, `systemctl enable`,
`systemctl restart`, and a first service status check.

For new `Tic`/`Tak` hosts the bootstrap path should also converge them to the
same base security posture:

- install `ufw` and set default `deny incoming`;
- keep `22/tcp` open;
- keep only the required role-specific UDP ports open;
- install an approved admin SSH public key before disabling password login;
- disable `PasswordAuthentication`, `KbdInteractiveAuthentication`,
  `X11Forwarding`, and `AllowTcpForwarding`;
- enforce `PermitRootLogin prohibit-password`;
- set `/etc/default/nelomai-*.service` style env files and private keys to
  `0600`.

If a separate `full` profile exists, it should not fork into a second large
bootstrap path. It should only add the remaining delta over `safe-init` for
the selected server type.

## 3. Installation Source of Truth

Official platform components should come from Ubuntu package sources, not from
ad-hoc GitHub installers.

Use package manager for:

- `wireguard`
- `iproute2`
- `iptables` and/or `nftables`
- `curl`
- `git`
- `ca-certificates`
- `tar`
- `zip`
- `bash`
- `systemd`
- other Linux runtime dependencies

Use GitHub/monorepo only for:

- Nelomai panel codebase
- Node agents
- bootstrap scripts and service scaffolding

This keeps WireGuard standard and reduces custom failure surface.

## 4. Agent Runtime Modes

The current design keeps two modes:

- `filesystem`
  - safe default
  - writes runtime artifacts only
  - returns planned commands
- `system`
  - uses the same artifact model
  - additionally executes real Linux commands

`filesystem` remains the fallback/debug mode even after `system` becomes
production-ready.

## 5. Runtime Layout

Panel-side scaffold already uses a WireGuard-oriented layout and should converge
to this model:

- runtime metadata root:
  - agent-owned state and metadata
- WireGuard file root:
  - `/etc/wireguard`
- per interface:
  - interface config file
  - peer config files
  - agent metadata file

Target structure:

```text
/var/lib/nelomai-agent/
  state.json
  interfaces/
    <agent_interface_id>/
      interface.json

/etc/wireguard/
  <agent_interface_id>.conf
  peers/
    <agent_interface_id>/
      <slot>.conf
```

Notes:

- panel `interface.id` is not enough as a Linux identity;
- `agent_interface_id` must be the stable server-side identity;
- the agent should not rely on interface display name as the only unique key.

## 6. System Dependencies to Expect

The runtime preflight for production `system` mode should treat these as
required:

- Linux platform
- `bash`
- `ip`
- `wg`
- `awg`
- `awg-quick`
- `python3` on `Tak` for the official bridge path
- `amneziawg-go` on `Tic` for the userspace tunnel path
- writable runtime root
- writable `/etc/wireguard`
- required peer subdirectories

Optional but likely needed later:

- `systemctl`
- `journalctl`
- backup tooling if package names differ on the target image

## 6.1 Panel Server Note

The panel server is different from Tic/Tak servers:

- for Tic/Tak bootstrap we must assume a blank Ubuntu 22.04 host;
- for the panel server we should not assume a blank host retroactively.

Before the first release, the panel server needs a separate inventory pass:

- list what is already installed;
- compare it against the real panel runtime requirements;
- define which packages/services must be added to the panel install guide.

## 7. Service Model

The agent itself should run as a managed system service.

Target expectation:

- one systemd unit for Node Tic-agent
- panel can restart it through agent/bootstrap lifecycle
- service logs remain available through normal Linux tools

WireGuard lifecycle should use standard Linux behavior:

- config file written by the agent
- interface applied by `wg-quick` or an equivalent explicit `ip` + `wg`
  sequence
- state changes reflected back to the panel through contract responses

## 7.1 Tic <-> Tak inter-server tunnel

The current target routing model is:

- `Tic` is the client side of the inter-server tunnel and is responsible for
  bring-up and reconnection.
- `Tak` is the server side of the inter-server tunnel.
- The tunnel protocol is `AmneziaWG 2.0`.
- The tunnel is shared by all interfaces/peers of a given `Tic` server that
  currently use `route_mode=via_tak`.
- Each bound `Tic -> Tak` pair gets a separate inter-server tunnel.
- One `Tak` may host multiple such tunnels for different `Tic` servers.
- One `Tic` currently has only one active `Tak` binding.
- `Tak` allocates and generates the `AmneziaWG 2.0` server-side tunnel config.
- The production source of truth should be the official `AmneziaWG` tooling.
- The current structured `amnezia_config` payload is only a bridge format for
  panel↔agent orchestration until the official integration is connected.
- `Tak` is currently also the source of truth for the structured `amnezia_config` payload:
  - endpoint;
  - tunnel addressing;
  - client/server keys;
  - `AmneziaWG 2.0` obfuscation parameters (`H*`, `S*`, `I*`).
- The panel relays the generated tunnel data to the selected `Tic`.
- The `Tak` tunnel listen port is random and non-standard in the current phase.
- The first-release default should use a dedicated point-to-point tunnel subnet
  per pair, preferably `/30`.
- `Tak` should perform outbound `SNAT/MASQUERADE` for traffic arriving from the
  `Tic` tunnel.
- If the tunnel fails, affected `via_tak` interfaces on `Tic` temporarily fall
  back to `standalone`, and return to `via_tak` after tunnel recovery.
- System traffic of the `Tic` host itself must stay outside this tunnel.

The first implementation slice should use a dedicated tunnel lifecycle with
four actions:

- `provision_tak_tunnel`
- `attach_tak_tunnel`
- `verify_tak_tunnel_status`
- `detach_tak_tunnel`

Recommended ownership:

- `tak-agent` provisions the server side, port, subnet, and generated
  `AmneziaWG 2.0` payload;
- `tic-agent` attaches that payload, maintains reconnect behavior, and drives
  interface fallback to `standalone` on failure.

## 8. What the Agent Must Not Own

The agent must not take over panel business rules such as:

- peer limit enforcement
- user ownership logic
- preview-mode rules
- dashboard visibility
- admin vs user permissions
- deciding which filters belong to which user in the database

It may receive effective runtime flags, but it must not become the source of
truth for those business rules.

## 9. Transition Rule From Current Scaffold

The scaffold is acceptable as long as every next step follows this migration
pattern:

1. keep existing action names and payloads stable;
2. keep `filesystem` mode working;
3. replace planned/mock command paths with real `system` execution gradually;
4. keep stdout JSON contract unchanged from the panel point of view;
5. prefer standard Linux/WireGuard behavior over custom logic.

This means the current partial implementation is not a dead end. It is a
controlled scaffold that must now converge toward real Ubuntu/WireGuard
execution.

## 10. Immediate Next Technical Step

The next production-oriented step should be:

- keep `prepare_interface` computational;
- upgrade `create_interface` in `system` mode first;
- write final config into the WireGuard-compatible layout;
- validate runtime preconditions before applying anything;
- only then execute the first real Linux/WireGuard command path.

Do not try to make the whole agent fully live in one step. The correct path is
one vertical slice at a time.
