# Nelomai Panel Beta Runbook

This runbook fixes the minimum preparation needed before starting a limited
beta rollout to a small group of real users.

## 1. Beta Goal

The beta stage is not the final release. Its purpose is:

- validate the core user and admin flows on real servers;
- collect feedback about stability, routing behavior, and UI;
- identify missing operational tooling before the full rollout.

## 2. Minimum Beta Topology

Prepare at least:

- one production-like panel server;
- one real `Tic` server;
- one real `Tak` server;
- one small test group of real users;
- one admin operator responsible for support during the beta.

The beta topology should already use:

- PostgreSQL;
- reverse proxy;
- TLS;
- `systemd`;
- production `SECRET_KEY`;
- production `DATABASE_URL`;
- production `PEER_AGENT_COMMAND`.

## 3. Required Beta Checks

Before inviting users, confirm:

- `preflight_check.py` passes without failures;
- `clean_start_check.py` passes;
- panel diagnostics page works;
- panel logs page works;
- panel jobs page works;
- live `Tic ↔ Tak` health workflow passes;
- backup storage path is writable;
- panel backups can be created and listed;
- server backups can be created and verified.

## 4. Minimum Supported Beta Scope

For the first beta, support only the core flow:

- add and bootstrap servers;
- bind `Tak` to `Tic`;
- create interface;
- create/download peer config;
- use `route_mode=via_tak`;
- allow automatic fallback `via_tak -> standalone -> via_tak`;
- allow manual repair / clear-backoff / rotate from diagnostics.

Do not expand beta scope until these flows are stable for the pilot group.

## 5. Operator Readiness

Before beta, the operator must know how to:

- add a server through the panel;
- read `/admin/diagnostics`;
- inspect `/admin/logs`;
- inspect `/admin/jobs`;
- run `Ротировать артефакты`;
- run `Снять backoff`;
- run `Восстановить пару`;
- move a pair back to `standalone` if needed.

## 6. Beta Rollback / Recovery

Before beta, define:

- who can stop the rollout;
- how to disable problematic users or interfaces;
- how to detach or repair a broken `Tak`;
- how to restart agents and services;
- how to restore panel/server state from backups.

The beta is not ready if rollback exists only in theory.

## 7. Feedback Loop

During the beta, collect at least:

- user-facing routing problems;
- UI/UX confusion points;
- failed bootstrap scenarios;
- repeated tunnel repairs or fallback events;
- diagnostics/logs gaps;
- recovery actions that required manual shell work.

Every beta issue should be triaged into:

- product/UI feedback;
- panel bug;
- agent bug;
- deployment/operations issue.

## 8. Exit Criteria For Full Rollout

Do not move to the full client rollout until:

- the pilot topology stays stable;
- repeated support actions are documented;
- no critical beta issue requires undocumented shell intervention;
- the panel and `Tic ↔ Tak` layers behave predictably for the test group.
