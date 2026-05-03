from datetime import UTC, datetime

from app.models import AppSetting, FilterKind, FilterScope, Interface, ResourceFilter, RouteMode, Server, User, UserRegion, UserResource, UserRole
from app.security import create_auth_download_token
from app.schemas import (
    AccessUserView,
    AdminPageView,
    BetaReadinessSummaryView,
    BasicSettingsView,
    ClientInterfaceOptionView,
    ClientView,
    FilterView,
    InterfaceSummaryView,
    InterfaceView,
    PeerView,
    ResourceItemView,
    ServerCardView,
    ServerDetailView,
    ServerBootstrapListItemView,
    ServerListItemView,
    ServerOptionView,
    ServersPageView,
    TakTunnelPairStateView,
    UserDashboardView,
)
from app.version import get_panel_version


def _normalize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _tak_tunnel_status_label(status: str | None) -> str | None:
    normalized = str(status or "").strip().lower()
    if not normalized:
        return None
    labels = {
        "active": "активен",
        "recovered": "автовосстановлен",
        "error": "ошибка",
        "detached": "отключён",
        "attached": "подключён",
        "provisioned": "подготовлен",
        "missing": "отсутствует",
    }
    labels["cooldown"] = "ожидает повторную попытку"
    labels["manual_attention_required"] = "требует ручного вмешательства"
    return labels.get(normalized, normalized)


def _user_region_label(value: UserRegion | None) -> str | None:
    if value == UserRegion.EUROPE:
        return "Европа"
    if value == UserRegion.EAST:
        return "Восток"
    if value == UserRegion.UNKNOWN:
        return "Не знаю/затрудняюсь ответить"
    return None


def _peer_download_url(peer) -> str | None:
    interface = getattr(peer, "interface", None)
    owner_user_id = getattr(interface, "user_id", None)
    peer_id = getattr(peer, "id", None)
    if not isinstance(peer_id, int) or not isinstance(owner_user_id, int):
        return None
    token = create_auth_download_token(
        scope="peer_auth_download",
        resource_id=peer_id,
        owner_user_id=owner_user_id,
    )
    return f"/downloads/auth/{token}"


def _interface_download_url(interface: Interface) -> str | None:
    interface_id = getattr(interface, "id", None)
    owner_user_id = getattr(interface, "user_id", None)
    if not isinstance(interface_id, int) or not isinstance(owner_user_id, int):
        return None
    token = create_auth_download_token(
        scope="interface_auth_download",
        resource_id=interface_id,
        owner_user_id=owner_user_id,
    )
    return f"/downloads/auth/{token}"


def serialize_interface(interface: Interface, expires_at) -> InterfaceView:
    configured_route_mode = interface.route_mode if interface.tak_server_id else RouteMode.STANDALONE
    effective_route_mode = RouteMode.STANDALONE if interface.tak_tunnel_fallback_active else configured_route_mode
    return InterfaceView(
        id=interface.id,
        agent_interface_id=interface.agent_interface_id,
        name=interface.name,
        download_url=_interface_download_url(interface),
        tic_server_name=interface.tic_server.name,
        route_mode=configured_route_mode,
        effective_route_mode=effective_route_mode,
        tak_server_id=interface.tak_server_id,
        tak_server_name=interface.tak_server.name if getattr(interface, "tak_server", None) is not None else None,
        tak_tunnel_fallback_active=interface.tak_tunnel_fallback_active,
        tak_tunnel_last_status=interface.tak_tunnel_last_status,
        tak_tunnel_status_label=_tak_tunnel_status_label(interface.tak_tunnel_last_status),
        tak_tunnel_auto_recovered=interface.tak_tunnel_last_status == "recovered",
        available_tak_options=[
            {"id": server.id, "name": server.name}
            for server in getattr(interface, "available_tak_options", [])
        ],
        peer_limit=interface.peer_limit,
        expires_at=expires_at,
        is_enabled=any(peer.is_enabled for peer in interface.peers),
        is_invalid=bool(interface.tic_server and interface.tic_server.is_excluded),
        exclusion_filters_enabled=interface.exclusion_filters_enabled,
        peers=[
            PeerView(
                id=peer.id,
                slot=peer.slot,
                download_url=_peer_download_url(peer),
                comment=peer.comment,
                is_enabled=peer.is_enabled,
                block_filters_enabled=peer.block_filters_enabled,
                expires_at=peer.expires_at,
                handshake_at=peer.handshake_at,
                live_rx_bytes=peer.live_rx_bytes,
                live_tx_bytes=peer.live_tx_bytes,
                traffic_7d_mb=peer.traffic_7d_mb,
                traffic_30d_mb=peer.traffic_30d_mb,
            )
            for peer in sorted(interface.peers, key=lambda item: item.slot)
        ],
    )


def serialize_resources(resources: UserResource | None, user_id: int) -> list[ResourceItemView]:
    hydrated = resources or UserResource(user_id=user_id)
    return [
        ResourceItemView(key="yandex_disk_url", label="Яндекс.Диск", value=hydrated.yandex_disk_url),
        ResourceItemView(key="amnezia_vpn_finland", label="AmneziaVPN Finland", value=hydrated.amnezia_vpn_finland),
        ResourceItemView(key="outline_japan", label="Outline Japan", value=hydrated.outline_japan),
    ]


def serialize_filters(global_filters: list[ResourceFilter], user_filters: list[ResourceFilter]) -> list[FilterView]:
    return [
        FilterView(
            id=item.id,
            name=item.name,
            kind=getattr(item, "kind", FilterKind.EXCLUSION),
            peer_id=item.peer_id,
            peer_label=(
                f"{item.peer.interface.name} / Peer {item.peer.slot}"
                if getattr(item, "peer", None) is not None and getattr(item.peer, "interface", None) is not None
                else None
            ),
            filter_type=item.filter_type,
            scope=item.scope,
            value=item.value,
            description=item.description,
            is_active=item.is_active,
        )
        for item in [*global_filters, *user_filters]
    ]


def serialize_dashboard(
    user: User,
    global_filters: list[ResourceFilter],
    preview_mode: bool,
    interfaces: list[Interface] | None = None,
    user_filters: list[ResourceFilter] | None = None,
) -> UserDashboardView:
    return UserDashboardView(
        id=user.id,
        login=user.login,
        display_name=user.display_name,
        role=user.role,
        preview_mode=preview_mode,
        interfaces=[serialize_interface(interface, user.expires_at) for interface in (interfaces if interfaces is not None else user.interfaces)],
        resources=serialize_resources(user.resources, user.id),
        filters=serialize_filters(global_filters, user_filters if user_filters is not None else user.filters),
    )


def serialize_server_options(servers: list[Server]) -> list[ServerOptionView]:
    return [ServerOptionView(id=server.id, name=server.name) for server in servers]


def serialize_basic_settings(settings: dict[str, str]) -> BasicSettingsView:
    return BasicSettingsView(
        current_version=get_panel_version(),
        nelomai_git_repo=settings.get("nelomai_git_repo", ""),
        dns_server=settings["dns_server"],
        mtu=int(settings["mtu"]),
        keepalive=int(settings["keepalive"]),
        exclusion_filters_enabled=settings["exclusion_filters_enabled"] == "1",
        block_filters_enabled=settings["block_filters_enabled"] == "1",
        admin_telegram_url=settings["admin_telegram_url"],
        admin_vk_url=settings["admin_vk_url"],
        admin_email_url=settings["admin_email_url"],
        admin_group_url=settings["admin_group_url"],
        audit_log_retention_days=int(settings.get("audit_log_retention_days", "30")),
    )


def serialize_interface_summary(interface: Interface) -> InterfaceSummaryView:
    traffic_30d_total_gb = round(sum(peer.traffic_30d_mb for peer in interface.peers) / 1024, 1)
    active_peers = sum(1 for peer in interface.peers if peer.is_enabled)
    is_pending_owner = interface.is_pending_owner
    configured_route_mode = interface.route_mode if interface.tak_server_id else RouteMode.STANDALONE
    effective_route_mode = RouteMode.STANDALONE if interface.tak_tunnel_fallback_active else configured_route_mode
    return InterfaceSummaryView(
        id=interface.id,
        agent_interface_id=interface.agent_interface_id,
        name=interface.name,
        owner_id=None if is_pending_owner else interface.user.id,
        owner_name="Ожидает владельца" if is_pending_owner else f"{interface.user.login} · {interface.user.display_name}",
        owner_login=None if is_pending_owner else interface.user.login,
        tic_server_id=interface.tic_server_id,
        tic_server_name=interface.tic_server.name,
        route_mode=configured_route_mode,
        effective_route_mode=effective_route_mode,
        tak_tunnel_fallback_active=interface.tak_tunnel_fallback_active,
        tak_tunnel_last_status=interface.tak_tunnel_last_status,
        tak_tunnel_status_label=_tak_tunnel_status_label(interface.tak_tunnel_last_status),
        tak_tunnel_auto_recovered=interface.tak_tunnel_last_status == "recovered",
        traffic_30d_gb=traffic_30d_total_gb,
        active_peers=active_peers,
        peer_limit=interface.peer_limit,
        is_enabled=active_peers > 0,
        is_invalid=bool(interface.tic_server and interface.tic_server.is_excluded),
    )


def serialize_client(user: User) -> ClientView:
    expires_at = _normalize_datetime(user.expires_at)
    return ClientView(
        id=user.id,
        login=user.login,
        display_name=user.display_name,
        region=user.region,
        region_label=_user_region_label(user.region),
        role=user.role,
        interface_count=len(user.interfaces),
        communication_channel=user.contact_link_record.value if user.contact_link_record else None,
        expires_at=expires_at,
        is_expired=expires_at is not None and expires_at <= datetime.now(UTC),
        has_no_expiry=expires_at is None,
        can_delete=user.role != UserRole.ADMIN,
    )


def serialize_access_user(user: User) -> AccessUserView:
    expires_at = _normalize_datetime(user.expires_at)
    return AccessUserView(
        id=user.id,
        login=user.login,
        display_name=user.display_name,
        region=user.region,
        region_label=_user_region_label(user.region),
        expires_at=expires_at,
        is_expired=expires_at is not None and expires_at <= datetime.now(UTC),
        communication_channel=user.contact_link_record.value if user.contact_link_record else None,
    )


def serialize_client_interface_option(interface: Interface) -> ClientInterfaceOptionView:
    return ClientInterfaceOptionView(
        id=interface.id,
        name=interface.name,
        owner_name="Ожидает владельца" if interface.user.role == UserRole.ADMIN else interface.user.display_name,
    )


def serialize_admin_page(
    panel_server: ServerCardView,
    tic_server: ServerCardView,
    tak_server: ServerCardView,
    beta_readiness: BetaReadinessSummaryView | None,
    panel_update_summary,
    agent_update_summaries,
    interfaces: list[Interface],
    available_interfaces: list[Interface],
    available_tic_servers: list[Server],
    available_tak_servers: list[Server],
    settings: dict[str, str],
    filters: list[FilterView],
    clients: list[User],
    access_users: list[User],
    access_users_without_expiry: list[User],
) -> AdminPageView:
    interface_options = [serialize_client_interface_option(interface) for interface in available_interfaces]
    return AdminPageView(
        panel_server=panel_server,
        tic_server=tic_server,
        tak_server=tak_server,
        beta_readiness=beta_readiness,
        panel_update_summary=panel_update_summary,
        agent_update_summaries=agent_update_summaries,
        interfaces=[serialize_interface_summary(interface) for interface in interfaces],
        settings=serialize_basic_settings(settings),
        filters=filters,
        clients=[serialize_client(user) for user in clients],
        access_users=[serialize_access_user(user) for user in access_users],
        access_users_without_expiry=[serialize_access_user(user) for user in access_users_without_expiry],
        client_interface_options=interface_options,
        available_tic_servers=serialize_server_options(available_tic_servers),
        available_tak_servers=serialize_server_options(available_tak_servers),
    )


def serialize_servers_page(
    servers: list[ServerListItemView],
    selected_type: str,
    selected_sort: str,
) -> ServersPageView:
    return ServersPageView(
        servers=servers,
        selected_type=selected_type,
        selected_sort=selected_sort,
    )


def serialize_client_interface_option(interface: Interface) -> ClientInterfaceOptionView:
    return ClientInterfaceOptionView(
        id=interface.id,
        name=interface.name,
        owner_name="Ожидает владельца" if interface.is_pending_owner else interface.user.display_name,
    )


def serialize_servers_page(
    servers: list[ServerListItemView],
    excluded_servers: list[ServerListItemView],
    pending_bootstrap_tasks: list[ServerBootstrapListItemView],
    tak_tunnel_pairs: list[TakTunnelPairStateView],
    beta_readiness: BetaReadinessSummaryView | None,
    selected_server_agent_update,
    selected_view: str,
    selected_bucket: str,
    selected_type: str,
    selected_location: str,
    selected_sort: str,
    selected_server: ServerDetailView | None = None,
    selected_bootstrap_task_id: int | None = None,
) -> ServersPageView:
    return ServersPageView(
        servers=servers,
        excluded_servers=excluded_servers,
        pending_bootstrap_tasks=pending_bootstrap_tasks,
        tak_tunnel_pairs=tak_tunnel_pairs,
        beta_readiness=beta_readiness,
        selected_server_agent_update=selected_server_agent_update,
        selected_view=selected_view,
        selected_bucket=selected_bucket,
        selected_type=selected_type,
        selected_location=selected_location,
        selected_sort=selected_sort,
        selected_server=selected_server,
        selected_bootstrap_task_id=selected_bootstrap_task_id,
    )
