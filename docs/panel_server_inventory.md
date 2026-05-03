# Nelomai Panel Server Inventory

This document fixes the minimum server-side inventory for the panel host before
the first release. Unlike Tic and Tak servers, the panel host is not assumed to
start as a blank machine retroactively. The task here is to compare the real
host with this baseline and add what is missing.

## 1. Target Role

Panel server responsibilities:

- run the FastAPI application;
- run schema migrations;
- keep panel backups and diagnostics data;
- invoke `PEER_AGENT_COMMAND` for agent-backed actions;
- expose the panel through a reverse proxy and TLS.

## 2. Operating System Baseline

Target server profile for the first release:

- Ubuntu 22.04 LTS or another explicitly supported Linux host;
- systemd-managed service environment;
- writable filesystem paths for panel state, logs, and backups.

## 3. Required Runtime

The panel server must have:

- Python 3.11+;
- project dependencies installed from `pyproject.toml`;
- PostgreSQL for production `DATABASE_URL`;
- OpenSSH client for remote bootstrap and agent SSH transport;
- `sshpass` for password-based server bootstrap and agent SSH transport;
- Git for updates and repository rollout;
- archive tooling for backups: `tar`, `zip`, `unzip`;
- reverse proxy with TLS termination.

## 4. Required Process / Service Layer

The panel server must have:

- a managed application process, typically `systemd`;
- reverse proxy service such as Nginx or Caddy;
- database connectivity to PostgreSQL;
- writable backup storage path for panel-generated backups.

## 5. Required Configuration

The release inventory must confirm:

- `SECRET_KEY` is replaced with a real production value;
- `DATABASE_URL` points to PostgreSQL, not SQLite;
- `PEER_AGENT_COMMAND` is configured for the production SSH bridge path;
- backup retention and storage settings are writable on the host.

## 6. Release Inventory Checklist

Before the first release, compare the real panel server against this list:

1. OS and service model are compatible with Linux + systemd.
2. Python 3.11+ is installed.
3. `pyproject.toml` dependencies are installed.
4. PostgreSQL is provisioned and reachable.
5. `SECRET_KEY`, `DATABASE_URL`, and `PEER_AGENT_COMMAND` are configured.
6. OpenSSH client and `sshpass` are installed for remote agent transport.
7. Git, `tar`, `zip`, and `unzip` are installed.
8. Reverse proxy and TLS are configured.
9. Backup storage path is writable and retained on disk.
