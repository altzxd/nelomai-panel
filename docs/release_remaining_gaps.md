# Nelomai Remaining Release Gaps

This document lists what still remains outside the codebase before GitHub
publication and before the first production deployment.

## 1. GitHub Publication

Before publishing the repository:

- verify the working tree does not contain accidental local artifacts to stage;
- ensure the final `.gitignore` is respected by the local Git repository;
- confirm no local `.env` file or private key file is present in the repo root.

## 2. Production Environment

Before the first deployment:

- replace the development `SECRET_KEY` with a real production secret;
- replace SQLite with PostgreSQL in `DATABASE_URL`;
- configure the real production `PEER_AGENT_COMMAND`;
- keep `DEBUG=false` in the release environment.

## 3. Panel Host

Before the first deployment:

- provision PostgreSQL and verify connectivity from the panel host;
- configure reverse proxy and TLS;
- install and enable the panel `systemd` unit;
- make the backup storage path writable and persistent.

## 4. Tic / Tak Host Reality Check

Before live bootstrap on real servers:

- validate the SSH bootstrap flow against a real blank Ubuntu 22.04 host;
- verify package installation, repository rollout, `npm install`, and `systemd` activation on a live host;
- verify `safe-init` and `full` profiles against the real server image.

## 5. Final Rule

The codebase is functionally ready for GitHub when:

1. all automated checks pass;
2. remaining gaps are limited to deployment secrets and real infrastructure;
3. the team understands that the unresolved items are environment tasks, not code defects.
