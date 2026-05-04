# Nelomai Panel Roadmap

This file reflects the current real state of the project, not the original plan.

## Baseline

The system is already running on production-like infrastructure:

- panel: `https://nelomai.ru`
- panel host: PostgreSQL, Nginx, TLS, systemd
- `1a`: live `Tic` server
- `2a`: live `Tak` server

Current baseline already includes:

- public registration links
- first-admin bootstrap link from terminal
- user login/logout
- login rate limit / lockout
- encrypted SSH passwords in DB
- secure auth cookie on HTTPS
- audit logs and cleanup
- jobs page and cleanup
- diagnostics
- updates page
- server bootstrap flows
- `standalone` interfaces
- `via_tak` interfaces through shared `Tic ↔ Tak` tunnel
- QR generation for peer configs
- live handshake / RX / TX for peers
- reboot recovery for panel, agents, `WG` and `AWG`
- server hardening baseline:
  - key-only SSH
  - secret file permissions
  - base firewall rules
- dynamic `UFW` opening only for active WireGuard listen ports
- real server metrics for `Tic/Tak` in panel

## Repository Rules

- Do not commit secrets, passwords, private keys, server credentials, bootstrap tokens, or production `.env`.
- `HANDOFF.md` is local-only and must stay out of Git.
- Keep panel-side and agent-side changes synchronized in GitHub and on live hosts.
- Prefer verified functional changes over UI polish.

## Current Verified Scope

These flows are considered implemented and working:

### Panel

- admin authentication
- public registration
- auto-created interfaces during registration
- user dashboard
- admin overview
- servers page
- diagnostics
- logs
- jobs
- updates
- settings
- backups

### User Access

- create user
- assign interfaces
- download peer config
- QR import path
- `standalone` internet access
- `via_tak` internet access
- live peer activity display

### Tic / Tak

- shared `AmneziaWG 2.0` tunnel
- tunnel fallback and auto-recovery
- cooldown / backoff / manual repair path
- peer lifecycle
- interface lifecycle
- tunnel artifact rotation
- reboot startup reconcile

## What Is Still Out Of Scope

These are intentionally not treated as blockers for the first small test group:

- second real physical `Tak` host for true multi-host switch
- zero-downtime switch between two physical `Tak` hosts
- large UI redesign
- deep analytics / statistics beyond operational needs
- full automation of every panel-host deploy decision

## Immediate Priorities

These are the most useful next blocks after the current baseline:

1. Pilot stabilization
- run repeated real-user scenarios with new accounts
- catch UX delays and edge cases under real usage
- watch logs, diagnostics, jobs, and tunnel state during active usage

2. Metrics refinement
- keep real `Tic/Tak` metrics in panel
- decide whether to add:
  - short history
  - refresh timestamps
  - lightweight caching if page load becomes heavy

3. Operational cleanup
- reduce remaining noisy or redundant admin text
- tighten labels and wording across admin/user pages
- keep task/log cleanup practical during pilot

4. Release safety
- if any sensitive data ever reached Git history, rotate it
- keep hardening in bootstrap/install paths for future blank hosts
- verify clean bootstrap again when a new blank host becomes available

## Before First Test Group

Minimal checklist before opening access to the first external users:

1. registration link creates account successfully
2. optional auto-interface creation works
3. `standalone` config gives internet
4. `via_tak` config gives internet
5. QR import works
6. handshake / RX / TX appear in panel
7. panel, `1a`, and `2a` survive reboot
8. logs / diagnostics / jobs stay clean enough for support

## After Pilot Starts

Once first testers are active, prioritize only issues that affect:

- registration
- login
- interface creation
- config delivery
- `standalone`
- `via_tak`
- server/tunnel recovery
- admin visibility during incidents

Everything else should stay secondary until real pilot feedback is collected.
