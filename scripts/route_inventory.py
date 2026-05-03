from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi.routing import APIRoute

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.main import app


@dataclass(frozen=True)
class RouteRule:
    methods: tuple[str, ...]
    path: str
    access: str
    notes: str


ROUTE_RULES = [
    RouteRule(("GET",), "/", "public", "login page"),
    RouteRule(("POST",), "/login", "public", "sets JWT cookie"),
    RouteRule(("POST",), "/logout", "auth", "clears JWT cookie"),
    RouteRule(("GET",), "/dashboard", "auth/owner/admin-preview", "regular users see only themselves; admin can target users"),
    RouteRule(("GET",), "/admin", "admin", "main admin page"),
    RouteRule(("GET",), "/admin/servers", "admin", "servers page"),
    RouteRule(("GET",), "/admin/logs", "admin", "audit logs page"),
    RouteRule(("GET",), "/admin/jobs", "admin", "panel background jobs page"),
    RouteRule(("GET",), "/admin/diagnostics", "admin", "panel self-diagnostics page"),
    RouteRule(("POST",), "/admin/diagnostics/run", "admin", "run panel self-diagnostics"),
    RouteRule(("POST",), "/admin/diagnostics/tak-tunnels/clear-backoff", "admin", "clear focused Tic/Tak tunnel backoff state from diagnostics"),
    RouteRule(("POST",), "/admin/diagnostics/tak-tunnels/repair", "admin/agent-backed", "repair focused Tic/Tak tunnel pair from diagnostics"),
    RouteRule(("POST",), "/admin/diagnostics/tak-tunnels/rotate", "admin/agent-backed", "rotate focused Tic/Tak tunnel artifacts from diagnostics"),
    RouteRule(("GET",), "/admin/agent-contract", "admin", "panel to Node-agent contract page"),
    RouteRule(("POST",), "/admin/logs/delete-all", "admin", "delete all audit logs form action"),
    RouteRule(("GET",), "/api/admin/agent-contract", "admin", "JSON contract manifest for future Node-agent"),
    RouteRule(("POST",), "/api/admin/jobs/expired-peers/run", "admin/agent-backed", "run expired peers cleanup job"),
    RouteRule(("POST",), "/api/admin/jobs/{job_id}/cancel", "admin", "cancel queued, running or stuck panel job"),
    RouteRule(("POST",), "/api/admin/servers", "admin/agent-backed", "bootstrap server task"),
    RouteRule(("GET",), "/api/admin/server-bootstrap/{task_id}", "admin", "bootstrap task status"),
    RouteRule(("POST",), "/api/admin/server-bootstrap/{task_id}/input", "admin", "interactive bootstrap input"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/restart-agent", "admin/agent-backed", "restart Node-agent"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/refresh", "admin/agent-backed", "refresh server status"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/runtime-check", "admin/agent-backed", "verify agent runtime readiness"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/reboot", "admin/agent-backed", "reboot server host"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/exclude", "admin", "exclude server"),
    RouteRule(("POST",), "/api/admin/servers/{server_id}/restore", "admin", "restore excluded server"),
    RouteRule(("DELETE",), "/api/admin/servers/{server_id}", "admin", "delete excluded server"),
    RouteRule(("GET",), "/api/users/{user_id}/resources", "auth/owner-or-admin", "read user resources"),
    RouteRule(("PUT",), "/api/users/{user_id}/resources", "admin", "admin edits resources; preview denied"),
    RouteRule(("DELETE",), "/api/users/{user_id}/resources", "admin", "admin clears resources; preview denied"),
    RouteRule(("GET",), "/api/users/{user_id}/filters", "auth/owner-or-admin", "read user/global filters"),
    RouteRule(("POST",), "/api/users/{user_id}/filters", "auth/owner-or-admin", "create user filters; global requires admin; preview denied"),
    RouteRule(("POST",), "/api/admin/filters", "admin", "create global filter"),
    RouteRule(("POST",), "/api/admin/filters/delete", "admin", "bulk delete filters"),
    RouteRule(("PATCH",), "/api/filters/{filter_id}", "auth/owner-or-admin", "edit own user filter or admin global filter; preview denied"),
    RouteRule(("DELETE",), "/api/filters/{filter_id}", "auth/owner-or-admin", "delete own user filter or admin global filter; preview denied"),
    RouteRule(("PUT",), "/api/admin/settings/basic", "admin", "basic settings"),
    RouteRule(("PUT",), "/api/admin/settings/updates", "admin", "Git/update settings"),
    RouteRule(("PUT",), "/api/admin/settings/logs", "admin", "audit log retention settings"),
    RouteRule(("PUT",), "/api/admin/settings/backups", "admin", "backup settings"),
    RouteRule(("POST",), "/api/admin/backups", "admin/agent-backed", "create backup archive"),
    RouteRule(("GET",), "/api/admin/backups/download-all", "admin", "download all panel backup archives"),
    RouteRule(("POST",), "/api/admin/backups/delete-all-except-latest", "admin", "delete all panel backups except latest"),
    RouteRule(("POST",), "/api/admin/backups/latest-full/verify-server-copies", "admin/agent-backed", "verify latest full backup server copies"),
    RouteRule(("POST",), "/api/admin/backups/scheduled/run-now", "admin/agent-backed", "run scheduled full backup now"),
    RouteRule(("POST",), "/api/admin/backups/server-copies/cleanup", "admin/agent-backed", "delete server backup copies except latest"),
    RouteRule(("GET",), "/api/admin/backups/{backup_id}/download", "admin", "download backup archive"),
    RouteRule(("GET",), "/api/admin/backups/{backup_id}/restore-plan", "admin", "dry-run backup restore plan"),
    RouteRule(("POST",), "/api/admin/backups/{backup_id}/restore-plan", "admin", "selected-user dry-run backup restore plan"),
    RouteRule(("POST",), "/api/admin/backups/{backup_id}/restore-users", "admin", "restore selected backup users"),
    RouteRule(("DELETE",), "/api/admin/backups/{backup_id}", "admin", "delete backup archive"),
    RouteRule(("GET",), "/api/admin/updates/check", "admin", "panel update check"),
    RouteRule(("GET",), "/api/admin/agent-updates/check", "admin/agent-backed", "check server agent updates"),
    RouteRule(("POST",), "/api/admin/agent-updates/apply", "admin/agent-backed", "apply server agent updates"),
    RouteRule(("POST",), "/api/admin/logs/cleanup", "admin", "delete old audit logs"),
    RouteRule(("DELETE",), "/api/admin/logs", "admin", "delete all audit logs"),
    RouteRule(("POST",), "/api/admin/interfaces/{interface_id}/toggle", "admin/agent-backed", "toggle interface"),
    RouteRule(("PUT",), "/api/admin/interfaces/{interface_id}/peer-limit", "admin", "peer limit"),
    RouteRule(("PUT",), "/api/admin/interfaces/{interface_id}/route-mode", "admin/agent-backed", "route mode"),
    RouteRule(("PUT",), "/api/admin/interfaces/{interface_id}/tak-server", "admin/agent-backed", "tak endpoint"),
    RouteRule(("PUT",), "/api/admin/interfaces/{interface_id}/exclusion-filters", "admin/agent-backed", "per-interface exclusion toggle"),
    RouteRule(("POST",), "/api/admin/interfaces", "admin/agent-backed", "create interface"),
    RouteRule(("POST",), "/api/admin/interfaces/prepare", "admin/agent-backed", "prepare interface allocation"),
    RouteRule(("POST",), "/api/interfaces/{interface_id}/peers", "auth/owner-or-admin", "create peer in own interface; preview denied"),
    RouteRule(("PUT",), "/api/peers/{peer_id}/comment", "auth/owner-or-admin", "edit own peer comment; preview denied"),
    RouteRule(("PUT",), "/api/peers/{peer_id}/expires", "admin", "peer lifetime; preview denied"),
    RouteRule(("PUT",), "/api/admin/peers/{peer_id}/block-filters", "admin/agent-backed", "per-peer block toggle"),
    RouteRule(("PUT",), "/api/admin/users/{user_id}/expires", "admin", "user expiration date; preview denied"),
    RouteRule(("POST",), "/api/peers/{peer_id}/download-link", "admin", "generate public peer download link; preview denied"),
    RouteRule(("DELETE",), "/api/admin/peer-download-links/{link_id}", "admin", "revoke public peer download link"),
    RouteRule(("POST",), "/api/admin/peer-download-links/revoke-all", "admin", "revoke all public peer links or lifetime-only links"),
    RouteRule(("POST",), "/api/peers/{peer_id}/recreate", "auth/owner-or-admin/agent-backed", "recreate own peer; preview denied"),
    RouteRule(("POST",), "/api/peers/{peer_id}/toggle", "auth/owner-or-admin/agent-backed", "toggle own peer; preview denied"),
    RouteRule(("GET",), "/downloads/peer/{token}", "public-token", "direct config download only"),
    RouteRule(("DELETE",), "/api/peers/{peer_id}", "auth/owner-or-admin/agent-backed", "delete own peer; preview denied"),
    RouteRule(("GET",), "/api/peers/{peer_id}/download", "auth/owner-or-admin/agent-backed", "download own peer config"),
    RouteRule(("GET",), "/api/interfaces/{interface_id}/download-all", "auth/owner-or-admin/agent-backed", "download own interface bundle"),
    RouteRule(("POST",), "/api/admin/users/{user_id}/assign-interface/{interface_id}", "admin", "assign pending interface"),
    RouteRule(("POST",), "/api/admin/users/{user_id}/detach-interface/{interface_id}", "admin", "detach interface; preview denied"),
    RouteRule(("DELETE",), "/api/admin/interfaces/{interface_id}", "admin", "delete pending interface"),
    RouteRule(("POST",), "/api/admin/users", "admin", "create user"),
    RouteRule(("DELETE",), "/api/admin/users/{user_id}", "admin", "delete user"),
    RouteRule(("PUT",), "/api/admin/users/{user_id}/channel", "admin", "communication channel"),
    RouteRule(("PUT",), "/api/admin/users/{user_id}/name", "admin", "display name"),
]


def normalize_methods(route: APIRoute) -> tuple[str, ...]:
    return tuple(sorted(method for method in route.methods if method not in {"HEAD", "OPTIONS"}))


def route_key(methods: tuple[str, ...], path: str) -> tuple[tuple[str, ...], str]:
    return methods, path


def run() -> None:
    rules = {route_key(rule.methods, rule.path): rule for rule in ROUTE_RULES}
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and not route.path.startswith(("/docs", "/redoc", "/openapi"))
    ]
    route_keys = {route_key(normalize_methods(route), route.path): route for route in routes}

    missing_rules = sorted(route_keys.keys() - rules.keys(), key=lambda item: (item[1], item[0]))
    stale_rules = sorted(rules.keys() - route_keys.keys(), key=lambda item: (item[1], item[0]))

    print("Route Inventory")
    print("===============")
    for key in sorted(route_keys.keys(), key=lambda item: (item[1], item[0])):
        rule = rules.get(key)
        methods, path = key
        access = rule.access if rule else "UNCLASSIFIED"
        notes = rule.notes if rule else "missing route rule"
        print(f"{','.join(methods):7} {path:58} {access:34} {notes}")

    if missing_rules or stale_rules:
        if missing_rules:
            print("\nMissing route rules:", file=sys.stderr)
            for methods, path in missing_rules:
                print(f"- {','.join(methods)} {path}", file=sys.stderr)
        if stale_rules:
            print("\nStale route rules:", file=sys.stderr)
            for methods, path in stale_rules:
                print(f"- {','.join(methods)} {path}", file=sys.stderr)
        raise SystemExit(1)

    print("\nOK: route inventory check passed")


if __name__ == "__main__":
    run()
