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
- `PANEL_PUBLIC_BASE_URL` with the real public panel URL;
- `PEER_AGENT_COMMAND` with the production SSH bridge command.

Do not deploy with:

- placeholder `SECRET_KEY`;
- SQLite `DATABASE_URL`;
- placeholder `PANEL_PUBLIC_BASE_URL`;
- empty `PEER_AGENT_COMMAND`.

## 3. Install the Application

Minimum install flow:

1. On Ubuntu 22.04, run `scripts/install_panel_server.sh` as `root` to:
   - install Python 3.11, PostgreSQL, OpenSSH client, `sshpass`, Nginx, and Certbot;
   - create the PostgreSQL user and database;
   - generate `SECRET_KEY` and `DATABASE_URL`;
   - write the panel `.env`;
   - create the virtual environment and install Python dependencies;
   - run `alembic upgrade head`;
   - install and start the panel `systemd` unit;
   - write the Nginx site and reload Nginx;
   - optionally request TLS through `certbot` if `PANEL_TLS_EMAIL` is set.
2. If you do not use the install script, create the Python environment,
   install project dependencies from `pyproject.toml`, and run
   `alembic upgrade head` manually.
3. Set at least:
   - `PANEL_PUBLIC_BASE_URL=https://nelomai.ru`;
   - `PANEL_TLS_EMAIL=<your-email>` if you want the script to request TLS.
4. After the script completes, inspect:
   - `systemctl status nelomai-panel.service`;
   - `journalctl -u nelomai-panel.service -n 50 --no-pager`.
5. On the first clean startup, read the one-time first-admin link from the
   panel terminal output: `Initial admin setup link: ...`.
6. Open `/bootstrap-admin/{token}` from that link and create the first
   administrator with only `login` and `password`.
7. Verify that `/` opens, the login page is reachable, and startup completes
   cleanly.

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
- the first clean startup prints the one-time first-admin bootstrap link;
- the first administrator can be created through `/bootstrap-admin/{token}`;
- release hygiene check passes;
- backup storage path is writable.
