# Nelomai Panel Roadmap

This roadmap tracks panel work only. Tic/Tak server agents are separate projects; panel-side code should expose clear contracts for the future Tic Node-agent without implementing server logic here.

## Working Rules

- Finish functional stability before visual polish.
- Keep changes small, complete, and verified.
- Do not break login, `/dashboard`, preview-mode, user resources, filters, route_mode, server page, or existing checks.
- Do not store secrets, passwords, tokens, or production `.env` values in the repository.
- For Tic/Tak actions, implement panel-side contracts and leave clear comments for future Node-agent behavior.

## Current Verification Baseline

Run before and after meaningful functional changes:

```powershell
python scripts\smoke_check.py
python scripts\agent_contract_check.py
python scripts\integrity_check.py
python scripts\config_check.py
python scripts\route_inventory.py
```

Existing checks cover:

- Basic page load and auth smoke flow.
- Preview-mode write protection.
- User resources and filters CRUD.
- Owner/admin access boundaries.
- Peer/interface permission boundaries.
- Agent payload contract with fake Tic executor.
- Data integrity invariants.
- Dev/production config readiness.
- Route inventory and access classification.

## Phase 1: Finish Functional Panel Flows

Goal: bring panel functionality to 100% before visual polish.

- Keep main admin page stable.
- Keep user dashboard stable.
- Keep server page stable.
- Keep interface creation/assignment/detach/delete flows stable.
- Keep peer create/recreate/delete/download/download-all flows stable.
- Keep route_mode and Tic/Tak endpoint controls stable.
- Keep user resources and contact links stable.
- Keep exclusion filters and block filters stable.
- Keep per-interface exclusion filter toggle stable.
- Keep per-peer block filter toggle stable.
- Keep admin account able to own assigned interfaces.
- Keep pending-owner interfaces out of normal user dashboards.
- Keep invalid interfaces visible but not assignable.

## Phase 2: Logs

This is the next planned feature phase.

- Add panel-side audit/event logs.
- Separate panel events from future Tic/Tak agent logs.
- Log admin-visible actions, for example:
  - login success/failure;
  - user creation/deletion/name/channel changes;
  - interface creation/assignment/detach/delete/toggle;
  - server add/exclude/restore/delete/restart/reboot;
  - peer create/recreate/delete/toggle/download link generation;
  - resource edits;
  - global/user filter create/update/delete;
  - settings changes.
- Add admin log viewer.
- Add basic filtering/searching by event type, actor, user, interface, server, and date if it stays small.
- Preserve logs as support/debug data, not as user-facing noise.

## Phase 3: Sidebar Statistics

Non-critical feature after logs and before Telegram bot.

- Add statistics area to sidebar or sidebar-adjacent panel.
- Exact contents will be defined later.
- Prefer deriving statistics from normalized panel data and logs.
- Keep it lightweight and avoid slowing normal page load.

## Phase 4: Self-Diagnostics

Operational feature after statistics and before Telegram bot.

- Add a panel self-diagnostics tool that runs on demand.
- The tool should check the current health of panel-side subsystems and contracts, for example:
  - database connectivity and migrations state;
  - critical settings completeness;
  - background jobs state;
  - backup storage availability;
  - panel-to-agent command path readiness;
  - server reachability summaries already known to the panel.
- When issues are found, show a compact diagnosis with problematic nodes/subsystems instead of only raw failures.
- Keep it safe for production use: diagnostics should inspect, not mutate, unless a later repair mode is explicitly added.

## Phase 5: Telegram Bot

Penultimate feature stage before release preparation.

- Add Telegram bot for reminders about approaching expiration dates.
- Use expiration data from users/interfaces/peers as needed.
- Current “Действует до” should not automatically disable interfaces.
- Peer lifetime remains separate: peer can have an exact expiry date/time and be deleted after expiration.
- Bot should remind, not replace panel permissions.

## Phase 6: Security And Release Preparation

Final stage before GitHub release/deploy.

- Run final security pass.
- Run a panel-server package inventory before first release:
  - record what is already installed on the panel host;
  - compare it against actual panel runtime requirements;
  - add missing packages/services to release/install instructions.
- Confirm every page and endpoint has expected access behavior.
- Confirm unauthenticated users are redirected to login except public download links.
- Confirm ordinary users only access their personal page and user-visible sidebar actions.
- Confirm public download links do not expose panel pages or inherited UI state.
- Confirm production config:
  - `DEBUG=false`;
  - strong non-default `SECRET_KEY`;
  - PostgreSQL `DATABASE_URL`;
  - configured `PEER_AGENT_COMMAND`;
  - no committed secrets.
- Confirm migrations/deploy notes are ready.
- Confirm Tic/Tak bootstrap docs assume blank Ubuntu 22.04 hosts and install all required software from bootstrap, not from preinstalled images.
- Clean repository.
- Push to GitHub.
- Prepare release/install instructions.

## Future Tic Node-agent Contract Notes

Panel already has contract checks for a fake executor.

The future Tic Node-agent should handle:

- prepare interface allocation;
- create interface with chosen `listen_port` and `address_v4`;
- reject busy port/address without creating panel record;
- recreate peer under the same slot/name;
- delete peer config;
- download peer config;
- download interface bundle as zip;
- toggle peer/interface state;
- apply route mode;
- apply exclusion filters at the interface/user level as currently contracted;
- apply block filters per peer as currently contracted.

## Future Tak Tunnel Notes

- `Tak` should later own the server side of the shared `AmneziaWG 2.0`
  inter-server tunnel used by `via_tak`.
- Manual editing of the Tak tunnel listen port is not required in the current
  phase, but may be added later as an advanced setting.
- The first tunnel implementation slice should start with a dedicated lifecycle:
  - `provision_tak_tunnel`
  - `attach_tak_tunnel`
  - `verify_tak_tunnel_status`
  - `detach_tak_tunnel`

The panel owns:

- user accounts and permissions;
- peer limits;
- user resources;
- filter ownership and UI;
- panel-side preview-mode;
- audit logs and statistics;
- Telegram reminder logic.

## Visual Polish

Visual/design refinement should come after functional completion.

- Keep current layout usable while functional work continues.
- Later improve spacing, typography, dashboard density, responsive behavior, and final visual hierarchy.
- Avoid large UI rewrites until the functional roadmap is stable.
