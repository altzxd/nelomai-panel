# Nelomai Panel Server Runbook

This runbook fixes the minimum production-oriented setup steps for the panel
server before the first release.

## 1. Prepare the Host

Required baseline:

- Linux host with `systemd`;
- Python 3.11+;
- PostgreSQL reachable from the panel host;
- reverse proxy with TLS;
- Git and OpenSSH client;
- writable path for panel backups.

## 2. Configure Environment

Start from `.env.example` and replace at least:

- `DEBUG=false`;
- `SECRET_KEY` with a real long production secret;
- `DATABASE_URL` with the real PostgreSQL connection string;
- `PEER_AGENT_COMMAND` with the production agent command.

Do not deploy with:

- placeholder `SECRET_KEY`;
- SQLite `DATABASE_URL`;
- empty `PEER_AGENT_COMMAND`.

## 3. Install the Application

Minimum install flow:

1. Create the Python environment.
2. Install project dependencies from `pyproject.toml`.
3. Run `alembic upgrade head`.
4. Start the FastAPI application behind the reverse proxy.
5. Verify that `/` opens and startup completes cleanly.

## 4. Service Layer

The panel release should run as a managed service:

- panel `systemd` unit installed;
- panel service enabled;
- panel service restartable;
- reverse proxy and TLS active in front of the service.

## 5. Final Pre-Release Checks

Before release, confirm:

- `preflight_check.py` passes;
- production config rules pass;
- clean-start check passes;
- release hygiene check passes;
- backup storage path is writable.
