# Nelomai Panel Server Release Checklist

This document fixes the minimum production checklist for the panel host before
the first public release. It is narrower than the panel inventory: the goal is
not to describe the server in general, but to list the concrete conditions that
must be satisfied before the release is considered deployable.

## 1. Core Runtime

The panel release requires:

- Python 3.11+;
- project dependencies from `pyproject.toml`;
- PostgreSQL as the production database;
- a managed application process through `systemd`.

## 2. Front Door

The panel release requires:

- reverse proxy in front of the application;
- TLS termination;
- production hostname and external routing configured.

## 3. Production Secrets and Configuration

Before release, confirm:

- `SECRET_KEY` is a real production secret;
- `DATABASE_URL` points to PostgreSQL;
- `PANEL_PUBLIC_BASE_URL` points to the real external panel URL;
- `PANEL_TLS_EMAIL` is set if TLS is provisioned through the install script;
- `PEER_AGENT_COMMAND` points to the production SSH bridge path;
- production `.env` values are not development placeholders.

## 4. Panel Service Layer

Before release, confirm:

- panel `systemd` unit exists;
- panel service is enabled;
- panel service starts cleanly after restart;
- schema migrations are applied.
- Nginx config proxies to the panel service.

## 5. Storage and Backups

Before release, confirm:

- backup storage path exists and is writable;
- backup retention is configured;
- panel can create and keep backups on disk.

## 6. First Release Checklist

The first release is blocked until all of these are true:

1. PostgreSQL is provisioned and reachable from the panel host.
2. Reverse proxy and TLS are configured.
3. Panel `systemd` unit is installed, enabled, and restartable.
4. Production `SECRET_KEY` is set.
5. Production `DATABASE_URL` is set.
6. Production `PANEL_PUBLIC_BASE_URL` is set.
7. Production `PEER_AGENT_COMMAND` is set.
8. Migrations are applied.
9. Nginx and TLS front the panel service.
10. Backup storage path is writable.
