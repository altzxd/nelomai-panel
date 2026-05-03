# Nelomai Release Gates

This document fixes the minimum gates before publishing the repository to
GitHub and before the first release deployment.

## 1. Repository Gates

Before GitHub publication:

- release hygiene check passes;
- no local `.env` file is required in the repository;
- `.tmp/`, local databases, caches, and build artifacts are ignored;
- inventory and release documents for the panel server are present.

## 2. Panel Runtime Gates

Before release:

- clean-start check passes on a fresh database;
- migration check passes;
- startup check passes;
- production config rules are enforced for release profile;
- security/access check passes.

## 3. Agent / Bootstrap Gates

Before release:

- Node agent contract checks pass;
- `safe-init` bootstrap profile check passes;
- `full` bootstrap profile check passes;
- panel bootstrap profile view check passes;
- panel E2E bootstrap check passes;
- SSH prompt and SSH exec checks pass;
- live `Tic ↔ Tak` tunnel validation passes;
- live panel tunnel-artifact rotation validation passes;
- live panel fallback validation passes;
- live panel backoff/manual-attention validation passes;
- live panel partial-repair validation passes;
- live panel manual-repair validation passes;
- optional live panel multi-`Tak` switch validation is documented and skip-safe;
- the combined live `Tic ↔ Tak` health workflow is documented and runnable from one entrypoint.

## 4. Panel Server Gates

Before release:

- panel server inventory baseline is documented;
- panel server release checklist is documented;
- PostgreSQL, reverse proxy, TLS, and `systemd` are part of the release plan.

## 5. Remaining Non-Green Items In Local Dev

These are acceptable in local development but must be resolved for release:

- `DEBUG=true`;
- placeholder `SECRET_KEY`;
- SQLite `DATABASE_URL`;
- empty `PEER_AGENT_COMMAND`.

## 6. Final Pre-Release Rule

The repository is ready for GitHub publication only when:

1. `preflight_check.py` passes without failures.
2. Release-only configuration gaps are understood and documented.
3. No local secret or temporary artifact is required to understand the repo.
