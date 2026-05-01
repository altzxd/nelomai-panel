from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import joinedload

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.models import (
    FilterKind,
    FilterScope,
    Interface,
    Peer,
    ResourceFilter,
    RouteMode,
    Server,
    ServerType,
    User,
    UserResource,
    UserRole,
)


class IntegrityFailure(RuntimeError):
    pass


def add_issue(issues: list[str], message: str) -> None:
    issues.append(message)


def check_users_and_resources(issues: list[str]) -> None:
    with SessionLocal() as db:
        users = db.execute(
            select(User).options(
                joinedload(User.resources),
                joinedload(User.contact_link_record),
                joinedload(User.interfaces).joinedload(Interface.tic_server),
            )
        ).unique().scalars().all()
        resources = db.execute(select(UserResource)).scalars().all()
        admin_count = sum(1 for user in users if user.role == UserRole.ADMIN)

        if admin_count == 0:
            add_issue(issues, "there is no admin user")

        resource_user_ids = [resource.user_id for resource in resources]
        duplicates = [user_id for user_id, count in Counter(resource_user_ids).items() if count > 1]
        for user in users:
            if user.resources is None:
                add_issue(issues, f"user {user.id} ({user.login}) has no UserResource row")
            if user.contact_link_record is None:
                add_issue(issues, f"user {user.id} ({user.login}) has no UserContactLink row")
            if user.display_name is None or user.display_name == "":
                add_issue(issues, f"user {user.id} ({user.login}) has empty display_name")
            assigned_valid_interfaces = [
                interface
                for interface in user.interfaces
                if not interface.is_pending_owner and interface.tic_server is not None and not interface.tic_server.is_excluded
            ]
            if len(assigned_valid_interfaces) > 5:
                add_issue(
                    issues,
                    f"user {user.id} ({user.login}) has {len(assigned_valid_interfaces)} assigned valid interfaces; max is 5",
                )
        for user_id in duplicates:
            add_issue(issues, f"user {user_id} has duplicate UserResource rows")


def check_interfaces_and_peers(issues: list[str]) -> None:
    with SessionLocal() as db:
        interfaces = db.execute(
            select(Interface)
            .options(
                joinedload(Interface.user),
                joinedload(Interface.tic_server),
                joinedload(Interface.tak_server),
                joinedload(Interface.peers),
            )
        ).unique().scalars().all()

        ports_by_server: dict[int, list[Interface]] = defaultdict(list)
        addresses_by_server: dict[int, list[Interface]] = defaultdict(list)

        for interface in interfaces:
            if interface.peer_limit not in {5, 10, 15, 20}:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has invalid peer_limit={interface.peer_limit}")
            if interface.tic_server is None:
                add_issue(issues, f"interface {interface.id} ({interface.name}) points to missing tic_server_id={interface.tic_server_id}")
                continue
            if interface.tic_server.server_type != ServerType.TIC:
                add_issue(issues, f"interface {interface.id} ({interface.name}) is attached to non-Tic tic_server")
            if interface.tak_server is not None and interface.tak_server.server_type != ServerType.TAK:
                add_issue(issues, f"interface {interface.id} ({interface.name}) is attached to non-Tak tak_server")
            if interface.route_mode == RouteMode.VIA_TAK and interface.tak_server_id is None:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has via_tak route_mode without tak_server")
            if interface.tak_server_id is None and interface.route_mode != RouteMode.STANDALONE:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has no Tak server but route_mode={interface.route_mode}")
            if interface.listen_port < 10001:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has listen_port below 10001")
            if not interface.address_v4:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has empty address_v4")
            if interface.is_pending_owner and (interface.user is None or interface.user.role != UserRole.ADMIN):
                add_issue(issues, f"pending interface {interface.id} ({interface.name}) is not attached to an admin placeholder owner")
            if interface.is_pending_owner and any(peer.is_enabled for peer in interface.peers):
                add_issue(issues, f"pending interface {interface.id} ({interface.name}) has enabled peers")
            if interface.tic_server.is_excluded and any(peer.is_enabled for peer in interface.peers):
                add_issue(issues, f"interface {interface.id} ({interface.name}) is on excluded Tic server but has enabled peers")
            if len(interface.peers) > interface.peer_limit:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has {len(interface.peers)} peers; limit is {interface.peer_limit}")

            slots = [peer.slot for peer in interface.peers]
            duplicate_slots = [slot for slot, count in Counter(slots).items() if count > 1]
            for slot in duplicate_slots:
                add_issue(issues, f"interface {interface.id} ({interface.name}) has duplicate peer slot {slot}")
            for peer in interface.peers:
                if peer.slot < 1 or peer.slot > interface.peer_limit:
                    add_issue(
                        issues,
                        f"peer {peer.id} belongs to interface {interface.id} ({interface.name}) but slot {peer.slot} is outside 1..{interface.peer_limit}",
                    )
                if peer.traffic_7d_mb < 0 or peer.traffic_30d_mb < 0:
                    add_issue(issues, f"peer {peer.id} has negative traffic counters")

            ports_by_server[interface.tic_server_id].append(interface)
            addresses_by_server[interface.tic_server_id].append(interface)

        for tic_server_id, server_interfaces in ports_by_server.items():
            ports = [interface.listen_port for interface in server_interfaces]
            duplicate_ports = [port for port, count in Counter(ports).items() if count > 1]
            for port in duplicate_ports:
                names = ", ".join(interface.name for interface in server_interfaces if interface.listen_port == port)
                add_issue(issues, f"Tic server {tic_server_id} has duplicate listen_port {port}: {names}")

        for tic_server_id, server_interfaces in addresses_by_server.items():
            addresses = [interface.address_v4 for interface in server_interfaces]
            duplicate_addresses = [address for address, count in Counter(addresses).items() if count > 1]
            for address in duplicate_addresses:
                names = ", ".join(interface.name for interface in server_interfaces if interface.address_v4 == address)
                add_issue(issues, f"Tic server {tic_server_id} has duplicate address_v4 {address}: {names}")


def check_filters(issues: list[str]) -> None:
    with SessionLocal() as db:
        filters = db.execute(
            select(ResourceFilter)
            .options(
                joinedload(ResourceFilter.user),
                joinedload(ResourceFilter.peer).joinedload(Peer.interface),
            )
        ).unique().scalars().all()

        for resource_filter in filters:
            label = f"filter {resource_filter.id} ({resource_filter.name})"
            if resource_filter.scope == FilterScope.GLOBAL:
                if resource_filter.user_id is not None:
                    add_issue(issues, f"{label} is global but has user_id={resource_filter.user_id}")
                if resource_filter.peer_id is not None:
                    add_issue(issues, f"{label} is global but has peer_id={resource_filter.peer_id}")
            if resource_filter.scope == FilterScope.USER:
                if resource_filter.user_id is None:
                    add_issue(issues, f"{label} is user-scoped but has no user_id")
                if resource_filter.user is None:
                    add_issue(issues, f"{label} points to missing user_id={resource_filter.user_id}")

            if resource_filter.kind == FilterKind.BLOCK and resource_filter.scope == FilterScope.USER:
                if resource_filter.peer_id is None:
                    add_issue(issues, f"{label} is a user block filter but has no peer_id")
                elif resource_filter.peer is None:
                    add_issue(issues, f"{label} points to missing peer_id={resource_filter.peer_id}")
                elif resource_filter.peer.interface.user_id != resource_filter.user_id:
                    add_issue(
                        issues,
                        f"{label} belongs to user_id={resource_filter.user_id} but peer {resource_filter.peer_id} belongs to user_id={resource_filter.peer.interface.user_id}",
                    )
            if resource_filter.kind == FilterKind.EXCLUSION and resource_filter.peer_id is not None:
                add_issue(issues, f"{label} is an exclusion filter but has peer_id={resource_filter.peer_id}")


def check_servers(issues: list[str]) -> None:
    with SessionLocal() as db:
        servers = db.execute(
            select(Server)
            .options(
                joinedload(Server.tic_interfaces).joinedload(Interface.peers),
                joinedload(Server.tak_interfaces),
            )
        ).unique().scalars().all()

        names = [server.name.strip().lower() for server in servers]
        duplicate_names = [name for name, count in Counter(names).items() if count > 1]
        for name in duplicate_names:
            add_issue(issues, f"duplicate server name after normalization: {name}")

        for server in servers:
            if not server.name.strip():
                add_issue(issues, f"server {server.id} has empty name")
            if not server.host.strip():
                add_issue(issues, f"server {server.id} ({server.name}) has empty host")
            if server.ssh_port < 1 or server.ssh_port > 65535:
                add_issue(issues, f"server {server.id} ({server.name}) has invalid ssh_port={server.ssh_port}")
            if server.server_type == ServerType.TAK and server.tic_interfaces:
                add_issue(issues, f"Tak server {server.id} ({server.name}) is used as Tic owner for interfaces")
            if server.server_type == ServerType.TIC and server.tak_interfaces:
                add_issue(issues, f"Tic server {server.id} ({server.name}) is used as Tak endpoint for interfaces")
            if server.server_type == ServerType.STORAGE and (server.tic_interfaces or server.tak_interfaces):
                add_issue(issues, f"Storage server {server.id} ({server.name}) is used by WireGuard interfaces")
            if server.is_excluded:
                enabled_peer_ids = [
                    peer.id
                    for interface in server.tic_interfaces
                    for peer in interface.peers
                    if peer.is_enabled
                ]
                if enabled_peer_ids:
                    add_issue(issues, f"excluded server {server.id} ({server.name}) has enabled peers: {enabled_peer_ids}")


def run() -> None:
    issues: list[str] = []
    check_users_and_resources(issues)
    check_servers(issues)
    check_interfaces_and_peers(issues)
    check_filters(issues)

    if issues:
        print("FAIL: integrity check found issues", file=sys.stderr)
        for index, issue in enumerate(issues, start=1):
            print(f"{index}. {issue}", file=sys.stderr)
        raise SystemExit(1)

    print("OK: integrity check passed")


if __name__ == "__main__":
    run()
