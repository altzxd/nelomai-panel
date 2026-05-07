from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import string
import time
import zipfile
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import httpx
import qrcode
from qrcode.image.svg import SvgPathImage
from sqlalchemy import Select, func, inspect, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import engine
from app.models import (
    AppSetting,
    AuditLog,
    BackupRecord,
    BackupType,
    FilterKind,
    FilterScope,
    FilterType,
    Interface,
    Peer,
    PeerDownloadLink,
    PanelJob,
    PanelJobStatus,
    RegistrationLink,
    ResourceFilter,
    RouteMode,
    Server,
    ServerBootstrapTask,
    ServerType,
    TicRegion,
    User,
    UserContactLink,
    UserRegion,
    UserResource,
    UserRole,
)
from app.schemas import (
    AdminFilterDeleteRequest,
    AgentContractActionView,
    AgentContractPageView,
    AuditLogView,
    AuditLogsPageView,
    AdminPageView,
    AdminUserCreate,
    BackupCreateRequest,
    BackupBulkDeleteView,
    BackupCleanupView,
    BackupRecordView,
    BackupRestoreApplyRequest,
    BackupRestoreApplyView,
    BackupRestoreConflictView,
    BackupRestorePlanView,
    BackupRestorePlanRequest,
    BackupRestoreUserPlanView,
    BackupServerSnapshotVerifyItemView,
    BackupServerSnapshotVerifyView,
    ServerBackupCleanupItemView,
    ServerBackupCleanupView,
    BackupSettingsUpdate,
    BackupSettingsView,
    BackupsPageView,
    BetaReadinessSummaryView,
    BasicSettingsUpdate,
    DiagnosticsCheckView,
    DiagnosticsFocusedTakTunnelView,
    DiagnosticsPageView,
    DiagnosticsRecommendationView,
    FilterCreate,
    FilterUpdate,
    FilterView,
    InterfaceCreate,
    InterfaceExclusionFiltersUpdate,
    InterfaceAllocationView,
    InterfacePeerLimitUpdate,
    InterfaceRouteModeUpdate,
    InterfaceTakServerUpdate,
    InterfacePrepareRequest,
    ResourceItemView,
    ServerCardView,
    ServerCreate,
    ServerBootstrapInput,
    ServerBootstrapListItemView,
    ServerBootstrapPendingInputView,
    ServerBootstrapStepView,
    ServerBootstrapSnapshotView,
    ServerBootstrapTaskView,
    ServerRuntimeCheckItemView,
    ServerRuntimeCheckView,
    ServerAgentUpdateView,
    ServerDetailView,
    ServerListItemView,
    ServersPageView,
    TakTunnelPairStateView,
    PeerBlockFiltersUpdate,
    PeerCommentUpdate,
    PeerExpiryUpdate,
    FirstAdminRegistrationCreate,
    PanelUpdateCheckView,
    PanelJobView,
    PanelJobsPageView,
    PublicRegistrationCreate,
    RegistrationLinkView,
    RegistrationLinksPageView,
    SharedPeerLinkView,
    SharedPeerLinksPageView,
    UpdatesPageView,
    UserExpiresUpdate,
    UserContactLinkUpdate,
    UserDisplayNameUpdate,
    UserDashboardView,
    UserResourceUpdate,
    UpdateSettingsUpdate,
)
from app.serializers import (
    serialize_admin_page,
    serialize_dashboard,
    serialize_resources,
    serialize_server_options,
    serialize_servers_page,
)
from app.security import (
    create_peer_download_token,
    decrypt_secret,
    encrypt_secret,
    get_password_hash,
    is_encrypted_secret,
    verify_password,
)
from app.version import get_panel_version

ROOT_DIR = Path(__file__).resolve().parents[1]
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class ServiceError(Exception):
    pass


class EntityNotFoundError(ServiceError):
    pass


class PermissionDeniedError(ServiceError):
    pass


class InvalidInputError(ServiceError):
    pass


class ServerOperationUnavailableError(ServiceError):
    pass


def _tic_region_label(value: TicRegion | str | None) -> str | None:
    if value in {TicRegion.EUROPE, "europe"}:
        return "Европа"
    if value in {TicRegion.EAST, "east"}:
        return "Восток"
    return None


def _server_location_label(server: Server) -> str | None:
    if server.server_type == ServerType.TIC:
        return _tic_region_label(server.tic_region)
    if server.server_type == ServerType.TAK:
        return server.tak_country.strip() if server.tak_country else None
    return None


DEFAULT_BASIC_SETTINGS = {
    "nelomai_git_repo": settings.nelomai_git_repo.strip(),
    "dns_server": "8.8.8.8",
    "mtu": "1280",
    "keepalive": "21",
    "exclusion_filters_enabled": "1",
    "block_filters_enabled": "1",
    "admin_telegram_url": "",
    "admin_vk_url": "",
    "admin_email_url": "",
    "admin_group_url": "",
    "audit_log_retention_days": "30",
    "backups_enabled": "1",
    "backup_frequency": "daily",
    "backup_time": "03:00",
    "backup_retention_days": "30",
    "backup_storage_path": ".tmp/backups",
    "backup_last_run_at": "",
    "server_backup_retention_days": "90",
    "server_backup_size_limit_mb": "5120",
    "server_backup_monthly_retention_days": "365",
    "server_backup_monthly_size_limit_mb": "3072",
    "backup_remote_storage_server_id": "",
}

DEPRECATED_SETTING_KEYS = {"panel_git_repo", "tic_git_repo", "tak_git_repo"}

AGENT_CONTRACT_VERSION = "1.0"
SUPPORTED_AGENT_CONTRACTS = ["1.0"]
AGENT_COMPONENT_LABELS = {
    "tic-agent": "Tic agent",
    "tak-agent": "Tak agent",
    "storage-agent": "Storage agent",
    "server-agent": "Bootstrap / generic server agent",
}

ACTION_CAPABILITIES = {
    "bootstrap_server": ["agent.bootstrap.v1", "agent.update.v1"],
    "bootstrap_server_status": ["agent.bootstrap.v1", "agent.update.v1"],
    "bootstrap_server_input": ["agent.bootstrap.v1", "agent.update.v1"],
    "restart_server_agent": ["agent.lifecycle.v1"],
    "verify_server_status": ["agent.status.v1"],
    "verify_server_runtime": ["agent.runtime.v1"],
    "reboot_server": ["agent.lifecycle.v1"],
    "check_server_agent_update": ["agent.update.v1"],
    "update_server_agent": ["agent.update.v1"],
    "create_server_backup": ["backup.server.v1"],
    "verify_server_backup_copy": ["backup.server.v1"],
    "cleanup_server_backups": ["backup.server.v1"],
    "provision_tak_tunnel": ["tunnel.tak.provision.v1"],
    "attach_tak_tunnel": ["tunnel.tak.attach.v1"],
    "verify_tak_tunnel_status": ["tunnel.tak.status.v1"],
    "detach_tak_tunnel": ["tunnel.tak.detach.v1"],
    "prepare_interface": ["interface.create.v1"],
    "create_interface": ["interface.create.v1"],
    "delete_interface": ["interface.delete.v1"],
    "toggle_interface": ["interface.state.v1"],
    "update_interface_route_mode": ["interface.route_mode.v1"],
    "update_interface_tak_server": ["interface.tak_server.v1"],
    "update_interface_exclusion_filters": ["filters.exclusion.v1"],
    "toggle_peer": ["peer.state.v1"],
    "recreate_peer": ["peer.recreate.v1"],
    "delete_peer": ["peer.delete.v1"],
    "download_peer_config": ["peer.download.v1"],
    "download_interface_bundle": ["peer.download_bundle.v1"],
    "update_peer_block_filters": ["filters.block.v1"],
}

ERROR_EVENT_MESSAGES_RU = {
    "auth.login_failed": "Не удалось войти: неверный логин или пароль.",
    "http.400": "Запрос отклонён: данные заполнены неверно.",
    "http.401": "Нужен вход в панель.",
    "http.403": "Отказано по правам: у пользователя нет доступа к действию.",
    "http.404": "Запрошенный объект не найден.",
    "http.503": "Серверное действие недоступно: агент или сервер не ответил.",
    "http.error": "Произошла ошибка при обработке запроса.",
}

AUDIT_EVENT_TYPE_LABELS = {
    "agent.command": "Команда агенту",
    "agent.command_failed": "Ошибка команды агенту",
    "auth.login_failed": "Ошибка входа",
    "backups.create": "Создание бэкапа",
    "backups.create_failed": "Ошибка создания бэкапа",
    "backups.restore_users": "Восстановление пользователей",
    "backups.verify_server_copies": "Проверка свежести BackUp",
    "diagnostics.run": "Запуск диагностики",
    "http.400": "Запрос отклонён",
    "http.401": "Нужен вход",
    "http.403": "Отказано по правам",
    "http.404": "Объект не найден",
    "http.503": "Серверное действие недоступно",
    "http.error": "Ошибка запроса",
    "panel_jobs.failed": "Ошибка задачи панели",
    "peer_links.create": "Создание ссылки на пир",
    "peer_links.revoke": "Отзыв ссылки на пир",
    "peer_links.revoke_bulk": "Массовый отзыв ссылок",
    "peers.expire_delete": "Удаление истёкшего пира",
    "peers.expire_delete_failed": "Ошибка удаления истёкшего пира",
    "servers.delete": "Удаление сервера",
    "servers.exclude": "Исключение сервера",
    "servers.reboot": "Перезагрузка сервера",
    "servers.refresh_status": "Проверка статуса сервера",
    "servers.verify_runtime": "Проверка среды агента",
    "servers.restart_agent": "Перезагрузка агента",
    "servers.restore": "Восстановление сервера",
    "tak_tunnels.auto_recovered": "Автовосстановление туннеля Tic/Tak",
    "tak_tunnels.artifacts_rotated": "Обновлены служебные файлы туннеля Tic/Tak",
    "tak_tunnels.cooldown": "Повторная попытка туннеля Tic/Tak отложена",
    "tak_tunnels.backoff_cleared": "Сброшена пауза восстановления туннеля Tic/Tak",
    "tak_tunnels.manual_repaired": "Ручное восстановление туннеля Tic/Tak",
    "updates.agent_apply": "Обновление агента",
    "updates.agent_check": "Проверка обновлений агента",
    "updates.panel_check": "Проверка обновлений панели",
    "updates.panel_check_failed": "Ошибка проверки обновлений панели",
    "tak_tunnels.manual_attention_required": "Туннель Tic/Tak требует внимания",
}

AGENT_ACTION_LABELS_RU = {
    "bootstrap_server": "добавление сервера",
    "bootstrap_server_status": "опрос статуса добавления сервера",
    "bootstrap_server_input": "ввод для добавления сервера",
    "check_server_agent_update": "проверка обновления агента",
    "cleanup_server_backups": "очистка бэкапов на сервере",
    "create_interface": "создание интерфейса",
    "create_server_backup": "создание серверного snapshot",
    "provision_tak_tunnel": "подготовка межсерверного туннеля Tak",
    "attach_tak_tunnel": "подключение межсерверного туннеля Tic",
    "verify_tak_tunnel_status": "проверка межсерверного туннеля Tic/Tak",
    "detach_tak_tunnel": "отключение межсерверного туннеля Tic/Tak",
    "delete_peer": "удаление пира",
    "download_interface_bundle": "скачивание архива интерфейса",
    "download_peer_config": "скачивание конфига пира",
    "prepare_interface": "подбор параметров интерфейса",
    "reboot_server": "перезагрузка сервера",
    "recreate_peer": "пересоздание пира",
    "restart_server_agent": "перезагрузка агента",
    "toggle_interface": "переключение интерфейса",
    "toggle_peer": "переключение пира",
    "update_interface_exclusion_filters": "переключение фильтров исключения",
    "update_interface_route_mode": "изменение route mode",
    "update_interface_tak_server": "смена Tak endpoint",
    "update_peer_block_filters": "переключение фильтров блока",
    "update_server_agent": "обновление агента",
    "verify_server_backup_copy": "проверка свежести серверного snapshot",
    "verify_server_runtime": "проверка среды агента",
    "verify_server_status": "проверка статуса сервера",
}


def _agent_capabilities_for_action(action: str) -> list[str]:
    return ACTION_CAPABILITIES.get(action, [])


def _agent_payload_envelope(action: str, component: str) -> dict[str, object]:
    return {
        "contract_version": AGENT_CONTRACT_VERSION,
        "supported_contracts": SUPPORTED_AGENT_CONTRACTS,
        "panel_version": get_panel_version(),
        "component": component,
        "requested_capabilities": _agent_capabilities_for_action(action),
    }


def _component_from_server_type(server_type: ServerType | str | None) -> str:
    try:
        normalized = ServerType(server_type) if server_type is not None else None
    except ValueError:
        normalized = None
    if normalized == ServerType.TIC:
        return "tic-agent"
    if normalized == ServerType.TAK:
        return "tak-agent"
    if normalized == ServerType.STORAGE:
        return "storage-agent"
    return "server-agent"


def _component_for_action(action: str) -> str:
    if action in {"bootstrap_server", "bootstrap_server_status", "bootstrap_server_input"}:
        return "server-agent"
    if action == "provision_tak_tunnel":
        return "tak-agent"
    if action == "attach_tak_tunnel":
        return "tic-agent"
    if action in {"verify_tak_tunnel_status", "detach_tak_tunnel"}:
        return "tic-agent / tak-agent"
    if action in {"prepare_interface", "create_interface", "toggle_interface", "update_interface_route_mode", "update_interface_tak_server", "update_interface_exclusion_filters", "toggle_peer", "recreate_peer", "delete_peer", "download_peer_config", "download_interface_bundle", "update_peer_block_filters"}:
        return "tic-agent"
    if action in {"create_server_backup", "verify_server_backup_copy", "cleanup_server_backups"}:
        return "tic-agent / tak-agent"
    if action in {"restart_server_agent", "verify_server_status", "verify_server_runtime", "reboot_server", "check_server_agent_update", "update_server_agent"}:
        return "tic-agent / tak-agent / storage-agent"
    return "server-agent"


def _encrypt_ssh_password(value: str | None) -> str:
    return encrypt_secret(value)


def _decrypt_ssh_password(value: str | None) -> str:
    return decrypt_secret(value)


def normalize_stored_ssh_secrets(db: Session) -> int:
    updated = 0
    for server in db.execute(select(Server)).scalars().all():
        if server.ssh_password and not is_encrypted_secret(server.ssh_password):
            server.ssh_password = _encrypt_ssh_password(server.ssh_password)
            db.add(server)
            updated += 1
    for task in db.execute(select(ServerBootstrapTask)).scalars().all():
        if task.ssh_password and not is_encrypted_secret(task.ssh_password):
            task.ssh_password = _encrypt_ssh_password(task.ssh_password)
            db.add(task)
            updated += 1
    if updated:
        db.commit()
    return updated


SENSITIVE_AUDIT_VALUE_RE = re.compile(r"(?i)\b(ssh_)?password=([^;\s]+)")


def scrub_sensitive_audit_logs(db: Session) -> int:
    updated = 0
    logs = db.execute(
        select(AuditLog).where(
            or_(
                AuditLog.details.ilike("%password=%"),
                AuditLog.message.ilike("%password=%"),
                AuditLog.message_ru.ilike("%password=%"),
            )
        )
    ).scalars().all()
    for log in logs:
        changed = False
        for field_name in ("details", "message", "message_ru"):
            value = getattr(log, field_name)
            if not value or "password=" not in value.lower():
                continue
            sanitized = SENSITIVE_AUDIT_VALUE_RE.sub(lambda match: f"{match.group(1) or ''}password=[REDACTED]", value)
            if sanitized != value:
                setattr(log, field_name, sanitized)
                changed = True
        if changed:
            db.add(log)
            updated += 1
    if updated:
        db.commit()
    return updated


def audit_log_retention_days(db: Session) -> int:
    row = db.get(AppSetting, "audit_log_retention_days")
    try:
        value = int(row.value) if row is not None else 30
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, 365))


def backup_storage_path(db: Session) -> Path:
    ensure_default_settings(db)
    row = db.get(AppSetting, "backup_storage_path")
    raw_value = row.value if row is not None else DEFAULT_BASIC_SETTINGS["backup_storage_path"]
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def purge_old_audit_logs(db: Session) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=audit_log_retention_days(db))
    old_logs = db.execute(select(AuditLog).where(AuditLog.created_at < cutoff)).scalars().all()
    for item in old_logs:
        db.delete(item)


def delete_audit_logs_older_than(db: Session, actor: User, days: int) -> int:
    require_admin(actor)
    if days not in {1, 3, 7, 14, 30}:
        raise InvalidInputError("Invalid audit log cleanup period")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    old_logs = db.execute(select(AuditLog).where(AuditLog.created_at < cutoff)).scalars().all()
    count = len(old_logs)
    for item in old_logs:
        db.delete(item)
    db.commit()
    return count


def delete_all_audit_logs(db: Session, actor: User) -> int:
    require_admin(actor)
    logs = db.execute(select(AuditLog)).scalars().all()
    count = len(logs)
    for item in logs:
        db.delete(item)
    db.commit()
    return count


def write_audit_log(
    db: Session,
    *,
    event_type: str,
    message: str,
    message_ru: str | None = None,
    severity: str = "info",
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    server_id: int | None = None,
    details: str | None = None,
    commit: bool = True,
) -> AuditLog:
    purge_old_audit_logs(db)
    log = AuditLog(
        event_type=event_type,
        severity=severity,
        message=message,
        message_ru=message_ru or message,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        server_id=server_id,
        details=details,
    )
    db.add(log)
    if commit:
        db.commit()
        db.refresh(log)
    return log


PANEL_JOB_STUCK_AFTER = timedelta(minutes=15)
PANEL_JOB_PROBLEM_STATUSES = {PanelJobStatus.FAILED, PanelJobStatus.STUCK}
PANEL_JOB_ACTIVE_STATUSES = {PanelJobStatus.QUEUED, PanelJobStatus.RUNNING, PanelJobStatus.STUCK}
PANEL_JOB_CLEANUP_STATUSES = {PanelJobStatus.COMPLETED, PanelJobStatus.FAILED, PanelJobStatus.CANCELLED, PanelJobStatus.STUCK}
TAK_TUNNEL_REPAIR_STATE_KEY = "tak_tunnel_repair_state_json"
TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT = 5


def _tak_tunnel_status_label_ui(status: str) -> str:
    mapping = {
        "active": "работает штатно",
        "recovered": "автовосстановлен",
        "fallback": "есть проблемы",
        "cooldown": "ожидает повторную попытку",
        "manual_attention_required": "требует ручного вмешательства",
        "error": "ошибка",
    }
    return mapping.get(status, status)
TAK_TUNNEL_AUTO_REPAIR_BACKOFF_SECONDS = (60, 300, 900, 3600, 10800)
UPDATES_CACHE_TTL_SECONDS = 120
SERVER_RUNTIME_CACHE_TTL_SECONDS = 10
PANEL_JOB_TYPE_LABELS = {
    "server_bootstrap": "Добавление сервера",
    "expired_peers_cleanup": "Очистка истёкших пиров",
    "backup_create": "Создание бэкапа",
    "backup_restore_plan": "Проверка восстановления",
    "backup_restore_users": "Восстановление пользователей",
    "backup_verify_freshness": "Проверка свежести BackUp",
    "backup_cleanup_server_copies": "Очистка бэкапов на серверах",
    "agent_updates_check": "Проверка обновлений агентов",
    "agent_updates_apply": "Обновление агентов",
}
PANEL_JOB_STATUS_LABELS = {
    PanelJobStatus.QUEUED: "В очереди",
    PanelJobStatus.RUNNING: "Выполняется",
    PanelJobStatus.COMPLETED: "Завершена",
    PanelJobStatus.FAILED: "Ошибка",
    PanelJobStatus.CANCELLED: "Остановлена",
    PanelJobStatus.STUCK: "Зависла",
}
_PANEL_UPDATE_CACHE: dict[str, object] = {"fetched_at": 0.0, "value": None}
_SERVER_AGENT_UPDATE_CACHE: dict[int, tuple[float, ServerAgentUpdateView]] = {}
_SERVER_RUNTIME_CACHE: dict[int, tuple[float, dict[str, object]]] = {}


def _tak_tunnel_pair_key(tic_server_id: int, tak_server_id: int) -> str:
    return f"tic:{tic_server_id}|tak:{tak_server_id}"


def _tak_tunnel_parse_datetime(raw_value: object) -> datetime | None:
    if not raw_value:
        return None
    try:
        value = datetime.fromisoformat(str(raw_value))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _load_tak_tunnel_repair_state(db: Session) -> dict[str, dict[str, object]]:
    row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
    if row is None or not row.value:
        return {}
    try:
        payload = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            normalized[key] = dict(value)
    return normalized


def _save_tak_tunnel_repair_state(db: Session, state: dict[str, dict[str, object]]) -> None:
    row = db.get(AppSetting, TAK_TUNNEL_REPAIR_STATE_KEY)
    payload = json.dumps(state, ensure_ascii=False) if state else ""
    if row is None:
        row = AppSetting(key=TAK_TUNNEL_REPAIR_STATE_KEY, value=payload)
    else:
        row.value = payload
    db.add(row)


def _tak_tunnel_auto_repair_backoff_seconds(failure_count: int) -> int:
    if failure_count <= 0:
        return 0
    index = min(failure_count - 1, len(TAK_TUNNEL_AUTO_REPAIR_BACKOFF_SECONDS) - 1)
    return TAK_TUNNEL_AUTO_REPAIR_BACKOFF_SECONDS[index]


def _tak_tunnel_pair_runtime_status(
    pair_state: dict[str, object],
    *,
    now: datetime,
) -> tuple[str | None, datetime | None]:
    if bool(pair_state.get("manual_attention_required")):
        return "manual_attention_required", None
    cooldown_until = _tak_tunnel_parse_datetime(pair_state.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > now:
        return "cooldown", cooldown_until
    return None, cooldown_until


def _tak_tunnel_register_failure(
    repair_state: dict[str, dict[str, object]],
    *,
    tic_server_id: int,
    tak_server_id: int,
    now: datetime,
) -> tuple[dict[str, object], bool]:
    pair_key = _tak_tunnel_pair_key(tic_server_id, tak_server_id)
    current = dict(repair_state.get(pair_key) or {})
    failure_count = int(current.get("failure_count") or 0) + 1
    backoff_seconds = _tak_tunnel_auto_repair_backoff_seconds(failure_count)
    updated = {
        "failure_count": failure_count,
        "last_failure_at": now.isoformat(),
        "last_attempt_at": now.isoformat(),
        "cooldown_until": (now + timedelta(seconds=backoff_seconds)).isoformat() if backoff_seconds else "",
        "manual_attention_required": failure_count >= TAK_TUNNEL_AUTO_REPAIR_FAILURE_LIMIT,
        "last_recovered_at": current.get("last_recovered_at") or "",
    }
    changed = current != updated
    repair_state[pair_key] = updated
    return updated, changed


def _tak_tunnel_register_success(
    repair_state: dict[str, dict[str, object]],
    *,
    tic_server_id: int,
    tak_server_id: int,
    now: datetime,
) -> bool:
    pair_key = _tak_tunnel_pair_key(tic_server_id, tak_server_id)
    current = dict(repair_state.get(pair_key) or {})
    if not current:
        return False
    updated = {
        "failure_count": 0,
        "last_failure_at": "",
        "last_attempt_at": now.isoformat(),
        "cooldown_until": "",
        "manual_attention_required": False,
        "last_recovered_at": now.isoformat(),
    }
    if current == updated:
        return False
    repair_state[pair_key] = updated
    return True


def _job_logs(job: PanelJob) -> list[str]:
    try:
        value = json.loads(job.logs_json or "[]")
    except json.JSONDecodeError:
        value = []
    return [str(item) for item in value]


def _set_job_logs(job: PanelJob, logs: list[str]) -> None:
    job.logs_json = json.dumps(logs, ensure_ascii=False)


def _append_job_log(job: PanelJob, message: str) -> None:
    logs = _job_logs(job)
    logs.append(message)
    _set_job_logs(job, logs)
    job.updated_at = utc_now()


def update_panel_job_progress(
    db: Session,
    job: PanelJob | None,
    progress_percent: int,
    current_stage: str,
    *,
    log: bool = True,
) -> None:
    if job is None:
        return
    job.progress_percent = max(0, min(100, int(progress_percent)))
    job.current_stage = current_stage[:255]
    job.updated_at = utc_now()
    if log:
        _append_job_log(job, current_stage)
    db.add(job)
    db.commit()


def mark_stuck_panel_jobs(db: Session) -> int:
    cutoff = utc_now() - PANEL_JOB_STUCK_AFTER
    jobs = db.execute(
        select(PanelJob).where(PanelJob.status == PanelJobStatus.RUNNING, PanelJob.updated_at < cutoff)
    ).scalars().all()
    for job in jobs:
        job.status = PanelJobStatus.STUCK
        job.error_message = "Задача зависла: превышено время выполнения 15 минут."
        job.current_stage = "Задача зависла"
        job.completed_at = utc_now()
        _append_job_log(job, job.error_message)
        db.add(job)
    if jobs:
        db.commit()
    return len(jobs)


def has_problem_panel_jobs(db: Session) -> bool:
    mark_stuck_panel_jobs(db)
    return db.execute(select(PanelJob.id).where(PanelJob.status.in_(PANEL_JOB_PROBLEM_STATUSES)).limit(1)).first() is not None


def _bootstrap_task_for_job(db: Session, job: PanelJob) -> ServerBootstrapTask | None:
    if job.job_type != "server_bootstrap":
        return None
    return db.execute(
        select(ServerBootstrapTask).where(ServerBootstrapTask.panel_job_id == job.id)
    ).scalar_one_or_none()


def _bootstrap_snapshot_for_job(db: Session, job: PanelJob) -> ServerBootstrapSnapshotView | None:
    task = _bootstrap_task_for_job(db, job)
    if task is None:
        return None
    return _task_bootstrap_snapshot(task)


def _bootstrap_pending_command_for_job(db: Session, job: PanelJob) -> str | None:
    task = _bootstrap_task_for_job(db, job)
    if task is None:
        return None
    return _task_pending_bootstrap_command(task)


def serialize_panel_job(
    job: PanelJob,
    db: Session | None = None,
    bootstrap_task: ServerBootstrapTask | None = None,
) -> PanelJobView:
    if bootstrap_task is None and db is not None:
        bootstrap_task = _bootstrap_task_for_job(db, job)
    source_label = "Открыть серверы" if job.job_type == "server_bootstrap" else None
    source_url = (
        f"/admin/servers?selected_bootstrap_task_id={bootstrap_task.id}#bootstrap-task-{bootstrap_task.id}"
        if bootstrap_task is not None
        else ("/admin/servers" if job.job_type == "server_bootstrap" else None)
    )
    return PanelJobView(
        id=job.id,
        job_type=job.job_type,
        job_type_label=PANEL_JOB_TYPE_LABELS.get(job.job_type, job.job_type),
        status=job.status,
        status_label=PANEL_JOB_STATUS_LABELS.get(job.status, job.status.value),
        progress_percent=max(0, min(100, int(job.progress_percent or 0))),
        current_stage=job.current_stage or "",
        created_by_login=job.created_by_user.login if job.created_by_user else None,
        logs=_job_logs(job),
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
        can_cancel=job.status in PANEL_JOB_ACTIVE_STATUSES,
        source_label=source_label,
        source_url=source_url,
        bootstrap_command_profile=bootstrap_task.bootstrap_command_profile if bootstrap_task is not None else None,
        bootstrap_packages=_task_bootstrap_package_list(bootstrap_task.bootstrap_packages_json) if bootstrap_task is not None else [],
        bootstrap_safe_init_packages=_task_bootstrap_package_list(bootstrap_task.bootstrap_safe_init_packages_json) if bootstrap_task is not None else [],
        bootstrap_full_only_packages=_task_bootstrap_package_list(bootstrap_task.bootstrap_full_only_packages_json) if bootstrap_task is not None else [],
        bootstrap_snapshot=_task_bootstrap_snapshot(bootstrap_task) if bootstrap_task is not None else None,
        bootstrap_pending_command=_task_pending_bootstrap_command(bootstrap_task) if bootstrap_task is not None else None,
        bootstrap_steps=_task_bootstrap_steps(bootstrap_task) if bootstrap_task is not None else [],
        bootstrap_last_step_error=_task_bootstrap_last_step_error(bootstrap_task) if bootstrap_task is not None else None,
    )


def _normalize_panel_job_status_filter(status_filter: str) -> str:
    return status_filter if status_filter in {"all", *[status.value for status in PanelJobStatus]} else "all"


def _normalize_panel_job_type_filter(type_filter: str) -> str:
    return type_filter if type_filter in {
        "all",
        "server_bootstrap",
        "expired_peers_cleanup",
        "backup_create",
        "backup_restore_plan",
        "backup_restore_users",
        "backup_verify_freshness",
        "backup_cleanup_server_copies",
        "agent_updates_check",
        "agent_updates_apply",
    } else "all"


def get_panel_jobs_page(
    db: Session,
    actor: User,
    status_filter: str = "all",
    type_filter: str = "all",
    selected_job_id: int | None = None,
) -> PanelJobsPageView:
    require_admin(actor)
    mark_stuck_panel_jobs(db)
    status_filter = _normalize_panel_job_status_filter(status_filter)
    type_filter = _normalize_panel_job_type_filter(type_filter)
    query = select(PanelJob).options(joinedload(PanelJob.created_by_user))
    if status_filter != "all":
        query = query.where(PanelJob.status == PanelJobStatus(status_filter))
    if type_filter != "all":
        query = query.where(PanelJob.job_type == type_filter)
    jobs = db.execute(query.order_by(PanelJob.created_at.desc(), PanelJob.id.desc()).limit(200)).scalars().all()
    bootstrap_tasks_by_job_id: dict[int, ServerBootstrapTask] = {}
    bootstrap_job_ids = [job.id for job in jobs if job.job_type == "server_bootstrap"]
    if bootstrap_job_ids:
        bootstrap_tasks = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.panel_job_id.in_(bootstrap_job_ids))
        ).scalars().all()
        bootstrap_tasks_by_job_id = {
            task.panel_job_id: task
            for task in bootstrap_tasks
            if task.panel_job_id is not None
        }
    return PanelJobsPageView(
        jobs=[serialize_panel_job(job, bootstrap_task=bootstrap_tasks_by_job_id.get(job.id)) for job in jobs],
        selected_status=status_filter,
        selected_type=type_filter,
        has_problem_jobs=has_problem_panel_jobs(db),
        has_active_jobs=any(job.status in PANEL_JOB_ACTIVE_STATUSES for job in jobs),
        selected_job_id=selected_job_id,
    )


def create_panel_job(db: Session, actor: User, job_type: str) -> PanelJob:
    require_admin(actor)
    job = PanelJob(
        job_type=job_type,
        status=PanelJobStatus.QUEUED,
        created_by_user_id=actor.id,
        progress_percent=0,
        current_stage="В очереди",
        logs_json="[]",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    _append_job_log(job, "Задача поставлена в очередь.")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def cancel_panel_job(db: Session, actor: User, job_id: int) -> PanelJobView:
    require_admin(actor)
    job = db.get(PanelJob, job_id)
    if job is None:
        raise EntityNotFoundError("Panel job not found")
    if job.status not in {PanelJobStatus.QUEUED, PanelJobStatus.RUNNING, PanelJobStatus.STUCK}:
        raise PermissionDeniedError("Only queued, running or stuck jobs can be cancelled")
    job.status = PanelJobStatus.CANCELLED
    job.current_stage = "Остановлена администратором"
    job.completed_at = utc_now()
    _append_job_log(job, "Задача остановлена администратором.")
    if job.job_type == "server_bootstrap":
        bootstrap_task = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.panel_job_id == job.id)
        ).scalar_one_or_none()
        if bootstrap_task is not None and bootstrap_task.status in {"running", "input_required"}:
            task_logs = _task_logs(bootstrap_task)
            task_logs.append("Bootstrap остановлен администратором из диспетчера задач.")
            bootstrap_task.status = "cancelled"
            bootstrap_task.input_prompt = None
            bootstrap_task.input_key = None
            bootstrap_task.input_kind = None
            bootstrap_task.last_error = None
            _set_task_logs(bootstrap_task, task_logs)
            db.add(bootstrap_task)
    db.add(job)
    db.commit()
    db.refresh(job)
    return serialize_panel_job(job, db)


def cleanup_inactive_panel_jobs(
    db: Session,
    actor: User,
    status_filter: str = "all",
    type_filter: str = "all",
) -> dict[str, int | str]:
    require_admin(actor)
    mark_stuck_panel_jobs(db)
    status_filter = _normalize_panel_job_status_filter(status_filter)
    type_filter = _normalize_panel_job_type_filter(type_filter)

    query = select(PanelJob).where(PanelJob.status.in_(PANEL_JOB_CLEANUP_STATUSES))
    if status_filter != "all":
        selected_status = PanelJobStatus(status_filter)
        if selected_status not in PANEL_JOB_CLEANUP_STATUSES:
            return {"deleted": 0, "status_filter": status_filter, "type_filter": type_filter}
        query = query.where(PanelJob.status == selected_status)
    if type_filter != "all":
        query = query.where(PanelJob.job_type == type_filter)

    jobs = db.execute(query.order_by(PanelJob.id.asc())).scalars().all()
    if not jobs:
        return {"deleted": 0, "status_filter": status_filter, "type_filter": type_filter}

    job_ids = [job.id for job in jobs]
    if job_ids:
        bootstrap_tasks = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.panel_job_id.in_(job_ids))
        ).scalars().all()
        for bootstrap_task in bootstrap_tasks:
            bootstrap_task.panel_job_id = None
            db.add(bootstrap_task)

    deleted = len(jobs)
    for job in jobs:
        db.delete(job)

    write_audit_log(
        db,
        event_type="panel_jobs.cleanup",
        severity="info",
        message=f"Deleted inactive panel jobs: {deleted}",
        message_ru=f"Удалены неактуальные задачи панели: {deleted}",
        actor_user_id=actor.id,
        details=f"deleted={deleted}; status_filter={status_filter}; type_filter={type_filter}",
    )
    db.commit()
    return {"deleted": deleted, "status_filter": status_filter, "type_filter": type_filter}


def run_expired_peers_cleanup_job(db: Session, actor: User) -> PanelJobView:
    job = create_panel_job(db, actor, "expired_peers_cleanup")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    job.progress_percent = 10
    job.current_stage = "Запущена очистка истёкших пиров"
    _append_job_log(job, "Запущена очистка истёкших пиров.")
    db.add(job)
    db.commit()
    try:
        deleted_count = purge_expired_peers(db)
        job.status = PanelJobStatus.COMPLETED
        job.progress_percent = 100
        job.current_stage = "Очистка завершена"
        job.completed_at = utc_now()
        _append_job_log(job, f"Очистка завершена. Удалено пиров: {deleted_count}.")
        db.add(job)
        db.commit()
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.progress_percent = 100
        job.current_stage = "Ошибка очистки"
        job.error_message = str(exc)
        job.completed_at = utc_now()
        _append_job_log(job, f"Ошибка задачи: {exc}")
        db.add(job)
        db.commit()
        write_audit_log(
            db,
            event_type="panel_jobs.failed",
            severity="error",
            message=f"Panel job failed: {job.job_type}; error={exc}",
            message_ru=f"Задача панели завершилась ошибкой: {job.job_type}. Ошибка: {exc}",
            actor_user_id=actor.id,
        )
    db.refresh(job)
    return serialize_panel_job(job, db)


def human_error_message_ru(event_type: str, fallback: str) -> str:
    return ERROR_EVENT_MESSAGES_RU.get(event_type) or fallback or ERROR_EVENT_MESSAGES_RU["http.error"]


def _build_tic_executor_payload(
    action: str,
    interface: Interface,
    peer: Peer | None = None,
    exclusion_filters_enabled: bool = True,
    block_filters_enabled: bool = True,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        **_agent_payload_envelope(action, "tic-agent"),
        "action": action,
        "tic_server": {
            "id": interface.tic_server.id,
            "name": interface.tic_server.name,
            "host": interface.tic_server.host,
        },
    }
    interface_payload: dict[str, object] = {
            "id": interface.id,
            "agent_interface_id": interface.agent_interface_id,
            "server_identity": {
                "tic_server_id": interface.tic_server_id,
                "agent_interface_id": interface.agent_interface_id,
                "name": interface.name,
            },
            "name": interface.name,
            "route_mode": interface.route_mode.value if interface.tak_server_id else RouteMode.STANDALONE.value,
    }
    if interface.listen_port:
        interface_payload["listen_port"] = interface.listen_port
    if interface.address_v4:
        interface_payload["address_v4"] = interface.address_v4
    interface_peers = list(getattr(interface, "peers", []) or [])
    interface_payload["peers"] = [
        {
            "id": peer.id,
            "slot": peer.slot,
            "comment": peer.comment,
            "is_enabled": peer.is_enabled,
            "block_filters_enabled": peer.block_filters_enabled,
        }
        for peer in sorted(interface_peers, key=lambda item: item.slot)
    ]
    payload["interface"] = interface_payload
    if interface.tak_server is not None:
        payload["tak_server"] = {
            "id": interface.tak_server.id,
            "name": interface.tak_server.name,
            "host": interface.tak_server.host,
        }
    if peer is not None:
        payload["peer"] = {
            "id": peer.id,
            "slot": peer.slot,
            "comment": peer.comment,
        }
    # Panel-side contract for the future Tic Node-agent. When disabled,
    # exclusion rules must not keep traffic on Tic; route it like regular traffic.
    payload["exclusion_filters"] = {"enabled": exclusion_filters_enabled}
    # Panel-side contract for block rules. When enabled, the future Node-agent
    # should drop traffic to these addresses instead of routing it anywhere.
    payload["block_filters"] = {"enabled": block_filters_enabled}
    if extra:
        payload.update(extra)
    return payload


def _build_interface_executor_context(
    *,
    interface_id: int,
    name: str,
    tic_server: Server,
    tak_server: Server | None,
    route_mode: RouteMode,
    listen_port: int = 0,
    address_v4: str = "",
    agent_interface_id: str | None = None,
) -> SimpleNamespace:
    if tic_server.server_type != ServerType.TIC:
        raise InvalidInputError("Interface lifecycle can run only on Tic servers")
    if tak_server is not None and tak_server.server_type != ServerType.TAK:
        raise InvalidInputError("Tak endpoint must reference a Tak server")
    return SimpleNamespace(
        id=interface_id,
        agent_interface_id=agent_interface_id,
        name=name,
        tic_server=tic_server,
        tic_server_id=tic_server.id,
        tak_server=tak_server,
        tak_server_id=tak_server.id if tak_server else None,
        route_mode=route_mode,
        listen_port=listen_port,
        address_v4=address_v4,
    )


def _refresh_via_tak_interface_runtime(db: Session, interface: Interface, *, actor_user_id: int | None = None) -> None:
    if interface.route_mode != RouteMode.VIA_TAK or interface.tak_server_id is None:
        return
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "update_interface_route_mode",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"route_mode": RouteMode.VIA_TAK.value}},
        ),
        actor_user_id=actor_user_id,
    )


def _ensure_interface_uses_tic_agent(interface: Interface) -> None:
    if interface.tic_server is None or interface.tic_server.server_type != ServerType.TIC:
        raise InvalidInputError("Interface lifecycle is available only for interfaces bound to Tic servers")
    if interface.tak_server is not None and interface.tak_server.server_type != ServerType.TAK:
        raise InvalidInputError("Interface has invalid Tak endpoint binding")


def _build_server_executor_payload(
    *,
    action: str,
    server: Server | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    if action in {"bootstrap_server", "bootstrap_server_status", "bootstrap_server_input"}:
        component = _component_for_action(action)
    else:
        component = _component_from_server_type(
            server.server_type if server is not None else (extra or {}).get("server", {}).get("server_type")
        )
    payload: dict[str, object] = {
        **_agent_payload_envelope(action, component),
        "action": action,
    }
    if server is not None:
        payload["server"] = {
            "id": server.id,
            "name": server.name,
            "server_type": server.server_type.value,
            "host": server.host,
            "ssh_port": server.ssh_port,
            "ssh_login": server.ssh_login,
            "ssh_password": _decrypt_ssh_password(server.ssh_password),
        }
    if extra:
        payload.update(extra)
    return payload


def _server_agent_identity_payload(server: Server) -> dict[str, object]:
    return {
        "id": server.id,
        "name": server.name,
        "server_type": server.server_type.value,
        "host": server.host,
    }


def _count_via_tak_interfaces_for_pair(
    db: Session,
    *,
    tic_server_id: int,
    tak_server_id: int,
) -> int:
    return int(
        db.execute(
            select(func.count(Interface.id)).where(
                Interface.tic_server_id == tic_server_id,
                Interface.tak_server_id == tak_server_id,
                Interface.route_mode == RouteMode.VIA_TAK,
            )
        ).scalar_one()
        or 0
    )


def _provision_and_attach_tak_tunnel(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    actor_user_id: int | None = None,
) -> str:
    return _repair_tak_tunnel_transport(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        actor_user_id=actor_user_id,
        allow_reprovision=True,
    )[0]


def _request_tak_tunnel_artifacts(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    actor_user_id: int | None = None,
    reuse_existing_only: bool,
    rotate_artifacts: bool = False,
) -> tuple[str, dict[str, object]]:
    provision_response = _run_agent_executor_logged(
        db,
        _build_server_executor_payload(
            action="provision_tak_tunnel",
            server=tak_server,
            extra={
                "tic_server": _server_agent_identity_payload(tic_server),
                "reuse_existing_only": reuse_existing_only,
                "rotate_artifacts": rotate_artifacts,
            },
        ),
        actor_user_id=actor_user_id,
    )
    tunnel_id = str(provision_response.get("tunnel_id") or "").strip()
    tunnel_artifacts = provision_response.get("tunnel_artifacts")
    if not tunnel_id or not isinstance(tunnel_artifacts, dict):
        raise ServerOperationUnavailableError("Tak tunnel provision did not return tunnel_id/tunnel_artifacts")
    artifact_revision = int(provision_response.get("artifact_revision") or 0)
    if artifact_revision > 0:
        tunnel_artifacts = dict(tunnel_artifacts)
        tunnel_artifacts["artifact_revision"] = artifact_revision
    return tunnel_id, tunnel_artifacts


def _attach_tak_tunnel_artifacts(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    tunnel_id: str,
    tunnel_artifacts: dict[str, object],
    actor_user_id: int | None = None,
) -> None:
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(
            action="attach_tak_tunnel",
            server=tic_server,
            extra={
                "tak_server": _server_agent_identity_payload(tak_server),
                "tunnel_id": tunnel_id,
                "tunnel_artifacts": tunnel_artifacts,
            },
        ),
        actor_user_id=actor_user_id,
    )


def _repair_tak_tunnel_transport(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    actor_user_id: int | None = None,
    allow_reprovision: bool,
) -> tuple[str, str]:
    try:
        tunnel_id, tunnel_artifacts = _request_tak_tunnel_artifacts(
            db,
            tic_server=tic_server,
            tak_server=tak_server,
            actor_user_id=actor_user_id,
            reuse_existing_only=True,
            rotate_artifacts=False,
        )
        _attach_tak_tunnel_artifacts(
            db,
            tic_server=tic_server,
            tak_server=tak_server,
            tunnel_id=tunnel_id,
            tunnel_artifacts=tunnel_artifacts,
            actor_user_id=actor_user_id,
        )
        return tunnel_id, "partial"
    except Exception:
        if not allow_reprovision:
            raise
    tunnel_id, tunnel_artifacts = _request_tak_tunnel_artifacts(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        actor_user_id=actor_user_id,
        reuse_existing_only=False,
        rotate_artifacts=False,
    )
    _attach_tak_tunnel_artifacts(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        tunnel_id=tunnel_id,
        tunnel_artifacts=tunnel_artifacts,
        actor_user_id=actor_user_id,
    )
    return tunnel_id, "full"


def _rotate_tak_tunnel_transport(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    actor_user_id: int | None = None,
) -> tuple[str, int]:
    tunnel_id, tunnel_artifacts = _request_tak_tunnel_artifacts(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        actor_user_id=actor_user_id,
        reuse_existing_only=False,
        rotate_artifacts=True,
    )
    _attach_tak_tunnel_artifacts(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        tunnel_id=tunnel_id,
        tunnel_artifacts=tunnel_artifacts,
        actor_user_id=actor_user_id,
    )
    return tunnel_id, int(tunnel_artifacts.get("artifact_revision") or 1)


def _detach_tak_tunnel_pair(
    db: Session,
    *,
    tic_server: Server,
    tak_server: Server,
    actor_user_id: int | None = None,
) -> None:
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(
            action="detach_tak_tunnel",
            server=tic_server,
            extra={"tak_server": _server_agent_identity_payload(tak_server)},
        ),
        actor_user_id=actor_user_id,
    )
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(
            action="detach_tak_tunnel",
            server=tak_server,
            extra={"tic_server": _server_agent_identity_payload(tic_server)},
        ),
        actor_user_id=actor_user_id,
    )


def _latest_tak_tunnel_rotation_event(
    db: Session,
    *,
    tic_server_id: int,
    tak_server_id: int,
) -> AuditLog | None:
    event = (
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.event_type == "tak_tunnels.artifacts_rotated",
                AuditLog.server_id == tic_server_id,
            )
            .order_by(AuditLog.id.desc())
        )
        .scalars()
        .first()
    )
    if event is None:
        return None
    try:
        details = json.loads(event.details or "{}")
    except json.JSONDecodeError:
        return None
    if (
        int(details.get("tic_server_id") or 0) != tic_server_id
        or int(details.get("tak_server_id") or 0) != tak_server_id
    ):
        return None
    return event


def _reconcile_tak_tunnel_routes(db: Session) -> None:
    if not settings.peer_agent_command:
        return
    interfaces = db.execute(
        select(Interface)
        .options(joinedload(Interface.tic_server), joinedload(Interface.tak_server))
        .where(Interface.tak_server_id.is_not(None), Interface.route_mode == RouteMode.VIA_TAK)
    ).scalars().all()
    if not interfaces:
        return

    now = utc_now()
    repair_state = _load_tak_tunnel_repair_state(db)
    by_pair: dict[tuple[int, int], list[Interface]] = {}
    for interface in interfaces:
        if interface.tic_server is None or interface.tak_server is None:
            continue
        by_pair.setdefault((interface.tic_server_id, interface.tak_server_id), []).append(interface)

    changed = False
    for _, pair_interfaces in by_pair.items():
        sample = pair_interfaces[0]
        healed_during_reconcile = False
        previous_status_label = "unknown"
        pair_state_changed = False
        pair_state = dict(repair_state.get(_tak_tunnel_pair_key(sample.tic_server_id, sample.tak_server_id)) or {})
        blocked_status, cooldown_until = _tak_tunnel_pair_runtime_status(pair_state, now=now)
        try:
            response = _run_tic_executor(
                _build_server_executor_payload(
                    action="verify_tak_tunnel_status",
                    server=sample.tic_server,
                    extra={"tak_server": _server_agent_identity_payload(sample.tak_server)},
                )
            )
            _validate_agent_contract_response(response)
            if response.get("ok") is not True:
                tunnel_status = {"is_active": False, "status": "error"}
            else:
                tunnel_status = response.get("tunnel_status") or {}
        except Exception:
            tunnel_status = {"is_active": False, "status": "error"}

        previous_status_label = str(
            tunnel_status.get("status") or ("active" if bool(tunnel_status.get("is_active")) else "error")
        )[:32]

        if not bool(tunnel_status.get("is_active")) and blocked_status is None:
            try:
                _repair_tak_tunnel_transport(
                    db,
                    tic_server=sample.tic_server,
                    tak_server=sample.tak_server,
                    allow_reprovision=False,
                )
                response = _run_tic_executor(
                    _build_server_executor_payload(
                        action="verify_tak_tunnel_status",
                        server=sample.tic_server,
                        extra={"tak_server": _server_agent_identity_payload(sample.tak_server)},
                    )
                )
                _validate_agent_contract_response(response)
                if response.get("ok") is True:
                    healed_status = response.get("tunnel_status") or {}
                    if bool(healed_status.get("is_active")):
                        tunnel_status = healed_status
                        healed_during_reconcile = True
            except Exception:
                db.rollback()
            if not healed_during_reconcile:
                pair_state, state_changed = _tak_tunnel_register_failure(
                    repair_state,
                    tic_server_id=sample.tic_server_id,
                    tak_server_id=sample.tak_server_id,
                    now=now,
                )
                blocked_status, cooldown_until = _tak_tunnel_pair_runtime_status(pair_state, now=now)
                pair_state_changed = pair_state_changed or state_changed
                changed = changed or state_changed

        is_active = bool(tunnel_status.get("is_active"))
        status_label = str(tunnel_status.get("status") or ("active" if is_active else "error"))[:32]
        if is_active:
            changed = _tak_tunnel_register_success(
                repair_state,
                tic_server_id=sample.tic_server_id,
                tak_server_id=sample.tak_server_id,
                now=now,
            ) or changed
        elif blocked_status is not None:
            status_label = blocked_status
        if healed_during_reconcile and is_active:
            status_label = "recovered"
            write_audit_log(
                db,
                event_type="tak_tunnels.auto_recovered",
                severity="info",
                message=f"Tak tunnel auto-recovered: tic={sample.tic_server.name}, tak={sample.tak_server.name}",
                message_ru=f"Панель автоматически восстановила туннель Tic/Tak: {sample.tic_server.name} → {sample.tak_server.name}",
                server_id=sample.tic_server.id,
                details=json.dumps(
                    {
                        "tic_server_id": sample.tic_server.id,
                        "tic_server_name": sample.tic_server.name,
                        "tak_server_id": sample.tak_server.id,
                        "tak_server_name": sample.tak_server.name,
                        "previous_status": previous_status_label,
                        "failure_count_before_recovery": int(pair_state.get("failure_count") or 0),
                        "interface_names": sorted(interface.name for interface in pair_interfaces),
                    },
                    ensure_ascii=False,
                ),
                commit=False,
            )
        if status_label == "manual_attention_required" and pair_state_changed:
            details = {
                "tic_server_id": sample.tic_server.id,
                "tic_server_name": sample.tic_server.name,
                "tak_server_id": sample.tak_server.id,
                "tak_server_name": sample.tak_server.name,
                "failure_count": int(pair_state.get("failure_count") or 0),
                "interface_names": sorted(interface.name for interface in pair_interfaces),
            }
            if cooldown_until is not None:
                details["cooldown_until"] = cooldown_until.isoformat()
            write_audit_log(
                db,
                event_type="tak_tunnels.manual_attention_required",
                severity="warning",
                message=f"Tak tunnel requires manual attention: tic={sample.tic_server.name}, tak={sample.tak_server.name}",
                message_ru=f"Автовосстановление туннеля Tic/Tak остановлено: требуется ручное вмешательство для пары {sample.tic_server.name} → {sample.tak_server.name}",
                server_id=sample.tic_server.id,
                details=json.dumps(details, ensure_ascii=False),
                commit=False,
            )
        elif status_label == "cooldown" and pair_state_changed:
            details = {
                "tic_server_id": sample.tic_server.id,
                "tic_server_name": sample.tic_server.name,
                "tak_server_id": sample.tak_server.id,
                "tak_server_name": sample.tak_server.name,
                "failure_count": int(pair_state.get("failure_count") or 0),
                "interface_names": sorted(interface.name for interface in pair_interfaces),
            }
            if cooldown_until is not None:
                details["cooldown_until"] = cooldown_until.isoformat()
            write_audit_log(
                db,
                event_type="tak_tunnels.cooldown",
                severity="warning",
                message=f"Tak tunnel auto-repair delayed by cooldown: tic={sample.tic_server.name}, tak={sample.tak_server.name}",
                message_ru=f"Повторная попытка восстановления туннеля Tic/Tak отложена для пары {sample.tic_server.name} → {sample.tak_server.name}",
                server_id=sample.tic_server.id,
                details=json.dumps(details, ensure_ascii=False),
                commit=False,
            )
        for interface in pair_interfaces:
            try:
                if is_active:
                    if interface.tak_tunnel_fallback_active:
                        _run_agent_executor_logged(
                            db,
                            _build_tic_executor_payload(
                                "update_interface_route_mode",
                                interface,
                                exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
                                block_filters_enabled=block_filters_enabled(db),
                                extra={"target_state": {"route_mode": RouteMode.VIA_TAK.value}},
                            ),
                        )
                        interface.tak_tunnel_fallback_active = False
                        changed = True
                    if interface.tak_tunnel_last_status != status_label:
                        interface.tak_tunnel_last_status = status_label
                        changed = True
                    db.add(interface)
                    continue

                if not interface.tak_tunnel_fallback_active:
                    _run_agent_executor_logged(
                        db,
                        _build_tic_executor_payload(
                            "update_interface_route_mode",
                            interface,
                            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
                            block_filters_enabled=block_filters_enabled(db),
                            extra={"target_state": {"route_mode": RouteMode.STANDALONE.value}},
                        ),
                    )
                    interface.tak_tunnel_fallback_active = True
                    changed = True
                if interface.tak_tunnel_last_status != status_label:
                    interface.tak_tunnel_last_status = status_label
                    changed = True
                db.add(interface)
            except Exception:
                if interface.tak_tunnel_last_status != "error":
                    interface.tak_tunnel_last_status = "error"
                    db.add(interface)
                    changed = True

    if changed:
        _save_tak_tunnel_repair_state(db, repair_state)
        db.commit()


def _trigger_tak_tunnel_reconcile(db: Session) -> None:
    try:
        _reconcile_tak_tunnel_routes(db)
    except Exception:
        db.rollback()


def _validate_agent_contract_response(response: dict[str, object]) -> None:
    contract_version = response.get("contract_version")
    supported_contracts = response.get("supported_contracts")
    if contract_version is None and supported_contracts is None:
        # Legacy agents are accepted so the panel can still reach them and
        # perform the update path before relying on newer capabilities.
        return
    if isinstance(supported_contracts, list):
        normalized = {str(item) for item in supported_contracts}
        if normalized.intersection(SUPPORTED_AGENT_CONTRACTS):
            return
    if contract_version is not None and str(contract_version) in SUPPORTED_AGENT_CONTRACTS:
        return
    raise ServerOperationUnavailableError("Peer server executor uses unsupported contract version")


def _audit_event_type_label(event_type: str) -> str:
    return AUDIT_EVENT_TYPE_LABELS.get(event_type, event_type)


def _format_audit_details_ru(log: AuditLog) -> str | None:
    if not log.details:
        return None
    try:
        details = json.loads(log.details)
    except json.JSONDecodeError:
        details = None

    if log.event_type == "http.401":
        return "Панель попросила пользователя войти перед продолжением."

    if log.event_type == "http.403":
        return "Панель заблокировала действие по правилам доступа."

    if log.event_type == "http.404":
        return "Запрошенная страница или запись не найдена."

    if log.event_type == "http.400":
        return "Запрос не прошёл проверку данных."

    if details is None:
        return log.details

    if log.event_type == "diagnostics.run" and isinstance(details, dict):
        overall_status = str(details.get("overall_status") or "unknown")
        status_label = {
            "ok": "всё штатно",
            "warning": "есть предупреждения",
            "error": "нужна проверка",
        }.get(overall_status, overall_status)
        problem_count = int(details.get("problem_count") or 0)
        recommendation_count = int(details.get("recommendation_count") or 0)
        incident_count = int(details.get("incident_count") or 0)
        parts = [
            f"итог: {status_label}",
            f"проблемных узлов: {problem_count}",
            f"рекомендаций: {recommendation_count}",
            f"инцидентов: {incident_count}",
        ]
        top_nodes = details.get("problem_nodes") or []
        if isinstance(top_nodes, list) and top_nodes:
            parts.append(f"узлы: {', '.join(str(item) for item in top_nodes[:4])}")
        return " | ".join(parts)

    if log.event_type == "tak_tunnels.auto_recovered" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        interface_names = details.get("interface_names") or []
        previous_status = str(details.get("previous_status") or "")
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if previous_status:
            parts.append(f"до восстановления: {_tak_tunnel_status_label_ui(previous_status)}")
        if isinstance(interface_names, list) and interface_names:
            parts.append(f"интерфейсы: {', '.join(str(item) for item in interface_names[:6])}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type == "tak_tunnels.manual_repaired" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        failure_count = int(details.get("failure_count_before_repair") or 0)
        repair_strategy = str(details.get("repair_strategy") or "")
        interface_names = details.get("interface_names") or []
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if repair_strategy:
            parts.append(f"стратегия: {repair_strategy}")
        if failure_count:
            parts.append(f"ошибок до ремонта: {failure_count}")
        if isinstance(interface_names, list) and interface_names:
            parts.append(f"интерфейсы: {', '.join(str(item) for item in interface_names[:6])}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type == "tak_tunnels.artifacts_rotated" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        tunnel_id = str(details.get("tunnel_id") or "")
        artifact_revision = int(details.get("artifact_revision") or 0)
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if tunnel_id:
            parts.append(f"туннель: {tunnel_id}")
        if artifact_revision:
            parts.append(f"ревизия артефактов: {artifact_revision}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type == "tak_tunnels.cooldown" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        failure_count = int(details.get("failure_count") or 0)
        cooldown_until = str(details.get("cooldown_until") or "")
        interface_names = details.get("interface_names") or []
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if failure_count:
            parts.append(f"неудачных попыток: {failure_count}")
        if cooldown_until:
            parts.append(f"повторная попытка после: {cooldown_until}")
        if isinstance(interface_names, list) and interface_names:
            parts.append(f"интерфейсы: {', '.join(str(item) for item in interface_names[:6])}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type == "tak_tunnels.backoff_cleared" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        failure_count = int(details.get("failure_count_before_clear") or 0)
        manual_attention = bool(details.get("manual_attention_before_clear"))
        interface_names = details.get("interface_names") or []
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if failure_count:
            parts.append(f"сброшено неудачных попыток: {failure_count}")
        if manual_attention:
            parts.append("снят флаг ручной проверки")
        if isinstance(interface_names, list) and interface_names:
            parts.append(f"интерфейсы: {', '.join(str(item) for item in interface_names[:6])}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type == "tak_tunnels.manual_attention_required" and isinstance(details, dict):
        tic_server_name = str(details.get("tic_server_name") or "")
        tak_server_name = str(details.get("tak_server_name") or "")
        failure_count = int(details.get("failure_count") or 0)
        cooldown_until = str(details.get("cooldown_until") or "")
        interface_names = details.get("interface_names") or []
        parts = []
        if tic_server_name or tak_server_name:
            parts.append(f"пара: {tic_server_name} → {tak_server_name}".strip())
        if failure_count:
            parts.append(f"неудачных попыток: {failure_count}")
        if cooldown_until:
            parts.append(f"повторная попытка после: {cooldown_until}")
        if isinstance(interface_names, list) and interface_names:
            parts.append(f"интерфейсы: {', '.join(str(item) for item in interface_names[:6])}")
        return " | ".join(part for part in parts if part) or log.details

    if log.event_type not in {"agent.command", "agent.command_failed"} or not isinstance(details, dict):
        return log.details

    action = str(details.get("action") or "")
    action_label = AGENT_ACTION_LABELS_RU.get(action, action or "неизвестное действие")
    component = str(details.get("component") or "")
    server_name = str(details.get("server_name") or "")
    interface_name = str(details.get("interface_name") or "")
    peer_slot = details.get("peer_slot")

    parts = [action_label]
    if server_name:
        parts.append(f"сервер: {server_name}")
    if interface_name:
        parts.append(f"интерфейс: {interface_name}")
    if peer_slot is not None:
        parts.append(f"пир: {peer_slot}")
    if component:
        parts.append(f"компонент: {component}")
    return " | ".join(parts)


def serialize_audit_log(log: AuditLog) -> AuditLogView:
    server_url = None
    if log.server_id is not None:
        server_url = f"/admin/servers?bucket=active&selected_server_id={log.server_id}"
    pair_label = None
    diagnostics_url = None
    if log.event_type in {
        "tak_tunnels.auto_recovered",
        "tak_tunnels.backoff_cleared",
        "tak_tunnels.cooldown",
        "tak_tunnels.manual_attention_required",
        "tak_tunnels.manual_repaired",
        "tak_tunnels.artifacts_rotated",
    } and log.details:
        try:
            details = json.loads(log.details)
        except json.JSONDecodeError:
            details = None
        if isinstance(details, dict):
            tic_server_id = details.get("tic_server_id")
            tak_server_id = details.get("tak_server_id")
            tic_server_name = str(details.get("tic_server_name") or "")
            tak_server_name = str(details.get("tak_server_name") or "")
            if tic_server_name or tak_server_name:
                pair_label = f"{tic_server_name} → {tak_server_name}".strip()
            if tic_server_id and tak_server_id:
                diagnostics_url = (
                    f"/admin/diagnostics?focused_tic_server_id={int(tic_server_id)}"
                    f"&focused_tak_server_id={int(tak_server_id)}#check-tak_tunnels"
                )
    return AuditLogView(
        id=log.id,
        event_type=log.event_type,
        event_type_label=_audit_event_type_label(log.event_type),
        severity=log.severity,
        message=log.message,
        message_ru=log.message_ru,
        actor_user_id=log.actor_user_id,
        actor_login=log.actor_user.login if log.actor_user else None,
        target_user_id=log.target_user_id,
        target_login=log.target_user.login if log.target_user else None,
        server_id=log.server_id,
        server_name=log.server.name if log.server else None,
        server_url=server_url,
        pair_label=pair_label,
        diagnostics_url=diagnostics_url,
        details=log.details,
        details_ru=_format_audit_details_ru(log),
        created_at=log.created_at,
    )


def get_audit_logs_page(
    db: Session,
    actor: User,
    *,
    severity: str = "all",
    event_type: str = "all",
    user_id: int | None = None,
    server_id: int | None = None,
    sort: str = "newest",
) -> AuditLogsPageView:
    require_admin(actor)
    purge_old_audit_logs(db)
    db.commit()

    query = (
        select(AuditLog)
        .options(
            joinedload(AuditLog.actor_user),
            joinedload(AuditLog.target_user),
            joinedload(AuditLog.server),
        )
    )
    if severity in {"info", "error", "warning"}:
        query = query.where(AuditLog.severity == severity)
    if event_type != "all":
        query = query.where(AuditLog.event_type == event_type)
    if user_id is not None:
        query = query.where(or_(AuditLog.actor_user_id == user_id, AuditLog.target_user_id == user_id))
    if server_id is not None:
        query = query.where(AuditLog.server_id == server_id)

    if sort == "event_type":
        query = query.order_by(AuditLog.event_type.asc(), AuditLog.created_at.desc())
    elif sort == "user":
        query = query.order_by(AuditLog.actor_user_id.asc().nullslast(), AuditLog.target_user_id.asc().nullslast(), AuditLog.created_at.desc())
    elif sort == "server":
        query = query.order_by(AuditLog.server_id.asc().nullslast(), AuditLog.created_at.desc())
    else:
        sort = "newest"
        query = query.order_by(AuditLog.created_at.desc())

    logs = db.execute(query.limit(200)).scalars().all()
    event_types = db.execute(select(AuditLog.event_type).distinct().order_by(AuditLog.event_type.asc())).scalars().all()
    users = db.execute(select(User).order_by(User.login.asc())).scalars().all()
    servers = db.execute(select(Server).order_by(Server.name.asc())).scalars().all()
    return AuditLogsPageView(
        logs=[serialize_audit_log(log) for log in logs],
        event_types=list(event_types),
        event_type_labels={item: _audit_event_type_label(item) for item in event_types},
        users=[{"id": user.id, "name": user.login} for user in users],
        servers=serialize_server_options(servers),
        selected_severity=severity,
        selected_event_type=event_type,
        selected_user_id=user_id,
        selected_server_id=server_id,
        selected_sort=sort,
    )


def get_panel_diagnostics_page(actor: User) -> DiagnosticsPageView:
    require_admin(actor)
    return DiagnosticsPageView(
        has_report=False,
        overall_status="idle",
        summary="Проверка ещё не запускалась.",
        problem_nodes=[],
        checks=[],
        recommendations=[],
        recent_incidents=[],
        run_history=[],
    )


def get_agent_contract_page(actor: User) -> AgentContractPageView:
    require_admin(actor)
    doc_path = ROOT_DIR / "docs" / "agent_contract.md"
    try:
        raw_markdown = doc_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw_markdown = "Contract document is missing."

    actions = [
        AgentContractActionView(
            action=action,
            component=_component_for_action(action),
            component_label=AGENT_COMPONENT_LABELS.get(_component_for_action(action), _component_for_action(action)),
            capabilities=capabilities,
        )
        for action, capabilities in sorted(ACTION_CAPABILITIES.items(), key=lambda item: item[0])
    ]
    components = [
        {"key": key, "label": label}
        for key, label in AGENT_COMPONENT_LABELS.items()
    ]
    return AgentContractPageView(
        contract_version=AGENT_CONTRACT_VERSION,
        supported_contracts=SUPPORTED_AGENT_CONTRACTS,
        panel_version=get_panel_version(),
        components=components,
        actions=actions,
        raw_markdown=raw_markdown,
        doc_path=str(doc_path),
    )


def _get_recent_diagnostics_incidents(db: Session, *, limit: int = 10) -> list[AuditLogView]:
    incident_event_types = {
        "agent.command_failed",
        "backups.create_failed",
        "panel_jobs.failed",
        "peers.expire_delete_failed",
    }
    logs = (
        db.execute(
            select(AuditLog)
            .options(
                joinedload(AuditLog.actor_user),
                joinedload(AuditLog.target_user),
                joinedload(AuditLog.server),
            )
            .where(
                or_(
                    AuditLog.event_type.in_(incident_event_types),
                    AuditLog.severity == "error",
                    AuditLog.server_id.is_not(None),
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit * 3)
        )
        .scalars()
        .all()
    )

    incidents: list[AuditLogView] = []
    seen_ids: set[int] = set()
    for log in logs:
        if log.id in seen_ids:
            continue
        if log.event_type not in incident_event_types and log.severity not in {"error", "warning"}:
            continue
        incidents.append(serialize_audit_log(log))
        seen_ids.add(log.id)
        if len(incidents) >= limit:
            break
    return incidents


def _get_diagnostics_run_history(db: Session, *, limit: int = 10) -> list[AuditLogView]:
    logs = (
        db.execute(
            select(AuditLog)
            .options(
                joinedload(AuditLog.actor_user),
                joinedload(AuditLog.target_user),
                joinedload(AuditLog.server),
            )
            .where(AuditLog.event_type == "diagnostics.run")
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [serialize_audit_log(log) for log in logs]


def _run_access_routes_diagnostics() -> tuple[str, str, list[str]]:
    from fastapi.testclient import TestClient

    from app.database import SessionLocal
    from app.main import app
    from app.security import create_access_token

    details: list[str] = []

    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        user = db.execute(select(User).where(User.role == UserRole.USER).order_by(User.id.asc())).scalars().first()
        peer = (
            db.execute(
                select(Peer)
                .join(Interface)
                .where(Interface.user_id == user.id if user else None, Interface.is_pending_owner.is_(False))
                .order_by(Peer.id.asc())
            ).scalars().first()
            if user is not None
            else None
        )

    if admin is None or user is None:
        return "warning", "Проверка доступа выполнена не полностью: нет тестовых пользователей.", ["Нужны admin и обычный пользователь."]

    diagnostics_headers = {"X-Nelomai-Internal-Check": "access-routes-diagnostics"}
    admin_headers = {
        "Cookie": f"access_token={create_access_token(admin.login)}",
        **diagnostics_headers,
    }
    user_headers = {
        "Cookie": f"access_token={create_access_token(user.login)}",
        **diagnostics_headers,
    }

    try:
        with TestClient(app) as client:
            dashboard_redirect = client.get("/dashboard", headers=diagnostics_headers, follow_redirects=False)
            if dashboard_redirect.status_code != 303:
                raise RuntimeError(f"/dashboard без авторизации вернул {dashboard_redirect.status_code}")
            details.append("Неавторизованный доступ к /dashboard уходит на login.")

            user_admin_response = client.get("/admin", headers=user_headers)
            if user_admin_response.status_code != 403:
                raise RuntimeError(f"/admin для обычного пользователя вернул {user_admin_response.status_code}")
            details.append("Обычный пользователь не может открыть /admin.")

            user_diagnostics_response = client.get("/admin/diagnostics", headers=user_headers)
            if user_diagnostics_response.status_code != 403:
                raise RuntimeError(f"/admin/diagnostics для обычного пользователя вернул {user_diagnostics_response.status_code}")
            details.append("Обычный пользователь не может открыть /admin/diagnostics.")

            if peer is not None:
                preview_write_response = client.put(
                    f"/api/peers/{peer.id}/comment?preview=1",
                    json={"comment": "preview forbidden"},
                    headers=admin_headers,
                )
                if preview_write_response.status_code != 403:
                    raise RuntimeError(f"preview write для пира вернул {preview_write_response.status_code}")
                details.append("Preview-mode блокирует write-действия администратора.")

        return "ok", "Базовые сценарии доступа и маршрутов работают корректно.", details
    except Exception as exc:
        details.append(str(exc))
        return "error", "Проверка доступа и маршрутов нашла расхождения в правилах авторизации.", details


def _build_diagnostics_recommendations(checks: list[DiagnosticsCheckView]) -> list[DiagnosticsRecommendationView]:
    recommendations: list[DiagnosticsRecommendationView] = []
    by_key = {check.key: check for check in checks}

    def add(
        key: str,
        title: str,
        message: str,
        *,
        severity: str = "warning",
        action_label: str | None = None,
        action_url: str | None = None,
    ) -> None:
        recommendations.append(
            DiagnosticsRecommendationView(
                key=key,
                title=title,
                message=message,
                severity=severity,
                action_label=action_label,
                action_url=action_url,
            )
        )

    database_check = by_key.get("database")
    if database_check and database_check.status == "error":
        add(
            "database",
            "Проверить доступ к базе данных",
            "Панель не смогла выполнить базовый запрос к БД. Нужно проверить DATABASE_URL, доступность PostgreSQL и последние ошибки в логах.",
            severity="error",
            action_label="Открыть логи",
            action_url="/admin/logs?severity=error",
        )

    migrations_check = by_key.get("migrations")
    if migrations_check and migrations_check.status != "ok":
        add(
            "migrations",
            "Проверить состояние миграций",
            "Схема базы выглядит неполной или повреждённой. Нужно сверить таблицу alembic_version и применённые миграции перед дальнейшими изменениями.",
            severity="warning" if migrations_check.status == "warning" else "error",
            action_label="Открыть логи",
            action_url="/admin/logs?severity=error",
        )

    settings_check = by_key.get("settings")
    if settings_check and settings_check.status != "ok":
        add(
            "settings",
            "Довести критичные настройки панели",
            "В конфигурации остались dev-значения или пустые обязательные поля. Имеет смысл сначала закрыть эти настройки, иначе часть функций будет работать нестабильно.",
            action_label="Открыть настройки",
            action_url="/admin?tab=settings&settings_view=basic",
        )

    agent_check = by_key.get("agent_channel")
    if agent_check and agent_check.status != "ok":
        add(
            "agent_channel",
            "Настроить канал panel → agent",
            "Agent-backed действия сейчас не готовы к работе. Нужно задать PEER_AGENT_COMMAND и затем повторить самодиагностику.",
            severity="error",
            action_label="Открыть логи",
            action_url="/admin/logs?event_type=agent.command_failed",
        )

    backup_storage_check = by_key.get("backup_storage")
    if backup_storage_check and backup_storage_check.status != "ok":
        add(
            "backup_storage",
            "Починить хранилище бэкапов",
            "Панель не может писать в backup storage. Нужно проверить путь хранения, права доступа и свободное место.",
            severity="error",
            action_label="Открыть настройки backup",
            action_url="/admin?tab=settings&settings_view=backups",
        )

    latest_backup_check = by_key.get("latest_full_backup")
    if latest_backup_check and latest_backup_check.status != "ok":
        add(
            "latest_full_backup",
            "Создать или восстановить свежий full backup",
            "Для безопасной работы панели нужен доступный последний full backup. Если файла нет или он не создан, сначала исправьте это.",
            severity="warning" if latest_backup_check.status == "warning" else "error",
            action_label="Открыть бэкапы",
            action_url="/admin?tab=settings&settings_view=backups",
        )

    jobs_check = by_key.get("jobs")
    if jobs_check and jobs_check.status != "ok":
        add(
            "jobs",
            "Разобрать проблемные фоновые задачи",
            "В панели есть задачи в проблемном состоянии. Их стоит проверить до новых операций, чтобы не копить зависшие сценарии и конфликтующие действия.",
            action_label="Открыть задачи",
            action_url="/admin/jobs",
        )

    servers_check = by_key.get("servers")
    if servers_check and servers_check.status != "ok":
        add(
            "servers",
            "Проверить проблемные серверы",
            "Часть серверов недоступна или исключена. Нужно проверить их состояние, иначе agent-команды и бэкапы будут давать неполную картину.",
            action_label="Открыть серверы",
            action_url="/admin/servers",
        )

    runtime_check = by_key.get("agent_runtime")
    if runtime_check and runtime_check.status != "ok":
        add(
            "agent_runtime",
            "Проверить runtime окружение серверных агентов",
            "Panel-side канал может быть настроен, но сами серверные runtime-зависимости могут быть не готовы. Нужно сверить Linux/WireGuard readiness на странице серверов и устранить проблемные узлы.",
            severity="warning" if runtime_check.status == "warning" else "error",
            action_label="Открыть серверы",
            action_url="/admin/servers",
        )

    tak_tunnels_check = by_key.get("tak_tunnels")
    if tak_tunnels_check and tak_tunnels_check.status != "ok":
        add(
            "tak_tunnels",
            "Проверить межсерверные туннели Tic ↔ Tak",
            "Часть интерфейсов с режимом via_tak может работать в fallback standalone. Нужно проверить проблемные пары Tic/Tak и восстановить общий межсерверный туннель.",
            severity="warning" if tak_tunnels_check.status == "warning" else "error",
            action_label="Открыть серверы",
            action_url="/admin/servers",
        )

    beta_check = by_key.get("beta_readiness")
    if beta_check and beta_check.status != "ok":
        add(
            "beta_readiness",
            "Довести панель до beta-ready состояния",
            "Для ограниченного beta rollout ещё остались пробелы в production-like конфигурации, backup readiness или server/Tic/Tak контуре. Сначала закройте их по diagnostics и beta runbook.",
            severity="warning" if beta_check.status == "warning" else "error",
            action_label="Открыть серверы",
            action_url="/admin/servers",
        )

    access_check = by_key.get("access_routes")
    if access_check and access_check.status != "ok":
        add(
            "access_routes",
            "Проверить правила доступа и preview-mode",
            "Один или несколько базовых сценариев авторизации работают не так, как ожидается. Нужно проверить redirect на login, блокировку admin routes для user и запрет write-действий в preview-mode.",
            severity="error" if access_check.status == "error" else "warning",
            action_label="Открыть /dashboard",
            action_url="/dashboard",
        )

    if not recommendations and checks:
        add(
            "healthy",
            "Критичных действий не требуется",
            "Самодиагностика не нашла проблем, требующих немедленного вмешательства. Можно переходить к следующему этапу работ.",
            severity="info",
        )

    return recommendations


def _build_beta_readiness_summary(
    *,
    backup_status: str,
    latest_backup_status: str,
    runtime_status: str,
    tak_tunnel_status: str,
) -> BetaReadinessSummaryView:
    status = "ok"
    details: list[str] = ["Runbook: docs/panel_beta_runbook.md"]
    if settings.secret_key == "dev-only-change-me-with-a-long-random-value":
        status = "warning"
        details.append("Не задан production SECRET_KEY.")
    if settings.debug:
        status = "warning"
        details.append("DEBUG включён.")
    if settings.database_url.startswith("sqlite"):
        status = "warning"
        details.append("Панель всё ещё использует SQLite вместо PostgreSQL.")
    if not settings.nelomai_git_repo.strip():
        status = "warning"
        details.append("Не задан NELOMAI_GIT_REPO для deploy/bootstrap/update path.")
    if not settings.peer_agent_command:
        status = "error"
        details.append("Не задан PEER_AGENT_COMMAND.")
    if backup_status != "ok":
        status = "error" if backup_status == "error" else status
        details.append("Backup storage не готов для beta.")
    if latest_backup_status != "ok":
        status = "warning" if status == "ok" else status
        details.append("Нет подтверждённого свежего full backup.")
    if runtime_status != "ok":
        status = "warning" if status == "ok" else status
        details.append("Не все server-agent runtime checks зелёные.")
    if tak_tunnel_status != "ok":
        status = "warning" if status == "ok" else status
        details.append("Есть проблемы с live Tic ↔ Tak контуром или его состоянием.")
    message = (
        "Панель выглядит готовой к ограниченному beta rollout."
        if status == "ok"
        else "Перед запуском малой тестовой группы стоит закрыть отмеченные beta-gap’ы."
    )
    return BetaReadinessSummaryView(
        status=status,
        message=message,
        details=details,
        runbook_url="/admin/diagnostics",
        settings_url="/admin?tab=settings&settings_view=basic",
        backups_url="/admin?tab=settings&settings_view=backups",
        servers_url="/admin/servers",
    )


def run_panel_diagnostics(
    db: Session,
    actor: User,
    *,
    focused_tic_server_id: int | None = None,
    focused_tak_server_id: int | None = None,
    include_live_checks: bool = True,
) -> DiagnosticsPageView:
    require_admin(actor)
    checks: list[DiagnosticsCheckView] = []
    problem_nodes: list[str] = []
    recent_incidents = _get_recent_diagnostics_incidents(db)
    focused_tak_tunnel: DiagnosticsFocusedTakTunnelView | None = None

    try:
        db.execute(select(User.id).limit(1)).first()
        checks.append(
            DiagnosticsCheckView(
                key="database",
                title="База данных",
                status="ok",
                message="Подключение к базе данных работает.",
                source_label="Открыть логи",
                source_url="/admin/logs",
            )
        )
    except Exception as exc:
        checks.append(
            DiagnosticsCheckView(
                key="database",
                title="База данных",
                status="error",
                message=f"Панель не смогла выполнить запрос к базе данных: {exc}",
                source_label="Открыть логи",
                source_url="/admin/logs?severity=error",
            )
        )
        problem_nodes.append("База данных")

    migration_details: list[str] = []
    migration_status = "ok"
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if "alembic_version" not in tables:
            migration_status = "warning"
            migration_details.append("Таблица alembic_version отсутствует.")
        else:
            row = db.execute(select(func.count()).select_from(AppSetting)).scalar_one()
            migration_details.append(f"База отвечает, app_settings записей: {row}.")
    except Exception as exc:
        migration_status = "error"
        migration_details.append(f"Не удалось проверить состояние миграций: {exc}")
    checks.append(
        DiagnosticsCheckView(
            key="migrations",
            title="Миграции",
            status=migration_status,
            message="Панель видит признаки корректно инициализированной схемы." if migration_status == "ok" else "Схема базы требует внимания.",
            details=migration_details,
            source_label="Открыть логи",
            source_url="/admin/logs?severity=error",
        )
    )
    if migration_status != "ok":
        problem_nodes.append("Миграции")

    settings_values = get_basic_settings(db)
    settings_details: list[str] = []
    settings_status = "ok"
    if settings.secret_key == "dev-only-change-me-with-a-long-random-value":
        settings_status = "warning"
        settings_details.append("Используется placeholder SECRET_KEY.")
    if settings.debug:
        settings_status = "warning"
        settings_details.append("DEBUG включён.")
    if settings.database_url.startswith("sqlite"):
        settings_status = "warning"
        settings_details.append("Используется SQLite, а не PostgreSQL.")
    if not settings_values.get("nelomai_git_repo", "").strip():
        settings_status = "warning"
        settings_details.append("Не задан Git-репозиторий Nelomai.")
    checks.append(
        DiagnosticsCheckView(
            key="settings",
            title="Критичные настройки",
            status=settings_status,
            message="Проверка базовых настроек панели завершена." if settings_details else "Критичных замечаний не найдено.",
            details=settings_details,
            source_label="Открыть настройки",
            source_url="/admin?tab=settings&settings_view=basic",
        )
    )
    if settings_status != "ok":
        problem_nodes.append("Настройки панели")

    agent_details: list[str] = []
    agent_status = "ok"
    if not settings.peer_agent_command:
        agent_status = "error"
        agent_details.append("PEER_AGENT_COMMAND не задан.")
    checks.append(
        DiagnosticsCheckView(
            key="agent_channel",
            title="Канал panel → agent",
            status=agent_status,
            message="Панель готова отправлять команды агенту." if agent_status == "ok" else "Канал agent-команд сейчас не готов.",
            details=agent_details,
            source_label="Открыть логи",
            source_url="/admin/logs?event_type=agent.command_failed",
        )
    )
    if agent_status != "ok":
        problem_nodes.append("Канал agent-команд")

    backup_path = backup_storage_path(db)
    backup_details: list[str] = [f"Путь: {backup_path}"]
    backup_status = "ok"
    try:
        backup_path.mkdir(parents=True, exist_ok=True)
        probe = backup_path / ".nelomai-diagnostics-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        backup_status = "error"
        backup_details.append(f"Нет доступа к backup storage: {exc}")
    checks.append(
        DiagnosticsCheckView(
            key="backup_storage",
            title="Хранилище бэкапов",
            status=backup_status,
            message="Backup storage доступно для записи." if backup_status == "ok" else "Backup storage недоступно для записи.",
            details=backup_details,
            source_label="Открыть настройки backup",
            source_url="/admin?tab=settings&settings_view=backups",
        )
    )
    if backup_status != "ok":
        problem_nodes.append("Хранилище бэкапов")

    latest_full = _latest_completed_full_backup(db)
    latest_backup_status = "ok"
    latest_backup_details: list[str] = []
    if latest_full is None:
        latest_backup_status = "warning"
        latest_backup_details.append("В панели нет завершённого full backup.")
    else:
        created_at = latest_full.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        latest_backup_details.append(f"Последний full backup: {latest_full.filename}")
        latest_backup_details.append(f"Создан: {created_at.astimezone(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}")
        backup_file = Path(latest_full.storage_path)
        if not backup_file.exists():
            latest_backup_status = "error"
            latest_backup_details.append("Файл последнего full backup отсутствует на диске панели.")
    checks.append(
        DiagnosticsCheckView(
            key="latest_full_backup",
            title="Последний full backup",
            status=latest_backup_status,
            message="Последний full backup доступен." if latest_backup_status == "ok" else "Последний full backup требует внимания.",
            details=latest_backup_details,
            source_label="Открыть бэкапы",
            source_url="/admin?tab=settings&settings_view=backups",
        )
    )
    if latest_backup_status != "ok":
        problem_nodes.append("Последний full backup")

    mark_stuck_panel_jobs(db)
    problem_jobs = db.execute(select(PanelJob).where(PanelJob.status.in_(PANEL_JOB_PROBLEM_STATUSES))).scalars().all()
    active_jobs = db.execute(select(PanelJob).where(PanelJob.status.in_(PANEL_JOB_ACTIVE_STATUSES))).scalars().all()
    jobs_status = "ok"
    jobs_details: list[str] = []
    if problem_jobs:
        jobs_status = "warning"
        jobs_details.extend([f"{job.job_type}: {job.current_stage}" for job in problem_jobs[:5]])
    if active_jobs:
        jobs_details.append(f"Активных задач: {len(active_jobs)}")
    checks.append(
        DiagnosticsCheckView(
            key="jobs",
            title="Фоновые задачи",
            status=jobs_status,
            message="Проблемных задач не найдено." if not problem_jobs else f"Найдены проблемные задачи: {len(problem_jobs)}.",
            details=jobs_details,
            source_label="Открыть задачи",
            source_url="/admin/jobs",
        )
    )
    if jobs_status != "ok":
        problem_nodes.append("Фоновые задачи")

    servers = db.execute(select(Server).order_by(Server.name.asc())).scalars().all()
    excluded_servers = [server.name for server in servers if server.is_excluded]
    offline_servers = [server.name for server in servers if not server.is_excluded and not server.is_active]
    servers_status = "ok"
    server_details = [f"Всего серверов: {len(servers)}"]
    if excluded_servers:
        servers_status = "warning"
        server_details.append(f"Исключены: {', '.join(excluded_servers[:5])}")
    if offline_servers:
        servers_status = "warning"
        server_details.append(f"Не отвечают: {', '.join(offline_servers[:5])}")
    checks.append(
        DiagnosticsCheckView(
            key="servers",
            title="Серверные узлы",
            status=servers_status,
            message="Серверы панели проверены по известному состоянию." if servers else "В панели пока нет серверов.",
            details=server_details,
            source_label="Открыть серверы",
            source_url="/admin/servers",
        )
    )
    if servers_status != "ok":
        problem_nodes.append("Серверные узлы")

    runtime_status = "ok"
    runtime_details: list[str] = []
    active_runtime_servers = [server for server in servers if not server.is_excluded]
    if not settings.peer_agent_command:
        runtime_status = "warning"
        runtime_details.append("PEER_AGENT_COMMAND не задан, agent-side runtime проверить нельзя.")
    elif not active_runtime_servers:
        runtime_details.append("Активных серверов для runtime-проверки нет.")
    elif include_live_checks:
        not_ready_servers: list[str] = []
        failed_servers: list[str] = []
        for server in active_runtime_servers:
            try:
                runtime_view = _build_server_runtime_view(
                    _run_tic_executor(_build_server_executor_payload(action="verify_server_runtime", server=server)),
                    server,
                )
            except ServerOperationUnavailableError as exc:
                runtime_status = "warning" if runtime_status == "ok" else runtime_status
                failed_servers.append(f"{server.name}: {exc}")
                continue
            if not runtime_view.ready:
                runtime_status = "warning" if runtime_status == "ok" else runtime_status
                failed_checks = [check.label for check in runtime_view.checks if check.status.lower() not in {"ok", "ready", "success"}]
                suffix = f" ({', '.join(failed_checks[:3])})" if failed_checks else ""
                not_ready_servers.append(f"{server.name}{suffix}")
        runtime_details.append(f"Проверено серверов: {len(active_runtime_servers)}")
        if not_ready_servers:
            runtime_details.append(f"Не готовы: {', '.join(not_ready_servers[:5])}")
        if failed_servers:
            runtime_details.append(f"Не удалось проверить: {', '.join(failed_servers[:5])}")
    else:
        offline_runtime_servers = [server.name for server in active_runtime_servers if not server.is_active]
        runtime_details.append(f"Серверов в snapshot: {len(active_runtime_servers)}")
        runtime_details.append("Live runtime-check выполняется только по кнопке «Запустить проверку».")
        if offline_runtime_servers:
            runtime_status = "warning"
            runtime_details.append(f"Неактивные по состоянию панели: {', '.join(offline_runtime_servers[:5])}")
    checks.append(
        DiagnosticsCheckView(
            key="agent_runtime",
            title="Runtime серверных агентов",
            status=runtime_status,
            message="Agent-side runtime окружение готово к выполнению операций." if runtime_status == "ok" else "Agent-side runtime требует внимания или не может быть проверен полностью.",
            details=runtime_details,
            source_label="Открыть серверы",
            source_url="/admin/servers",
        )
    )
    if runtime_status != "ok":
        problem_nodes.append("Runtime серверных агентов")

    tak_tunnel_status = "ok"
    tak_tunnel_details: list[str] = []
    via_tak_interfaces = (
        db.execute(
            select(Interface)
            .options(joinedload(Interface.tic_server), joinedload(Interface.tak_server))
            .where(Interface.route_mode == RouteMode.VIA_TAK, Interface.tak_server_id.is_not(None))
            .order_by(Interface.id.asc())
        )
        .scalars()
        .all()
    )
    if not via_tak_interfaces:
        tak_tunnel_details.append("Активных связок via_tak нет.")
    elif not settings.peer_agent_command:
        tak_tunnel_status = "warning"
        tak_tunnel_details.append("PEER_AGENT_COMMAND не задан, состояние межсерверных туннелей проверить нельзя.")
    else:
        tak_tunnel_action_links: list[DiagnosticsCheckView.ActionLinkView] = []
        tak_tunnel_pairs: dict[tuple[int, int], list[Interface]] = {}
        for interface in via_tak_interfaces:
            if interface.tic_server is None or interface.tak_server is None:
                continue
            tak_tunnel_pairs.setdefault((interface.tic_server_id, interface.tak_server_id), []).append(interface)
        tak_tunnel_details.append(f"Проверено пар Tic/Tak: {len(tak_tunnel_pairs)}")
        degraded_pairs: list[str] = []
        failed_pairs: list[str] = []
        recovered_pairs: list[str] = []
        cooldown_pairs: list[str] = []
        manual_attention_pairs: list[str] = []
        rotated_pairs: list[str] = []
        repair_state = _load_tak_tunnel_repair_state(db)
        for pair_interfaces in tak_tunnel_pairs.values():
            sample = pair_interfaces[0]
            if focused_tic_server_id is not None and sample.tic_server_id != focused_tic_server_id:
                continue
            if focused_tak_server_id is not None and sample.tak_server_id != focused_tak_server_id:
                continue
            pair_label = f"{sample.tic_server.name} -> {sample.tak_server.name}"
            pair_state = dict(repair_state.get(_tak_tunnel_pair_key(sample.tic_server_id, sample.tak_server_id)) or {})
            failure_count = int(pair_state.get("failure_count") or 0)
            cooldown_until = _tak_tunnel_parse_datetime(pair_state.get("cooldown_until"))
            if bool(pair_state.get("manual_attention_required")):
                manual_attention_pairs.append(f"{pair_label} · неудачных попыток: {failure_count}")
            elif cooldown_until is not None and cooldown_until > utc_now():
                cooldown_pairs.append(f"{pair_label} - retry after {cooldown_until.isoformat()}")
            if any(
                interface.tak_tunnel_last_status == "recovered" and not interface.tak_tunnel_fallback_active
                for interface in pair_interfaces
            ):
                interface_names = ", ".join(sorted(interface.name for interface in pair_interfaces))
                recovered_pairs.append(f"{pair_label} · интерфейсы: {interface_names}")
            if include_live_checks:
                try:
                    response = _run_tic_executor(
                        _build_server_executor_payload(
                            action="verify_tak_tunnel_status",
                            server=sample.tic_server,
                            extra={"tak_server": _server_agent_identity_payload(sample.tak_server)},
                        )
                    )
                    _validate_agent_contract_response(response)
                    tunnel_status = response.get("tunnel_status") or {}
                    is_active = bool(tunnel_status.get("is_active"))
                    status_value = str(tunnel_status.get("status") or ("active" if is_active else "unknown"))
                    artifact_revision = int(tunnel_status.get("artifact_revision") or 0)
                    if artifact_revision > 0:
                        rotation_event = _latest_tak_tunnel_rotation_event(
                            db,
                            tic_server_id=sample.tic_server_id,
                            tak_server_id=sample.tak_server_id,
                        )
                        rotation_at = rotation_event.created_at.isoformat() if rotation_event is not None else "время неизвестно"
                        rotated_pairs.append(f"{pair_label} - rev {artifact_revision} - {rotation_at}")
                    if not is_active:
                        tak_tunnel_status = "warning" if tak_tunnel_status == "ok" else tak_tunnel_status
                        interface_names = ", ".join(sorted(interface.name for interface in pair_interfaces))
                        degraded_pairs.append(f"{pair_label} ({status_value}) · интерфейсы: {interface_names}")
                except ServerOperationUnavailableError as exc:
                    tak_tunnel_status = "warning" if tak_tunnel_status == "ok" else tak_tunnel_status
                    failed_pairs.append(f"{pair_label}: {exc}")
                    tak_tunnel_action_links.append(
                        DiagnosticsCheckView.ActionLinkView(
                            label=f"{sample.tic_server.name} <-> {sample.tak_server.name}",
                            url=f"/admin/servers?bucket=active&server_type=tic&selected_server_id={sample.tic_server.id}",
                        )
                    )
            else:
                rotation_event = _latest_tak_tunnel_rotation_event(
                    db,
                    tic_server_id=sample.tic_server_id,
                    tak_server_id=sample.tak_server_id,
                )
                if rotation_event is not None:
                    rotation_at = rotation_event.created_at.isoformat()
                    rotated_pairs.append(f"{pair_label} - latest rotation - {rotation_at}")
                if any(interface.tak_tunnel_fallback_active for interface in pair_interfaces):
                    tak_tunnel_status = "warning" if tak_tunnel_status == "ok" else tak_tunnel_status
                    interface_names = ", ".join(sorted(interface.name for interface in pair_interfaces))
                    degraded_pairs.append(f"{pair_label} (fallback) · интерфейсы: {interface_names}")
        if degraded_pairs:
            tak_tunnel_details.append(f"Проблемные туннели: {'; '.join(degraded_pairs[:5])}")
        if failed_pairs:
            tak_tunnel_details.append(f"Не удалось проверить: {'; '.join(failed_pairs[:5])}")
        if recovered_pairs:
            tak_tunnel_details.append(f"Автовосстановлены: {'; '.join(recovered_pairs[:5])}")
        if rotated_pairs:
            tak_tunnel_details.append(f"Последние ротации артефактов: {'; '.join(rotated_pairs[:5])}")
        if cooldown_pairs:
            tak_tunnel_details.append(f"В cooldown: {'; '.join(cooldown_pairs[:5])}")
        if manual_attention_pairs:
            tak_tunnel_details.append(f"Требуют ручного вмешательства: {'; '.join(manual_attention_pairs[:5])}")
        tak_tunnel_action_links.append(
            DiagnosticsCheckView.ActionLinkView(
                label="Логи автовосстановления",
                url="/admin/logs?event_type=tak_tunnels.auto_recovered",
            )
        )
        tak_tunnel_action_links.append(
            DiagnosticsCheckView.ActionLinkView(
                label="Логи cooldown",
                url="/admin/logs?event_type=tak_tunnels.cooldown",
            )
        )
        tak_tunnel_action_links.append(
            DiagnosticsCheckView.ActionLinkView(
                label="Логи ручного внимания",
                url="/admin/logs?event_type=tak_tunnels.manual_attention_required",
            )
        )
        tak_tunnel_action_links.append(
            DiagnosticsCheckView.ActionLinkView(
                label="Логи ручного восстановления",
                url="/admin/logs?event_type=tak_tunnels.manual_repaired",
            )
        )
    if "tak_tunnel_action_links" in locals() and not tak_tunnel_action_links:
        for pair_interfaces in tak_tunnel_pairs.values():
            sample = pair_interfaces[0]
            if not any(interface.tak_tunnel_fallback_active for interface in pair_interfaces):
                continue
            tak_tunnel_action_links.append(
                DiagnosticsCheckView.ActionLinkView(
                    label=f"{sample.tic_server.name} ↔ {sample.tak_server.name}",
                    url=f"/admin/servers?bucket=active&server_type=tic&selected_server_id={sample.tic_server.id}",
                )
            )
            if len(tak_tunnel_action_links) >= 5:
                break
    if focused_tic_server_id is not None and focused_tak_server_id is not None:
        tak_tunnel_details.insert(0, f"Фокус: пара Tic/Tak {focused_tic_server_id} → {focused_tak_server_id}")
        focused_pair = next(
            (
                pair_interfaces[0]
                for pair_interfaces in tak_tunnel_pairs.values()
                if pair_interfaces
                and pair_interfaces[0].tic_server_id == focused_tic_server_id
                and pair_interfaces[0].tak_server_id == focused_tak_server_id
            ),
            None,
        ) if "tak_tunnel_pairs" in locals() else None
        if focused_pair is not None:
            pair_status = "warning"
            pair_details: list[str] = []
            focused_pair_state = dict(repair_state.get(_tak_tunnel_pair_key(focused_pair.tic_server_id, focused_pair.tak_server_id)) or {})
            pair_message = "Не удалось получить актуальный статус туннеля для выбранной пары."
            try:
                if include_live_checks:
                    focused_response = _run_tic_executor(
                        _build_server_executor_payload(
                            action="verify_tak_tunnel_status",
                            server=focused_pair.tic_server,
                            extra={"tak_server": _server_agent_identity_payload(focused_pair.tak_server)},
                        )
                    )
                    _validate_agent_contract_response(focused_response)
                    focused_status = focused_response.get("tunnel_status") or {}
                    is_active = bool(focused_status.get("is_active"))
                    raw_status = str(focused_status.get("status") or ("active" if is_active else "unknown"))
                    artifact_revision = int(focused_status.get("artifact_revision") or 0)
                    pair_status = "ok" if is_active else "warning"
                    pair_message = (
                        "Туннель выбранной пары активен."
                        if is_active
                        else "Туннель выбранной пары неактивен или требует внимания."
                    )
                    pair_details.append(f"Статус агента: {raw_status}")
                    if artifact_revision > 0:
                        pair_details.append(f"Ревизия артефактов: {artifact_revision}")
                        rotation_event = _latest_tak_tunnel_rotation_event(
                            db,
                            tic_server_id=focused_pair.tic_server_id,
                            tak_server_id=focused_pair.tak_server_id,
                        )
                        if rotation_event is not None:
                            pair_details.append(
                                f"Последняя ротация артефактов: {rotation_event.created_at.isoformat()}"
                            )
                else:
                    raw_status = focused_pair.tak_tunnel_last_status or ("fallback" if focused_pair.tak_tunnel_fallback_active else "unknown")
                    raw_status = focused_pair.tak_tunnel_last_status or ("fallback" if focused_pair.tak_tunnel_fallback_active else "unknown")
                    pair_status = "warning" if focused_pair.tak_tunnel_fallback_active else "ok"
                    pair_message = (
                        "Сейчас пара использует fallback для интерфейсов."
                        if focused_pair.tak_tunnel_fallback_active
                        else "Сейчас пара не использует аварийный fallback."
                    )
                    pair_details.append(f"Snapshot-статус пары: {raw_status}")
                    pair_details.append("Live tunnel-check недоступен сейчас из-за ошибки проверки туннеля.")
                    rotation_event = _latest_tak_tunnel_rotation_event(
                        db,
                        tic_server_id=focused_pair.tic_server_id,
                        tak_server_id=focused_pair.tak_server_id,
                    )
                    if rotation_event is not None:
                        pair_details.append(f"Последняя ротация артефактов: {rotation_event.created_at.isoformat()}")
                if bool(focused_pair_state.get("manual_attention_required")):
                    pair_details.append("Автовосстановление остановлено: требуется ручное вмешательство.")
                elif failure_count:
                    pair_details.append(f"Неудачных попыток подряд: {failure_count}")
                cooldown_until = _tak_tunnel_parse_datetime(focused_pair_state.get("cooldown_until"))
                if cooldown_until is not None and cooldown_until > utc_now():
                    pair_details.append(f"Повторная попытка не раньше: {cooldown_until.isoformat()}")
                last_recovered_at = _tak_tunnel_parse_datetime(focused_pair_state.get("last_recovered_at"))
                if last_recovered_at is not None:
                    pair_details.append(f"Последнее успешное восстановление: {last_recovered_at.isoformat()}")
                interface_names = sorted(
                    interface.name
                    for interface in tak_tunnel_pairs.get((focused_tic_server_id, focused_tak_server_id), [])
                )
                if interface_names:
                    pair_details.append(f"Интерфейсы via_tak: {', '.join(interface_names)}")
                recovered_names = sorted(
                    interface.name
                    for interface in tak_tunnel_pairs.get((focused_tic_server_id, focused_tak_server_id), [])
                    if interface.tak_tunnel_last_status == "recovered" and not interface.tak_tunnel_fallback_active
                )
                if recovered_names:
                    pair_details.append(f"Автовосстановлены: {', '.join(recovered_names)}")
                fallback_names = sorted(
                    interface.name
                    for interface in tak_tunnel_pairs.get((focused_tic_server_id, focused_tak_server_id), [])
                    if interface.tak_tunnel_fallback_active
                )
                if fallback_names:
                    pair_details.append(f"Сейчас в fallback: {', '.join(fallback_names)}")
            except Exception as exc:
                pair_details.append(f"Ошибка проверки: {exc}")
            focused_tak_tunnel = DiagnosticsFocusedTakTunnelView(
                pair_label=f"{focused_pair.tic_server.name} → {focused_pair.tak_server.name}",
                status=pair_status,
                message=pair_message,
                details=pair_details,
                server_url=f"/admin/servers?bucket=active&selected_server_id={focused_pair.tic_server.id}",
                auto_recovered_logs_url=f"/admin/logs?event_type=tak_tunnels.auto_recovered&server_id={focused_pair.tic_server.id}",
                cooldown_logs_url=f"/admin/logs?event_type=tak_tunnels.cooldown&server_id={focused_pair.tic_server.id}",
                manual_attention_logs_url=f"/admin/logs?event_type=tak_tunnels.manual_attention_required&server_id={focused_pair.tic_server.id}",
                manual_repair_logs_url=f"/admin/logs?event_type=tak_tunnels.manual_repaired&server_id={focused_pair.tic_server.id}",
            )
    checks.append(
        DiagnosticsCheckView(
            key="tak_tunnels",
            title="Межсерверные туннели Tic ↔ Tak",
            status=tak_tunnel_status,
            message="Общие туннели Tic/Tak для интерфейсов via_tak работают штатно." if tak_tunnel_status == "ok" else "Есть проблемы с общими туннелями Tic/Tak или их не удалось проверить.",
            details=tak_tunnel_details,
            source_label="Открыть серверы",
            source_url="/admin/servers",
        )
    )
    checks[-1].action_links = tak_tunnel_action_links[:5] if "tak_tunnel_action_links" in locals() else []
    if tak_tunnel_status != "ok":
        problem_nodes.append("Межсерверные туннели Tic ↔ Tak")

    beta_readiness = _build_beta_readiness_summary(
        backup_status=backup_status,
        latest_backup_status=latest_backup_status,
        runtime_status=runtime_status,
        tak_tunnel_status=tak_tunnel_status,
    )
    checks.append(
        DiagnosticsCheckView(
            key="beta_readiness",
            title="Готовность к beta rollout",
            status=beta_readiness.status,
            message=beta_readiness.message,
            details=beta_readiness.details,
            source_label="Открыть серверы",
            source_url="/admin/servers",
            action_links=[
                DiagnosticsCheckView.ActionLinkView(
                    label="Настройки панели",
                    url="/admin?tab=settings&settings_view=basic",
                ),
                DiagnosticsCheckView.ActionLinkView(
                    label="Backup-настройки",
                    url="/admin?tab=settings&settings_view=backups",
                ),
                DiagnosticsCheckView.ActionLinkView(
                    label="Серверы",
                    url="/admin/servers",
                ),
            ],
        )
    )
    if beta_readiness.status != "ok":
        problem_nodes.append("Готовность к beta rollout")

    access_status, access_message, access_details = _run_access_routes_diagnostics()
    checks.append(
        DiagnosticsCheckView(
            key="access_routes",
            title="Доступ и маршруты",
            status=access_status,
            message=access_message,
            details=access_details,
            source_label="Открыть /dashboard",
            source_url="/dashboard",
        )
    )
    if access_status != "ok":
        problem_nodes.append("Доступ и маршруты")

    overall_status = "ok"
    if any(item.status == "error" for item in checks):
        overall_status = "error"
    elif any(item.status == "warning" for item in checks):
        overall_status = "warning"

    summary = {
        "ok": "Самодиагностика не нашла критичных проблем.",
        "warning": "Самодиагностика завершена с предупреждениями.",
        "error": "Самодиагностика нашла проблемные узлы.",
    }[overall_status]
    recommendations = _build_diagnostics_recommendations(checks)
    write_audit_log(
        db,
        event_type="diagnostics.run",
        severity="info" if overall_status == "ok" else ("warning" if overall_status == "warning" else "error"),
        message=f"Diagnostics run completed: {overall_status}.",
        message_ru=f"Самодиагностика завершена: {overall_status}.",
        actor_user_id=actor.id,
        details=json.dumps(
            {
                "overall_status": overall_status,
                "problem_count": len(problem_nodes),
                "recommendation_count": len(recommendations),
                "incident_count": len(recent_incidents),
                "problem_nodes": problem_nodes[:8],
            },
            ensure_ascii=False,
        ),
    )
    run_history = _get_diagnostics_run_history(db)

    return DiagnosticsPageView(
        has_report=True,
        overall_status=overall_status,
        summary=summary,
        problem_nodes=problem_nodes,
        focused_tak_tunnel=focused_tak_tunnel,
        checks=checks,
        recommendations=recommendations,
        recent_incidents=recent_incidents,
        run_history=run_history,
    )


def _run_tic_executor(payload: dict[str, object]) -> dict[str, object]:
    if not settings.peer_agent_command:
        raise ServerOperationUnavailableError("Peer server executor is not configured")
    action = str(payload.get("action") or "")
    timeout_seconds = (
        settings.peer_agent_bootstrap_timeout_seconds
        if action in {"bootstrap_server", "bootstrap_server_status", "bootstrap_server_input"}
        else settings.peer_agent_timeout_seconds
    )

    try:
        completed = subprocess.run(
            settings.peer_agent_command,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=True,
        )
    except OSError as exc:
        raise ServerOperationUnavailableError(f"Peer server executor failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServerOperationUnavailableError("Peer server executor timed out") from exc

    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "Peer server executor failed").strip()
        raise ServerOperationUnavailableError(details)

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}

    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        raise ServerOperationUnavailableError("Peer server executor returned invalid JSON") from None

    if response.get("ok", True) is False:
        raise ServerOperationUnavailableError(str(response.get("error") or "Peer server executor reported failure"))
    _validate_agent_contract_response(response)
    return response


def _run_tic_executor_interactive(payload: dict[str, object]) -> dict[str, object]:
    if not settings.peer_agent_command:
        raise ServerOperationUnavailableError("Peer server executor is not configured")
    action = str(payload.get("action") or "")
    timeout_seconds = (
        settings.peer_agent_bootstrap_timeout_seconds
        if action in {"bootstrap_server", "bootstrap_server_status", "bootstrap_server_input"}
        else settings.peer_agent_timeout_seconds
    )
    try:
        completed = subprocess.run(
            settings.peer_agent_command,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=True,
        )
    except OSError as exc:
        raise ServerOperationUnavailableError(f"Peer server executor failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServerOperationUnavailableError("Peer server executor timed out") from exc
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "Peer server executor failed").strip()
        raise ServerOperationUnavailableError(details)
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {"ok": True}
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        raise ServerOperationUnavailableError("Peer server executor returned invalid JSON") from None
    if response.get("ok", True) is False:
        raise ServerOperationUnavailableError(str(response.get("error") or "Peer server executor reported failure"))
    _validate_agent_contract_response(response)
    return response


def _agent_payload_summary(payload: dict[str, object]) -> tuple[str, int | None, str]:
    action = str(payload.get("action") or "unknown")
    server_id: int | None = None
    server_name = ""
    server_payload = payload.get("server")
    if isinstance(server_payload, dict):
        raw_id = server_payload.get("id")
        server_id = int(raw_id) if isinstance(raw_id, int) else None
        server_name = str(server_payload.get("name") or "")
    tic_payload = payload.get("tic_server")
    if server_id is None and isinstance(tic_payload, dict):
        raw_id = tic_payload.get("id")
        server_id = int(raw_id) if isinstance(raw_id, int) else None
        server_name = str(tic_payload.get("name") or "")
    interface_payload = payload.get("interface")
    peer_payload = payload.get("peer")
    details = {
        "action": action,
        "component": payload.get("component"),
        "server_id": server_id,
        "server_name": server_name,
        "interface_id": interface_payload.get("id") if isinstance(interface_payload, dict) else None,
        "interface_name": interface_payload.get("name") if isinstance(interface_payload, dict) else None,
        "peer_id": peer_payload.get("id") if isinstance(peer_payload, dict) else None,
        "peer_slot": peer_payload.get("slot") if isinstance(peer_payload, dict) else None,
        "contract_version": payload.get("contract_version"),
    }
    return action, server_id, json.dumps(details, ensure_ascii=False)


def _run_agent_executor_logged(
    db: Session,
    payload: dict[str, object],
    *,
    actor_user_id: int | None = None,
    interactive: bool = False,
) -> dict[str, object]:
    action, server_id, details = _agent_payload_summary(payload)
    try:
        response = _run_tic_executor_interactive(payload) if interactive else _run_tic_executor(payload)
    except ServerOperationUnavailableError as exc:
        write_audit_log(
            db,
            event_type="agent.command_failed",
            severity="error",
            message=f"Agent command failed: action={action}; error={exc}",
            message_ru=f"Команда агенту завершилась ошибкой: {action}. Ошибка: {exc}",
            actor_user_id=actor_user_id,
            server_id=server_id,
            details=details,
        )
        raise
    write_audit_log(
        db,
        event_type="agent.command",
        severity="info",
        message=f"Agent command completed: action={action}.",
        message_ru=f"Команда агенту выполнена: {action}.",
        actor_user_id=actor_user_id,
        server_id=server_id,
        details=details,
    )
    return response


def _run_agent_executor_quiet(payload: dict[str, object]) -> dict[str, object]:
    return _run_tic_executor(payload)


def _run_peer_agent_action(
    db: Session,
    action: str,
    interface: Interface,
    peer: Peer,
    *,
    actor_user_id: int | None = None,
) -> dict[str, object]:
    return _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            action,
            interface,
            peer,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=peer_block_filters_enabled(db, peer),
        ),
        actor_user_id=actor_user_id,
    )


def _extract_download_payload(
    response: dict[str, object],
    *,
    default_filename: str,
    default_content_type: str,
) -> dict[str, object]:
    # This is the panel-side contract for the future Tic project:
    # the executor returns file bytes as base64 plus optional filename/content_type.
    content_base64 = response.get("content_base64")
    if not isinstance(content_base64, str) or not content_base64.strip():
        raise ServerOperationUnavailableError("Peer server executor did not return file content")
    try:
        content = base64.b64decode(content_base64)
    except Exception as exc:
        raise ServerOperationUnavailableError("Peer server executor returned invalid file payload") from exc
    filename = response.get("filename") if isinstance(response.get("filename"), str) and response.get("filename") else default_filename
    content_type = (
        response.get("content_type")
        if isinstance(response.get("content_type"), str) and response.get("content_type")
        else default_content_type
    )
    return {
        "filename": filename,
        "content": content,
        "content_type": content_type,
    }


def normalize_login(login: str) -> str:
    return login.strip().lower()


REGISTRATION_LOGIN_RE = re.compile(r"^[A-Za-z0-9]{1,20}$")
REGISTRATION_PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{4,255}$")
REGISTRATION_DISPLAY_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 ]{1,120}$")
REGISTRATION_CONTACT_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 @?_\\-\\.,]{1,255}$")
REGISTRATION_USED_LINK_RETENTION_DAYS = 7
INITIAL_ADMIN_TOKEN_SETTING_KEY = "initial_admin_setup_token"
INITIAL_ADMIN_TOKEN_CREATED_AT_SETTING_KEY = "initial_admin_setup_token_created_at"
INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY = "initial_admin_setup_token_announced_at"


def _user_region_label(value: UserRegion | str | None) -> str | None:
    if value in {UserRegion.EUROPE, "europe"}:
        return "Европа"
    if value in {UserRegion.EAST, "east"}:
        return "Восток"
    if value in {UserRegion.UNKNOWN, "unknown"}:
        return "Не знаю/затрудняюсь ответить"
    return None


def _registration_link_url(token_id: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/registration/{quote(token_id)}"


def _initial_admin_setup_url(token_id: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/bootstrap-admin/{quote(token_id)}"


def has_admin_user(db: Session) -> bool:
    return db.execute(select(User.id).where(User.role == UserRole.ADMIN).limit(1)).scalar_one_or_none() is not None


def _validate_first_admin_payload(db: Session, payload: FirstAdminRegistrationCreate) -> tuple[str, str]:
    login = normalize_login(payload.login)
    password = str(payload.password or "").strip()
    if not REGISTRATION_LOGIN_RE.fullmatch(login):
        raise PermissionDeniedError("Login must be 1-20 chars and contain only English letters and digits")
    if db.execute(select(User).where(User.login == login)).scalar_one_or_none() is not None:
        raise PermissionDeniedError("Login already exists")
    if not REGISTRATION_PASSWORD_RE.fullmatch(password):
        raise PermissionDeniedError("Password must be at least 4 chars and contain only English letters and digits")
    return login, password


def _validate_public_registration_payload(db: Session, payload: PublicRegistrationCreate) -> tuple[str, str, str, str, UserRegion]:
    login = normalize_login(payload.login)
    password = str(payload.password or "").strip()
    display_name = str(payload.display_name or "").strip()
    communication_channel = str(payload.communication_channel or "").strip()
    region = UserRegion(payload.region)

    if not REGISTRATION_LOGIN_RE.fullmatch(login):
        raise PermissionDeniedError("Login must be 1-20 chars and contain only English letters and digits")
    if db.execute(select(User).where(User.login == login)).scalar_one_or_none() is not None:
        raise PermissionDeniedError("Login already exists")
    if not REGISTRATION_PASSWORD_RE.fullmatch(password):
        raise PermissionDeniedError("Password must be at least 4 chars and contain only English letters and digits")
    if not REGISTRATION_DISPLAY_NAME_RE.fullmatch(display_name):
        raise PermissionDeniedError("Display name may contain only Cyrillic, Latin letters, digits and spaces")
    if not REGISTRATION_CONTACT_RE.fullmatch(communication_channel):
        raise PermissionDeniedError("Contact may contain only letters, digits, spaces and @ ? _ - . ,")

    return login, password, display_name, communication_channel, region


def authenticate_user(db: Session, login: str, password: str) -> User | None:
    stmt: Select[tuple[User]] = select(User).where(User.login == normalize_login(login))
    user = db.execute(stmt).scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def require_admin(actor: User) -> None:
    if actor.role != UserRole.ADMIN:
        raise PermissionDeniedError("Admin access is required")


def ensure_default_settings(db: Session) -> None:
    existing_keys = set(db.execute(select(AppSetting.key)).scalars().all())
    deprecated_rows = db.execute(select(AppSetting).where(AppSetting.key.in_(DEPRECATED_SETTING_KEYS))).scalars().all()
    for row in deprecated_rows:
        db.delete(row)
    existing_keys -= DEPRECATED_SETTING_KEYS
    missing = [key for key in DEFAULT_BASIC_SETTINGS if key not in existing_keys]
    for key in missing:
        db.add(AppSetting(key=key, value=DEFAULT_BASIC_SETTINGS[key]))
    repo_default = settings.nelomai_git_repo.strip()
    repo_row = db.get(AppSetting, "nelomai_git_repo")
    if repo_default and repo_row is not None and not repo_row.value.strip():
        repo_row.value = repo_default
        db.add(repo_row)
    purge_old_audit_logs(db)
    if missing or deprecated_rows or (repo_default and repo_row is not None and repo_row.value == repo_default):
        db.flush()


def ensure_initial_admin_setup_token(db: Session) -> str | None:
    ensure_default_settings(db)
    if has_admin_user(db):
        for key in (
            INITIAL_ADMIN_TOKEN_SETTING_KEY,
            INITIAL_ADMIN_TOKEN_CREATED_AT_SETTING_KEY,
            INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY,
        ):
            row = db.get(AppSetting, key)
            if row is not None:
                db.delete(row)
        db.flush()
        return None
    existing = db.get(AppSetting, INITIAL_ADMIN_TOKEN_SETTING_KEY)
    if existing is not None and existing.value.strip():
        return existing.value.strip()
    token = secrets.token_urlsafe(24)
    now_iso = utc_now().isoformat()
    db.merge(AppSetting(key=INITIAL_ADMIN_TOKEN_SETTING_KEY, value=token))
    db.merge(AppSetting(key=INITIAL_ADMIN_TOKEN_CREATED_AT_SETTING_KEY, value=now_iso))
    db.flush()
    return token


def get_initial_admin_setup_url(db: Session, base_url: str) -> str | None:
    token = ensure_initial_admin_setup_token(db)
    if not token:
        return None
    return _initial_admin_setup_url(token, base_url)


def get_initial_admin_setup_token(db: Session, token_id: str) -> str:
    if has_admin_user(db):
        raise EntityNotFoundError("Initial admin setup link is invalid or already used")
    token_row = db.get(AppSetting, INITIAL_ADMIN_TOKEN_SETTING_KEY)
    if token_row is None or token_row.value.strip() != token_id.strip():
        raise EntityNotFoundError("Initial admin setup link is invalid or already used")
    return token_row.value.strip()


def complete_initial_admin_setup(db: Session, token_id: str, payload: FirstAdminRegistrationCreate) -> User:
    get_initial_admin_setup_token(db, token_id)
    login, password = _validate_first_admin_payload(db, payload)
    admin = User(
        login=login,
        password_hash=get_password_hash(password),
        display_name=login,
        role=UserRole.ADMIN,
        expires_at=None,
        is_active=True,
    )
    db.add(admin)
    for key in (
        INITIAL_ADMIN_TOKEN_SETTING_KEY,
        INITIAL_ADMIN_TOKEN_CREATED_AT_SETTING_KEY,
        INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY,
    ):
        row = db.get(AppSetting, key)
        if row is not None:
            db.delete(row)
    db.flush()
    write_audit_log(
        db,
        event_type="admin_bootstrap.completed",
        severity="info",
        message=f"Initial admin account created: {login}",
        message_ru=f"Создан первый администратор панели: {login}.",
        actor_user_id=admin.id,
        details="initial-admin-bootstrap",
        commit=False,
    )
    db.commit()
    db.refresh(admin)
    return admin


def announce_initial_admin_setup_if_needed(db: Session, base_url: str) -> str | None:
    token = ensure_initial_admin_setup_token(db)
    if not token:
        return None
    announced = db.get(AppSetting, INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY)
    if announced is None:
        db.merge(AppSetting(key=INITIAL_ADMIN_TOKEN_ANNOUNCED_AT_SETTING_KEY, value=utc_now().isoformat()))
        db.commit()
        return _initial_admin_setup_url(token, base_url)
    return None


def ensure_seed_data(db: Session) -> None:
    existing_admin = db.execute(select(User).limit(1)).scalar_one_or_none()
    if existing_admin:
        ensure_default_settings(db)
        db.commit()
        return

    tic_server = Server(
        name="1a (Tic Server)",
        server_type=ServerType.TIC,
        host="tic.local",
        ssh_port=22,
        ssh_login="root",
        ssh_password="",
        last_seen_at=utc_now(),
    )
    tak_server = Server(
        name="2a (Tak Server)",
        server_type=ServerType.TAK,
        host="tak.local",
        ssh_port=22,
        ssh_login="root",
        ssh_password="",
        last_seen_at=utc_now(),
    )

    admin = User(
        login="admin",
        password_hash=get_password_hash("admin"),
        display_name="Админ",
        role=UserRole.ADMIN,
        expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    demo_user = User(
        login="demo",
        password_hash=get_password_hash("demo"),
        display_name="Alex3k",
        role=UserRole.USER,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )
    db.add_all([tic_server, tak_server, admin, demo_user])
    db.flush()

    demo_interface = Interface(
        name="VPN101",
        user_id=demo_user.id,
        tic_server_id=tic_server.id,
        tak_server_id=tak_server.id,
        route_mode=RouteMode.VIA_TAK,
        listen_port=10001,
        address_v4="10.8.0.1/24",
        address_v6="fd00:8::1/64",
        peer_limit=5,
    )
    db.add(demo_interface)
    db.flush()

    peers = [
        Peer(slot=1, interface_id=demo_interface.id, comment="Основной ноутбук", traffic_7d_mb=22480, traffic_30d_mb=91234),
        Peer(slot=2, interface_id=demo_interface.id, comment="Телефон", is_enabled=False, traffic_7d_mb=5120, traffic_30d_mb=18745),
        Peer(slot=3, interface_id=demo_interface.id, comment="Планшет", traffic_7d_mb=3500, traffic_30d_mb=9340),
        Peer(slot=4, interface_id=demo_interface.id, comment="Резерв", traffic_7d_mb=0, traffic_30d_mb=640),
        Peer(slot=5, interface_id=demo_interface.id, comment="Тестовый", traffic_7d_mb=1100, traffic_30d_mb=3200),
    ]
    db.add_all(peers)

    db.add(UserResource(user_id=admin.id))
    db.add(UserContactLink(user_id=admin.id, value=None))
    db.add(
        UserResource(
            user_id=demo_user.id,
            yandex_disk_url="https://disk.yandex.ru/example",
            amnezia_vpn_finland="amnezia://finland/demo-config",
            outline_japan="ss://outline-japan-demo",
        )
    )
    db.add(UserContactLink(user_id=demo_user.id, value="https://t.me/nelomai_support"))

    filters = [
        ResourceFilter(name="RU ASN", filter_type=FilterType.IP, scope=FilterScope.GLOBAL, value="77.88.8.8", description="Глобальный IP-фильтр"),
        ResourceFilter(name="Drive links", filter_type=FilterType.LINK, scope=FilterScope.USER, value="disk.yandex.ru", description="Личное правило", user_id=demo_user.id),
    ]
    db.add_all(filters)
    ensure_default_settings(db)
    db.commit()


def purge_expired_peers(db: Session, peer_ids: set[int] | None = None) -> int:
    query = (
        select(Peer)
        .options(joinedload(Peer.interface).joinedload(Interface.tic_server), joinedload(Peer.interface).joinedload(Interface.user))
        .where(Peer.expires_at.is_not(None))
    )
    if peer_ids is not None:
        if not peer_ids:
            return 0
        query = query.where(Peer.id.in_(peer_ids))
    expired_peers = [
        peer
        for peer in db.execute(query).unique().scalars().all()
        if normalize_utc_datetime(peer.expires_at) is not None and normalize_utc_datetime(peer.expires_at) <= utc_now()
    ]
    if not expired_peers:
        return 0
    deleted_count = 0
    for peer in expired_peers:
        peer_id = peer.id
        interface_name = peer.interface.name
        peer_slot = peer.slot
        target_user_id = peer.interface.user_id
        server_id = peer.interface.tic_server_id
        agent_peer = peer
        peer = SimpleNamespace(interface=SimpleNamespace(name=interface_name), slot=peer_slot)
        try:
            ensure_interface_is_valid(agent_peer.interface)
            _run_peer_agent_action(db, "delete_peer", agent_peer.interface, agent_peer)
        except (PermissionDeniedError, ServerOperationUnavailableError) as exc:
            write_audit_log(
                db,
                event_type="peers.expire_delete_failed",
                severity="error",
                message=f"Expired peer could not be deleted from Tic server: peer={peer_id}; error={exc}",
                message_ru=f"Не удалось удалить истёкший пир с Tic сервера: {peer.interface.name}, пир {peer.slot}. Ошибка: {exc}",
                target_user_id=target_user_id,
                server_id=server_id,
            )
            continue
        db.delete(agent_peer)
        db.flush()
        deleted_count += 1
        write_audit_log(
            db,
            event_type="peers.expire_delete",
            severity="warning",
            message=f"Expired peer deleted from Tic server and panel: {interface_name} peer {peer_slot}.",
            message_ru=f"Истёкший пир удалён с Tic сервера и из панели: {interface_name}, пир {peer_slot}.",
            target_user_id=target_user_id,
            server_id=server_id,
            commit=False,
        )
    db.commit()
    return deleted_count


def get_dashboard_data(db: Session, user: User, preview_mode: bool = False) -> UserDashboardView:
    purge_expired_peers(db)
    hydrated_user = db.execute(
        select(User)
        .options(
            joinedload(User.interfaces).joinedload(Interface.peers),
            joinedload(User.interfaces).joinedload(Interface.tic_server),
            joinedload(User.interfaces).joinedload(Interface.tak_server),
            joinedload(User.resources),
            joinedload(User.filters),
        )
        .where(User.id == user.id)
    ).unique().scalar_one()

    active_filter_kinds: list[FilterKind] = []
    if exclusion_filters_enabled(db):
        active_filter_kinds.append(FilterKind.EXCLUSION)
    if block_filters_enabled(db):
        active_filter_kinds.append(FilterKind.BLOCK)

    if active_filter_kinds:
        global_filters = db.execute(
            select(ResourceFilter)
            .where(ResourceFilter.scope == FilterScope.GLOBAL, ResourceFilter.kind.in_(active_filter_kinds))
            .order_by(ResourceFilter.id.asc())
        ).scalars().all()
        user_filters = [item for item in hydrated_user.filters if item.kind in active_filter_kinds]
    else:
        global_filters = []
        user_filters = []

    visible_interfaces = [interface for interface in hydrated_user.interfaces if not interface.is_pending_owner]
    for interface in visible_interfaces:
        matching_tak = _get_matching_tak_server(db, interface.tic_server)
        interface.available_tak_options = [matching_tak] if matching_tak is not None else []
    return serialize_dashboard(
        hydrated_user,
        global_filters,
        preview_mode,
        interfaces=visible_interfaces,
        user_filters=user_filters,
    )


def get_user_by_id(db: Session, user_id: int) -> User:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise EntityNotFoundError("User not found")
    return user


def resolve_target_user(db: Session, actor: User, target_user_id: int | None = None) -> User:
    if target_user_id is None or target_user_id == actor.id:
        return get_user_by_id(db, actor.id)
    if actor.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only admins can access another user")
    return get_user_by_id(db, target_user_id)


def ensure_can_write_user_resources(actor: User, target_user: User, preview_mode: bool) -> None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    if actor.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only admins can edit user resources")


def ensure_can_create_filter(actor: User, target_user: User, payload: FilterCreate, preview_mode: bool) -> None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    if payload.scope == FilterScope.GLOBAL:
        if actor.role != UserRole.ADMIN:
            raise PermissionDeniedError("Only admins can manage global filters")
        return
    if actor.role == UserRole.ADMIN:
        return
    if actor.id != target_user.id:
        raise PermissionDeniedError("Users can edit only their own filters")


def ensure_can_edit_filter(actor: User, resource_filter: ResourceFilter, preview_mode: bool) -> None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    if resource_filter.scope == FilterScope.GLOBAL:
        if actor.role != UserRole.ADMIN:
            raise PermissionDeniedError("Only admins can edit global filters")
        return
    if actor.role == UserRole.ADMIN:
        return
    if resource_filter.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own filters")


def get_or_create_user_resources(db: Session, user: User) -> UserResource:
    if user.resources is not None:
        return user.resources
    resource = UserResource(user_id=user.id)
    db.add(resource)
    db.commit()
    db.refresh(resource)
    return resource


def get_or_create_user_contact_link(db: Session, user: User) -> UserContactLink:
    if user.contact_link_record is not None:
        return user.contact_link_record
    record = UserContactLink(user_id=user.id)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def ensure_users_have_resources(db: Session) -> None:
    users = db.execute(select(User).options(joinedload(User.resources), joinedload(User.contact_link_record))).unique().scalars().all()
    changed = False
    for user in users:
        if user.resources is None:
            db.add(UserResource(user_id=user.id))
            changed = True
        if user.contact_link_record is None:
            db.add(UserContactLink(user_id=user.id))
            changed = True
    if changed:
        db.commit()


def serialize_user_resources(resource: UserResource | None, user_id: int) -> list[ResourceItemView]:
    return serialize_resources(resource, user_id)


def get_user_resources_view(db: Session, target_user: User) -> list[ResourceItemView]:
    db.refresh(target_user)
    return serialize_user_resources(target_user.resources, target_user.id)


def update_user_resources(db: Session, target_user: User, payload: UserResourceUpdate) -> list[ResourceItemView]:
    resource = get_or_create_user_resources(db, target_user)
    resource.yandex_disk_url = payload.yandex_disk_url
    resource.amnezia_vpn_finland = payload.amnezia_vpn_finland
    resource.outline_japan = payload.outline_japan
    db.add(resource)
    db.commit()
    db.refresh(target_user)
    return get_user_resources_view(db, target_user)


def delete_user_resources(db: Session, target_user: User) -> None:
    if target_user.resources is not None:
        db.delete(target_user.resources)
        db.commit()
        db.refresh(target_user)


def filters_enabled_for_kind(db: Session, kind: FilterKind) -> bool:
    if kind == FilterKind.BLOCK:
        return block_filters_enabled(db)
    return exclusion_filters_enabled(db)


def ensure_filters_enabled_for_kind(db: Session, kind: FilterKind) -> None:
    if not filters_enabled_for_kind(db, kind):
        raise PermissionDeniedError("Block filters are disabled" if kind == FilterKind.BLOCK else "Exclusion filters are disabled")


def peer_block_filters_enabled(db: Session, peer: Peer) -> bool:
    return block_filters_enabled(db) and peer.block_filters_enabled


def format_peer_label(peer: Peer | None) -> str | None:
    if peer is None:
        return None
    interface_name = peer.interface.name if peer.interface is not None else "Interface"
    return f"{interface_name} / Peer {peer.slot}"


def resolve_filter_peer(db: Session, target_user: User, payload: FilterCreate) -> Peer | None:
    if payload.kind != FilterKind.BLOCK or payload.scope != FilterScope.USER:
        return None
    if payload.peer_id is None:
        raise InvalidInputError("Block filter must target a peer")
    peer = db.execute(
        select(Peer)
        .join(Peer.interface)
        .options(joinedload(Peer.interface))
        .where(Peer.id == payload.peer_id, Interface.user_id == target_user.id)
    ).scalar_one_or_none()
    if peer is None:
        raise InvalidInputError("Block filter peer is not available for this user")
    return peer


def get_filters_view(db: Session, target_user: User, kind: FilterKind | None = None) -> list[FilterView]:
    requested_kinds = [kind] if kind is not None else [FilterKind.EXCLUSION, FilterKind.BLOCK]
    active_kinds = [item for item in requested_kinds if filters_enabled_for_kind(db, item)]
    if not active_kinds:
        return []
    global_filters = db.execute(
        select(ResourceFilter)
        .options(joinedload(ResourceFilter.peer).joinedload(Peer.interface))
        .where(ResourceFilter.scope == FilterScope.GLOBAL, ResourceFilter.kind.in_(active_kinds))
        .order_by(ResourceFilter.id.asc())
    ).scalars().all()
    user_filters = db.execute(
        select(ResourceFilter)
        .options(joinedload(ResourceFilter.peer).joinedload(Peer.interface))
        .outerjoin(ResourceFilter.peer)
        .outerjoin(Peer.interface)
        .where(
            ResourceFilter.scope == FilterScope.USER,
            ResourceFilter.user_id == target_user.id,
            ResourceFilter.kind.in_(active_kinds),
            or_(ResourceFilter.kind != FilterKind.BLOCK, Interface.user_id == target_user.id),
        )
        .order_by(ResourceFilter.id.asc())
    ).scalars().all()
    return [
        FilterView(
            id=item.id,
            name=item.name,
            kind=item.kind,
            peer_id=item.peer_id,
            peer_label=format_peer_label(item.peer),
            filter_type=item.filter_type,
            scope=item.scope,
            value=item.value,
            description=item.description,
            is_active=item.is_active,
        )
        for item in [*global_filters, *user_filters]
    ]


def get_admin_filters_view(db: Session, scope_filter: str = "all", kind: FilterKind = FilterKind.EXCLUSION) -> list[FilterView]:
    rows = db.execute(
        select(ResourceFilter)
        .options(joinedload(ResourceFilter.user))
        .options(joinedload(ResourceFilter.peer).joinedload(Peer.interface))
        .where(ResourceFilter.kind == kind)
        .order_by(ResourceFilter.scope.asc(), ResourceFilter.id.asc())
    ).scalars().all()
    grouped_user_filters: dict[tuple[str, str, str], list[ResourceFilter]] = {}
    filter_views: list[FilterView] = []

    for item in rows:
        if item.scope == FilterScope.GLOBAL:
            if scope_filter in {"all", "global"}:
                filter_views.append(
                    FilterView(
                        id=item.id,
                        name=item.name,
                        kind=item.kind,
                        peer_id=item.peer_id,
                        peer_label=format_peer_label(item.peer),
                        filter_type=item.filter_type,
                        scope=item.scope,
                        value=item.value,
                        description=item.description,
                        is_active=item.is_active,
                        owner_users=[],
                        delete_ids=[item.id],
                    )
                )
            continue

        group_key = (item.name.strip().lower(), item.filter_type.value, item.value.strip().lower(), str(item.peer_id or ""))
        grouped_user_filters.setdefault(group_key, []).append(item)

    if scope_filter in {"all", "user"}:
        for group in grouped_user_filters.values():
            first = group[0]
            owners = [
                {"id": item.user.id, "display_name": item.user.display_name}
                for item in group
                if item.user is not None
            ]
            owners.sort(key=lambda entry: str(entry["display_name"]).lower())
            filter_views.append(
                FilterView(
                    id=first.id,
                    name=first.name,
                    kind=first.kind,
                    peer_id=first.peer_id,
                    peer_label=format_peer_label(first.peer),
                    filter_type=first.filter_type,
                    scope=first.scope,
                    value=first.value,
                    description=first.description,
                    is_active=any(item.is_active for item in group),
                    owner_users=owners,
                    delete_ids=[item.id for item in group],
                )
            )

    return filter_views


def validate_filter_value(filter_type: FilterType, value: str) -> None:
    if filter_type != FilterType.IP:
        return
    parts = value.strip().split(".")
    if len(parts) != 4:
        raise InvalidInputError("IP filter must be IPv4 address x.x.x.x with values from 0 to 255")
    for part in parts:
        if not part.isdigit():
            raise InvalidInputError("IP filter must be IPv4 address x.x.x.x with values from 0 to 255")
        number = int(part)
        if number < 0 or number > 255:
            raise InvalidInputError("IP filter must be IPv4 address x.x.x.x with values from 0 to 255")


def create_filter(
    db: Session,
    actor: User,
    target_user: User,
    payload: FilterCreate,
    preview_mode: bool,
) -> FilterView:
    if payload.scope == FilterScope.USER:
        ensure_filters_enabled_for_kind(db, payload.kind)
    ensure_can_create_filter(actor, target_user, payload, preview_mode=preview_mode)
    target_peer = resolve_filter_peer(db, target_user, payload)
    filter_value = payload.value.strip()
    validate_filter_value(payload.filter_type, filter_value)
    resource_filter = ResourceFilter(
        user_id=None if payload.scope == FilterScope.GLOBAL else target_user.id,
        peer_id=target_peer.id if target_peer is not None else None,
        name=payload.name.strip(),
        kind=payload.kind,
        filter_type=payload.filter_type,
        scope=payload.scope,
        value=filter_value,
        description=payload.description.strip() if payload.description else None,
        is_active=payload.is_active,
    )
    db.add(resource_filter)
    db.commit()
    db.refresh(resource_filter)
    return FilterView(
        id=resource_filter.id,
        name=resource_filter.name,
        kind=resource_filter.kind,
        peer_id=resource_filter.peer_id,
        peer_label=format_peer_label(target_peer),
        filter_type=resource_filter.filter_type,
        scope=resource_filter.scope,
        value=resource_filter.value,
        description=resource_filter.description,
        is_active=resource_filter.is_active,
    )


def create_global_filter(db: Session, actor: User, payload: FilterCreate) -> FilterView:
    require_admin(actor)
    if payload.scope != FilterScope.GLOBAL:
        raise PermissionDeniedError("Admin settings can create only global filters")
    return create_filter(db, actor, actor, payload, preview_mode=False)


def delete_filters_bulk(db: Session, actor: User, payload: AdminFilterDeleteRequest) -> None:
    require_admin(actor)
    filters = db.execute(select(ResourceFilter).where(ResourceFilter.id.in_(payload.ids))).scalars().all()
    for resource_filter in filters:
        db.delete(resource_filter)
    db.commit()


def get_filter_by_id(db: Session, filter_id: int) -> ResourceFilter:
    resource_filter = db.execute(
        select(ResourceFilter)
        .options(joinedload(ResourceFilter.peer).joinedload(Peer.interface))
        .where(ResourceFilter.id == filter_id)
    ).scalar_one_or_none()
    if resource_filter is None:
        raise EntityNotFoundError("Filter not found")
    return resource_filter


def update_filter(db: Session, resource_filter: ResourceFilter, payload: FilterUpdate) -> FilterView:
    if resource_filter.scope == FilterScope.USER:
        ensure_filters_enabled_for_kind(db, resource_filter.kind)
    next_filter_type = payload.filter_type if payload.filter_type is not None else resource_filter.filter_type
    next_value = payload.value.strip() if payload.value is not None else resource_filter.value
    validate_filter_value(next_filter_type, next_value)
    if payload.name is not None:
        resource_filter.name = payload.name.strip()
    if payload.filter_type is not None:
        resource_filter.filter_type = payload.filter_type
    if payload.value is not None:
        resource_filter.value = next_value
    if payload.description is not None:
        resource_filter.description = payload.description.strip() or None
    if payload.is_active is not None:
        resource_filter.is_active = payload.is_active
    db.add(resource_filter)
    db.commit()
    db.refresh(resource_filter)
    return FilterView(
        id=resource_filter.id,
        name=resource_filter.name,
        kind=resource_filter.kind,
        peer_id=resource_filter.peer_id,
        peer_label=format_peer_label(resource_filter.peer),
        filter_type=resource_filter.filter_type,
        scope=resource_filter.scope,
        value=resource_filter.value,
        description=resource_filter.description,
        is_active=resource_filter.is_active,
    )


def delete_filter(db: Session, resource_filter: ResourceFilter) -> None:
    if resource_filter.scope == FilterScope.USER:
        ensure_filters_enabled_for_kind(db, resource_filter.kind)
    db.delete(resource_filter)
    db.commit()


def get_basic_settings(db: Session) -> dict[str, str]:
    ensure_default_settings(db)
    rows = db.execute(select(AppSetting).where(AppSetting.key.in_(DEFAULT_BASIC_SETTINGS.keys()))).scalars().all()
    values = {row.key: row.value for row in rows}
    return {
        "nelomai_git_repo": values.get("nelomai_git_repo", DEFAULT_BASIC_SETTINGS["nelomai_git_repo"]),
        "dns_server": values.get("dns_server", DEFAULT_BASIC_SETTINGS["dns_server"]),
        "mtu": values.get("mtu", DEFAULT_BASIC_SETTINGS["mtu"]),
        "keepalive": values.get("keepalive", DEFAULT_BASIC_SETTINGS["keepalive"]),
        "exclusion_filters_enabled": values.get(
            "exclusion_filters_enabled",
            DEFAULT_BASIC_SETTINGS["exclusion_filters_enabled"],
        ),
        "block_filters_enabled": values.get(
            "block_filters_enabled",
            DEFAULT_BASIC_SETTINGS["block_filters_enabled"],
        ),
        "admin_telegram_url": values.get("admin_telegram_url", DEFAULT_BASIC_SETTINGS["admin_telegram_url"]),
        "admin_vk_url": values.get("admin_vk_url", DEFAULT_BASIC_SETTINGS["admin_vk_url"]),
        "admin_email_url": values.get("admin_email_url", DEFAULT_BASIC_SETTINGS["admin_email_url"]),
        "admin_group_url": values.get("admin_group_url", DEFAULT_BASIC_SETTINGS["admin_group_url"]),
        "audit_log_retention_days": values.get(
            "audit_log_retention_days",
            DEFAULT_BASIC_SETTINGS["audit_log_retention_days"],
        ),
        "backups_enabled": values.get("backups_enabled", DEFAULT_BASIC_SETTINGS["backups_enabled"]),
        "backup_frequency": values.get("backup_frequency", DEFAULT_BASIC_SETTINGS["backup_frequency"]),
        "backup_time": values.get("backup_time", DEFAULT_BASIC_SETTINGS["backup_time"]),
        "backup_retention_days": values.get(
            "backup_retention_days",
            DEFAULT_BASIC_SETTINGS["backup_retention_days"],
        ),
        "backup_storage_path": values.get("backup_storage_path", DEFAULT_BASIC_SETTINGS["backup_storage_path"]),
        "backup_last_run_at": values.get("backup_last_run_at", DEFAULT_BASIC_SETTINGS["backup_last_run_at"]),
        "server_backup_retention_days": values.get(
            "server_backup_retention_days",
            DEFAULT_BASIC_SETTINGS["server_backup_retention_days"],
        ),
        "server_backup_size_limit_mb": values.get(
            "server_backup_size_limit_mb",
            DEFAULT_BASIC_SETTINGS["server_backup_size_limit_mb"],
        ),
        "server_backup_monthly_retention_days": values.get(
            "server_backup_monthly_retention_days",
            DEFAULT_BASIC_SETTINGS["server_backup_monthly_retention_days"],
        ),
        "server_backup_monthly_size_limit_mb": values.get(
            "server_backup_monthly_size_limit_mb",
            DEFAULT_BASIC_SETTINGS["server_backup_monthly_size_limit_mb"],
        ),
        "backup_remote_storage_server_id": values.get(
            "backup_remote_storage_server_id",
            DEFAULT_BASIC_SETTINGS["backup_remote_storage_server_id"],
        ),
    }


def exclusion_filters_enabled(db: Session) -> bool:
    return get_basic_settings(db).get("exclusion_filters_enabled", "1") == "1"


def interface_exclusion_filters_enabled(db: Session, interface: Interface) -> bool:
    return exclusion_filters_enabled(db) and interface.exclusion_filters_enabled


def block_filters_enabled(db: Session) -> bool:
    return get_basic_settings(db).get("block_filters_enabled", "1") == "1"


def ensure_exclusion_filters_enabled(db: Session) -> None:
    if not exclusion_filters_enabled(db):
        raise PermissionDeniedError("Exclusion filters are disabled")


def update_basic_settings(db: Session, payload: BasicSettingsUpdate) -> dict[str, str]:
    ensure_default_settings(db)
    values = {
        "dns_server": payload.dns_server.strip(),
        "mtu": str(payload.mtu),
        "keepalive": str(payload.keepalive),
        "exclusion_filters_enabled": "1" if payload.exclusion_filters_enabled else "0",
        "block_filters_enabled": "1" if payload.block_filters_enabled else "0",
        "admin_telegram_url": payload.admin_telegram_url.strip(),
        "admin_vk_url": payload.admin_vk_url.strip(),
        "admin_email_url": payload.admin_email_url.strip(),
        "admin_group_url": payload.admin_group_url.strip(),
    }
    rows = db.execute(select(AppSetting).where(AppSetting.key.in_(values.keys()))).scalars().all()
    by_key = {row.key: row for row in rows}
    for key, value in values.items():
        row = by_key.get(key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
        db.add(row)
    db.commit()
    return get_basic_settings(db)


def update_git_settings(db: Session, actor: User, payload: UpdateSettingsUpdate) -> dict[str, str]:
    require_admin(actor)
    ensure_default_settings(db)
    values = {
        "nelomai_git_repo": payload.nelomai_git_repo.strip(),
    }
    rows = db.execute(select(AppSetting).where(AppSetting.key.in_(values.keys()))).scalars().all()
    by_key = {row.key: row for row in rows}
    for key, value in values.items():
        row = by_key.get(key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
        db.add(row)
    db.commit()
    return get_basic_settings(db)


def update_audit_log_settings(db: Session, actor: User, retention_days: int) -> int:
    require_admin(actor)
    if retention_days < 1 or retention_days > 365:
        raise InvalidInputError("Audit log retention must be between 1 and 365 days")
    ensure_default_settings(db)
    row = db.get(AppSetting, "audit_log_retention_days") or AppSetting(key="audit_log_retention_days", value="30")
    row.value = str(retention_days)
    db.add(row)
    db.commit()
    return retention_days


def get_backup_settings(db: Session, actor: User) -> BackupSettingsView:
    require_admin(actor)
    values = get_basic_settings(db)
    last_run_at = _parse_backup_last_run(values.get("backup_last_run_at"))
    remote_storage_server_id = _backup_int(values.get("backup_remote_storage_server_id"))
    return BackupSettingsView(
        backups_enabled=values.get("backups_enabled", "1") == "1",
        backup_frequency=values.get("backup_frequency", "daily"),
        backup_time=values.get("backup_time", "03:00"),
        backup_retention_days=int(values.get("backup_retention_days", "30")),
        backup_storage_path=values.get("backup_storage_path", DEFAULT_BASIC_SETTINGS["backup_storage_path"]),
        backup_last_run_at=last_run_at,
        backup_next_run_at=_next_scheduled_backup_at(values),
        server_backup_retention_days=int(values.get("server_backup_retention_days", "90")),
        server_backup_size_limit_mb=int(values.get("server_backup_size_limit_mb", "5120")),
        server_backup_monthly_retention_days=int(values.get("server_backup_monthly_retention_days", "365")),
        server_backup_monthly_size_limit_mb=int(values.get("server_backup_monthly_size_limit_mb", "3072")),
        backup_remote_storage_server_id=remote_storage_server_id,
    )


def update_backup_settings(db: Session, actor: User, payload: BackupSettingsUpdate) -> BackupSettingsView:
    require_admin(actor)
    ensure_default_settings(db)
    if payload.backup_remote_storage_server_id is not None:
        storage_server = db.execute(
            select(Server).where(
                Server.id == payload.backup_remote_storage_server_id,
                Server.server_type == ServerType.STORAGE,
            )
        ).scalar_one_or_none()
        if storage_server is None:
            raise InvalidInputError("Remote storage server not found")
    values = {
        "backups_enabled": "1" if payload.backups_enabled else "0",
        "backup_frequency": payload.backup_frequency,
        "backup_time": payload.backup_time,
        "backup_retention_days": str(payload.backup_retention_days),
        "backup_storage_path": payload.backup_storage_path.strip(),
        "server_backup_retention_days": str(payload.server_backup_retention_days),
        "server_backup_size_limit_mb": str(payload.server_backup_size_limit_mb),
        "server_backup_monthly_retention_days": str(payload.server_backup_monthly_retention_days),
        "server_backup_monthly_size_limit_mb": str(payload.server_backup_monthly_size_limit_mb),
        "backup_remote_storage_server_id": str(payload.backup_remote_storage_server_id or ""),
    }
    rows = db.execute(select(AppSetting).where(AppSetting.key.in_(values.keys()))).scalars().all()
    by_key = {row.key: row for row in rows}
    for key, value in values.items():
        row = by_key.get(key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
        db.add(row)
    db.commit()
    return get_backup_settings(db, actor)


def _backup_schedule_interval_days(frequency: str) -> int:
    if frequency == "every_3_days":
        return 3
    if frequency == "weekly":
        return 7
    return 1


def _parse_backup_last_run(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _backup_due_at(now: datetime, backup_time: str) -> datetime:
    local_now = now.astimezone(MOSCOW_TZ)
    hour_raw, minute_raw = backup_time.split(":", 1)
    return local_now.replace(hour=int(hour_raw), minute=int(minute_raw), second=0, microsecond=0)


def _next_scheduled_backup_at(values: dict[str, str], now: datetime | None = None) -> datetime | None:
    if values.get("backups_enabled", "1") != "1":
        return None
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(MOSCOW_TZ)
    due_today = _backup_due_at(now, values.get("backup_time", "03:00"))
    last_run_at = _parse_backup_last_run(values.get("backup_last_run_at"))
    if last_run_at is not None:
        last_run_at = last_run_at.astimezone(MOSCOW_TZ)
    interval_days = _backup_schedule_interval_days(values.get("backup_frequency", "daily"))
    if last_run_at is None:
        return due_today if now < due_today else due_today + timedelta(days=1)
    next_from_last = last_run_at + timedelta(days=interval_days)
    due_for_next_period = _backup_due_at(next_from_last, values.get("backup_time", "03:00"))
    if now < due_for_next_period:
        return due_for_next_period
    return due_today if now < due_today else due_today + timedelta(days=interval_days)


def run_scheduled_backup_if_due(
    db: Session,
    now: datetime | None = None,
    *,
    force: bool = False,
    actor: User | None = None,
) -> BackupRecordView | None:
    """Run one scheduled full backup when settings say it is due.

    This is panel-side scheduling only. Full backups may ask Tic/Tak Node-agents
    for server snapshots when those agents are available.
    """
    ensure_default_settings(db)
    values = get_basic_settings(db)
    if actor is not None:
        require_admin(actor)
    if not force and values.get("backups_enabled", "1") != "1":
        return None

    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(MOSCOW_TZ)
    due_at = _backup_due_at(now, values.get("backup_time", "03:00"))
    if not force and now < due_at:
        return None

    last_run_at = _parse_backup_last_run(values.get("backup_last_run_at"))
    if last_run_at is not None:
        last_run_at = last_run_at.astimezone(MOSCOW_TZ)
    interval_days = _backup_schedule_interval_days(values.get("backup_frequency", "daily"))
    if not force and last_run_at is not None and now - last_run_at < timedelta(days=interval_days):
        return None

    admin = actor or db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
    if admin is None:
        return None

    cleanup_old_backups(db, admin, now=now)
    record = create_backup(db, admin, BackupCreateRequest(backup_type=BackupType.FULL))
    cleanup_old_backups(db, admin, now=now)
    row = db.get(AppSetting, "backup_last_run_at") or AppSetting(key="backup_last_run_at", value="")
    row.value = now.isoformat()
    db.add(row)
    db.commit()
    return record


def server_backup_policy(db: Session) -> dict[str, object]:
    values = get_basic_settings(db)
    remote_storage_server_id = _backup_int(values.get("backup_remote_storage_server_id"))
    remote_storage_server = None
    if remote_storage_server_id is not None:
        remote_storage_server = db.execute(
            select(Server).where(Server.id == remote_storage_server_id, Server.server_type == ServerType.STORAGE)
        ).scalar_one_or_none()
    return {
        "fresh_retention_days": int(values.get("server_backup_retention_days", "90")),
        "fresh_size_limit_mb": int(values.get("server_backup_size_limit_mb", "5120")),
        "monthly_retention_days": int(values.get("server_backup_monthly_retention_days", "365")),
        "monthly_size_limit_mb": int(values.get("server_backup_monthly_size_limit_mb", "3072")),
        "remote_storage_server": (
            {
                "id": remote_storage_server.id,
                "name": remote_storage_server.name,
                "host": remote_storage_server.host,
                "ssh_port": remote_storage_server.ssh_port,
            }
            if remote_storage_server is not None
            else None
        ),
    }


def _parse_github_repo_url(repo_url: str) -> tuple[str, str] | None:
    value = repo_url.strip().removesuffix("/")
    if not value:
        return None
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.netloc.lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _version_parts(version: str) -> tuple[int, ...]:
    clean = version.strip().lower().removeprefix("v")
    numbers: list[int] = []
    for part in clean.replace("-", ".").split("."):
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers or [0])


def check_panel_updates(db: Session, actor: User) -> dict[str, object]:
    require_admin(actor)
    basic_settings = get_basic_settings(db)
    current_version = get_panel_version()
    repo_url = basic_settings.get("nelomai_git_repo", "").strip()
    repo = _parse_github_repo_url(repo_url)
    if repo is None:
        result = {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": repo_url,
            "release_url": None,
            "message": "GitHub repository is not configured",
        }
        write_audit_log(
            db,
            event_type="updates.panel_check",
            severity="warning",
            message="Panel update check skipped: repository is not configured",
            message_ru="Проверка обновлений панели пропущена: Git-репозиторий Nelomai не задан.",
            actor_user_id=actor.id,
        )
        _PANEL_UPDATE_CACHE["fetched_at"] = time.time()
        _PANEL_UPDATE_CACHE["value"] = dict(result)
        return result

    owner, name = repo
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nelomai-panel-update-check"}
    latest_version: str | None = None
    release_url: str | None = None
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
            release_response = client.get(f"https://api.github.com/repos/{owner}/{name}/releases/latest")
            if release_response.status_code == 200:
                data = release_response.json()
                latest_version = str(data.get("tag_name") or "").strip() or None
                release_url = str(data.get("html_url") or "").strip() or None
            elif release_response.status_code == 404:
                tags_response = client.get(f"https://api.github.com/repos/{owner}/{name}/tags?per_page=1")
                tags_response.raise_for_status()
                tags = tags_response.json()
                if tags:
                    latest_version = str(tags[0].get("name") or "").strip() or None
                    release_url = f"https://github.com/{owner}/{name}/releases/tag/{latest_version}" if latest_version else None
            else:
                release_response.raise_for_status()
    except httpx.HTTPError as exc:
        write_audit_log(
            db,
            event_type="updates.panel_check_failed",
            severity="error",
            message=f"Panel update check failed: {exc}",
            message_ru=f"Не удалось проверить обновления панели: {exc}",
            actor_user_id=actor.id,
        )
        raise ServerOperationUnavailableError(f"Could not check panel updates: {exc}") from exc

    if not latest_version:
        result = {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": repo_url,
            "release_url": None,
            "message": "No releases or tags found",
        }
        write_audit_log(
            db,
            event_type="updates.panel_check",
            severity="warning",
            message="Panel update check completed: no releases or tags found",
            message_ru="Проверка обновлений панели завершена: релизы или теги не найдены.",
            actor_user_id=actor.id,
        )
        _PANEL_UPDATE_CACHE["fetched_at"] = time.time()
        _PANEL_UPDATE_CACHE["value"] = dict(result)
        return result

    update_available = _version_parts(latest_version) > _version_parts(current_version)
    result = {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "repo_url": repo_url,
        "release_url": release_url,
        "message": "Update is available" if update_available else "Panel is up to date",
    }
    write_audit_log(
        db,
        event_type="updates.panel_check",
        severity="warning" if update_available else "info",
        message=f"Panel update check completed: current={current_version}, latest={latest_version}",
        message_ru=(
            f"Доступно обновление панели: текущая версия {current_version}, последняя {latest_version}."
            if update_available
            else f"Панель актуальна: текущая версия {current_version}, последняя {latest_version}."
        ),
        actor_user_id=actor.id,
    )
    _PANEL_UPDATE_CACHE["fetched_at"] = time.time()
    _PANEL_UPDATE_CACHE["value"] = dict(result)
    return result


def _check_panel_updates_summary(db: Session) -> dict[str, object]:
    basic_settings = get_basic_settings(db)
    current_version = get_panel_version()
    repo_url = basic_settings.get("nelomai_git_repo", "").strip()
    repo = _parse_github_repo_url(repo_url)
    if repo is None:
        return {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": repo_url,
            "release_url": None,
            "message": "GitHub repository is not configured",
        }

    owner, name = repo
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nelomai-panel-version-summary"}
    latest_version: str | None = None
    release_url: str | None = None
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True, headers=headers) as client:
            release_response = client.get(f"https://api.github.com/repos/{owner}/{name}/releases/latest")
            if release_response.status_code == 200:
                data = release_response.json()
                latest_version = str(data.get("tag_name") or "").strip() or None
                release_url = str(data.get("html_url") or "").strip() or None
            elif release_response.status_code == 404:
                tags_response = client.get(f"https://api.github.com/repos/{owner}/{name}/tags?per_page=1")
                tags_response.raise_for_status()
                tags = tags_response.json()
                if tags:
                    latest_version = str(tags[0].get("name") or "").strip() or None
                    release_url = f"https://github.com/{owner}/{name}/releases/tag/{latest_version}" if latest_version else None
            else:
                release_response.raise_for_status()
    except httpx.HTTPError as exc:
        return {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": repo_url,
            "release_url": None,
            "message": f"Could not check panel updates: {exc}",
        }

    if not latest_version:
        return {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": repo_url,
            "release_url": None,
            "message": "No releases or tags found",
        }

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": _version_parts(latest_version) > _version_parts(current_version),
        "repo_url": repo_url,
        "release_url": release_url,
        "message": "Update is available" if _version_parts(latest_version) > _version_parts(current_version) else "Panel is up to date",
    }


def _server_agent_repository(settings_values: dict[str, str], server: Server) -> str:
    return settings_values.get("nelomai_git_repo", "").strip()


def _server_agent_update_view(
    *,
    server: Server,
    repository_url: str,
    status: str,
    message: str,
    agent_version: str | None = None,
    contract_version: str | None = None,
    capabilities: list[str] | None = None,
    is_legacy: bool = False,
    current_version: str | None = None,
    latest_version: str | None = None,
    update_available: bool = False,
    release_url: str | None = None,
) -> ServerAgentUpdateView:
    return ServerAgentUpdateView(
        server_id=server.id,
        name=server.name,
        server_type=server.server_type.value,
        repository_url=repository_url,
        status=status,
        agent_version=agent_version,
        contract_version=contract_version,
        capabilities=capabilities or [],
        is_legacy=is_legacy,
        current_version=current_version,
        latest_version=latest_version,
        update_available=update_available,
        release_url=release_url,
        message=message,
    )


def _run_server_agent_update_action(db: Session, actor: User, server: Server, action: str) -> ServerAgentUpdateView:
    settings_values = get_basic_settings(db)
    repository_url = _server_agent_repository(settings_values, server)
    if not repository_url:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="repo_missing",
            message="Git repository is not configured",
        )
    if server.is_excluded:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="excluded",
            message="Server is excluded from the panel environment",
        )

    try:
        response = _run_agent_executor_logged(
            db,
            _build_server_executor_payload(
                action=action,
                server=server,
                extra={
                    # Panel-side contract for the future Node-agent: Nelomai is
                    # a monorepo; the server specialization is selected by the
                    # component field in the payload envelope.
                    "repository_url": repository_url,
                },
            ),
            actor_user_id=actor.id,
        )
    except ServerOperationUnavailableError as exc:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="error",
            message=str(exc),
        )

    is_legacy = response.get("contract_version") is None and response.get("supported_contracts") is None
    status_value = str(response.get("status") or ("updated" if action == "update_server_agent" else "checked"))
    if action == "check_server_agent_update" and is_legacy:
        status_value = "legacy"
    update_available = bool(response.get("update_available", False))
    if action == "update_server_agent" and response.get("ok", True) is not False:
        server.is_active = True
        server.last_seen_at = utc_now()
        db.add(server)
        db.commit()

    return _server_agent_update_view(
        server=server,
        repository_url=repository_url,
        status=status_value,
        agent_version=str(response.get("agent_version")) if response.get("agent_version") is not None else None,
        contract_version=str(response.get("contract_version")) if response.get("contract_version") is not None else None,
        capabilities=[str(item) for item in response.get("capabilities", [])] if isinstance(response.get("capabilities"), list) else [],
        is_legacy=is_legacy,
        current_version=str(response.get("current_version")) if response.get("current_version") is not None else None,
        latest_version=str(response.get("latest_version")) if response.get("latest_version") is not None else None,
        update_available=update_available,
        release_url=str(response.get("release_url")) if response.get("release_url") else None,
        message=str(response.get("message") or ("Agent update completed" if action == "update_server_agent" else "Agent update check completed")),
    )


def _server_agent_update_summary(db: Session, server: Server) -> ServerAgentUpdateView:
    settings_values = get_basic_settings(db)
    repository_url = _server_agent_repository(settings_values, server)
    if not repository_url:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="repo_missing",
            message="Git repository is not configured",
        )
    if server.is_excluded:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="excluded",
            message="Server is excluded from the panel environment",
        )
    try:
        response = _run_tic_executor(
            _build_server_executor_payload(
                action="check_server_agent_update",
                server=server,
                extra={"repository_url": repository_url},
            )
        )
    except ServerOperationUnavailableError as exc:
        return _server_agent_update_view(
            server=server,
            repository_url=repository_url,
            status="error",
            message=str(exc),
        )

    is_legacy = response.get("contract_version") is None and response.get("supported_contracts") is None
    status_value = str(response.get("status") or "checked")
    if is_legacy:
        status_value = "legacy"
    return _server_agent_update_view(
        server=server,
        repository_url=repository_url,
        status=status_value,
        agent_version=str(response.get("agent_version")) if response.get("agent_version") is not None else None,
        contract_version=str(response.get("contract_version")) if response.get("contract_version") is not None else None,
        capabilities=[str(item) for item in response.get("capabilities", [])] if isinstance(response.get("capabilities"), list) else [],
        is_legacy=is_legacy,
        current_version=str(response.get("current_version")) if response.get("current_version") is not None else None,
        latest_version=str(response.get("latest_version")) if response.get("latest_version") is not None else None,
        update_available=bool(response.get("update_available", False)),
        release_url=str(response.get("release_url")) if response.get("release_url") else None,
        message=str(response.get("message") or "Agent update check completed"),
    )


def _check_panel_updates_summary_cached(db: Session, *, force: bool = False) -> dict[str, object]:
    fetched_at = float(_PANEL_UPDATE_CACHE.get("fetched_at") or 0.0)
    cached_value = _PANEL_UPDATE_CACHE.get("value")
    if (
        not force
        and isinstance(cached_value, dict)
        and (time.time() - fetched_at) < UPDATES_CACHE_TTL_SECONDS
    ):
        return dict(cached_value)
    if not force:
        basic_settings = get_basic_settings(db)
        current_version = get_panel_version()
        return {
            "current_version": current_version,
            "latest_version": None,
            "update_available": False,
            "repo_url": basic_settings.get("nelomai_git_repo", "").strip(),
            "release_url": None,
            "message": "Проверка обновлений ещё не запускалась в этой сессии",
        }
    value = _check_panel_updates_summary(db)
    _PANEL_UPDATE_CACHE["fetched_at"] = time.time()
    _PANEL_UPDATE_CACHE["value"] = dict(value)
    return value


def _server_agent_update_summary_cached(
    db: Session,
    server: Server,
    *,
    force: bool = False,
) -> ServerAgentUpdateView:
    cached = _SERVER_AGENT_UPDATE_CACHE.get(server.id)
    if not force and cached is not None and (time.time() - cached[0]) < UPDATES_CACHE_TTL_SECONDS:
        return cached[1]
    if not force:
        return _server_agent_update_view(
            server=server,
            repository_url=_server_agent_repository(get_basic_settings(db), server),
            status="unchecked",
            message="Проверка обновлений ещё не запускалась в этой сессии",
        )
    value = _server_agent_update_summary(db, server)
    _SERVER_AGENT_UPDATE_CACHE[server.id] = (time.time(), value)
    return value


def has_available_updates(db: Session) -> bool:
    panel_update_summary = _check_panel_updates_summary_cached(db)
    if bool(panel_update_summary.get("update_available")):
        return True
    servers = db.execute(
        select(Server)
        .where(Server.server_type.in_([ServerType.TIC, ServerType.TAK, ServerType.STORAGE]))
        .order_by(Server.id.asc())
    ).scalars().all()
    for server in servers:
        if _server_agent_update_summary_cached(db, server).update_available:
            return True
    return False


def _agent_update_problem_priority(item: ServerAgentUpdateView) -> tuple[int, str, int]:
    if item.status in {"legacy", "repo_missing", "excluded", "error"}:
        return (0, item.server_type, item.server_id)
    if item.update_available:
        return (1, item.server_type, item.server_id)
    return (2, item.server_type, item.server_id)


def get_updates_page_data(db: Session, actor: User, *, attention_only: bool = False, server_type: str = "all") -> UpdatesPageView:
    require_admin(actor)
    panel_update_summary = PanelUpdateCheckView(**_check_panel_updates_summary_cached(db))
    selected_server_type = server_type if server_type in {"all", "tic", "tak", "storage"} else "all"
    servers = db.execute(
        select(Server)
        .where(Server.server_type.in_([ServerType.TIC, ServerType.TAK, ServerType.STORAGE]))
        .order_by(Server.server_type.asc(), Server.name.asc(), Server.id.asc())
    ).scalars().all()
    if selected_server_type != "all":
        servers = [server for server in servers if server.server_type.value == selected_server_type]
    all_agent_update_summaries = [_server_agent_update_summary_cached(db, server) for server in servers]
    update_available_count = (1 if panel_update_summary.update_available else 0) + len(
        [item for item in all_agent_update_summaries if item.update_available]
    )
    problem_agent_update_summaries = [
        item
        for item in all_agent_update_summaries
        if item.update_available or item.status in {"legacy", "repo_missing", "excluded", "error"}
    ]
    healthy_agent_update_summaries = [
        item
        for item in all_agent_update_summaries
        if not (item.update_available or item.status in {"legacy", "repo_missing", "excluded", "error"})
    ]
    problem_agent_update_summaries = sorted(problem_agent_update_summaries, key=_agent_update_problem_priority)
    healthy_agent_update_summaries = sorted(healthy_agent_update_summaries, key=lambda item: (item.server_type, item.server_id))
    bulk_updatable_agent_count = len([item for item in all_agent_update_summaries if item.update_available])
    manual_review_agent_count = len(
        [item for item in all_agent_update_summaries if item.status in {"legacy", "repo_missing", "excluded", "error"}]
    )
    version_issue_count = (1 if panel_update_summary.update_available else 0) + len(
        problem_agent_update_summaries
    )
    agent_update_summaries = problem_agent_update_summaries if attention_only else all_agent_update_summaries
    return UpdatesPageView(
        panel_update_summary=panel_update_summary,
        agent_update_summaries=agent_update_summaries,
        problem_agent_update_summaries=problem_agent_update_summaries,
        healthy_agent_update_summaries=healthy_agent_update_summaries,
        update_available_count=update_available_count,
        version_issue_count=version_issue_count,
        attention_only=attention_only,
        bulk_updatable_agent_count=bulk_updatable_agent_count,
        manual_review_agent_count=manual_review_agent_count,
        selected_server_type=selected_server_type,
    )


def check_server_agent_updates(db: Session, actor: User) -> list[ServerAgentUpdateView]:
    require_admin(actor)
    job = create_panel_job(db, actor, "agent_updates_check")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Готовим проверку обновлений агентов")
    servers = db.execute(select(Server).order_by(Server.server_type.asc(), Server.name.asc())).scalars().all()
    try:
        results: list[ServerAgentUpdateView] = []
        total = max(1, len(servers))
        for index, server in enumerate(servers, start=1):
            update_panel_job_progress(db, job, 10 + int(index / total * 75), f"Проверяем {server.name} ({index}/{total})")
            result = _run_server_agent_update_action(db, actor, server, "check_server_agent_update")
            results.append(result)
            _SERVER_AGENT_UPDATE_CACHE[server.id] = (time.time(), result)
            _write_agent_update_audit_log(db, actor, result, action="check")
        errors = len([item for item in results if item.status == "error"])
        available = len([item for item in results if item.update_available])
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Проверка агентов завершена: обновлений {available}, ошибок {errors}")
        return results
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка проверки обновлений агентов: {exc}")
        raise


def apply_server_agent_updates(db: Session, actor: User, server_id: int | None = None) -> list[ServerAgentUpdateView]:
    require_admin(actor)
    job = create_panel_job(db, actor, "agent_updates_apply")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Готовим обновление агентов")
    if server_id is not None:
        servers = [get_server_by_id(db, server_id)]
    else:
        servers = db.execute(select(Server).order_by(Server.server_type.asc(), Server.name.asc())).scalars().all()
    try:
        results: list[ServerAgentUpdateView] = []
        total = max(1, len(servers))
        for index, server in enumerate(servers, start=1):
            update_panel_job_progress(db, job, 10 + int(index / total * 75), f"Обновляем {server.name} ({index}/{total})")
            result = _run_server_agent_update_action(db, actor, server, "update_server_agent")
            results.append(result)
            _SERVER_AGENT_UPDATE_CACHE[server.id] = (time.time(), result)
            _write_agent_update_audit_log(db, actor, result, action="apply")
        errors = len([item for item in results if item.status == "error"])
        updated = len([item for item in results if item.status == "updated"])
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Обновление агентов завершено: обновлено {updated}, ошибок {errors}")
        return results
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка обновления агентов: {exc}")
        raise


def _write_agent_update_audit_log(db: Session, actor: User, result: ServerAgentUpdateView, action: str) -> None:
    event_type = "updates.agent_check" if action == "check" else "updates.agent_apply"
    severity = "error" if result.status == "error" else ("warning" if result.status in {"repo_missing", "excluded", "legacy"} or result.update_available else "info")
    action_ru = "Проверка обновления агента" if action == "check" else "Обновление агента"
    if result.status == "repo_missing":
        message_ru = f"{action_ru} {result.name} пропущена: Git-репозиторий Nelomai не задан."
    elif result.status == "excluded":
        message_ru = f"{action_ru} {result.name} пропущена: сервер исключён из окружения панели."
    elif result.status == "legacy":
        message_ru = f"{action_ru} {result.name}: агент ответил по старому формату, доступен только безопасный путь обновления."
    elif result.status == "error":
        message_ru = f"{action_ru} {result.name} завершилась ошибкой: {result.message}"
    elif result.update_available:
        message_ru = f"{action_ru} {result.name}: доступно обновление агента до {result.latest_version or 'новой версии'}."
    else:
        message_ru = f"{action_ru} {result.name} завершена: {result.message}"
    write_audit_log(
        db,
        event_type=event_type,
        severity=severity,
        message=f"{event_type}: server={result.name}, component={result.server_type}, status={result.status}, message={result.message}",
        message_ru=message_ru,
        actor_user_id=actor.id,
        server_id=result.server_id,
        details=json.dumps(
            {
                "server_type": result.server_type,
                "status": result.status,
                "repository_url": result.repository_url,
                "agent_version": result.agent_version,
                "contract_version": result.contract_version,
                "capabilities": result.capabilities,
                "is_legacy": result.is_legacy,
                "current_version": result.current_version,
                "latest_version": result.latest_version,
                "update_available": result.update_available,
            },
            ensure_ascii=False,
        ),
    )


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _row_dict(item: object, fields: list[str]) -> dict[str, object]:
    return {field: _json_safe(getattr(item, field)) for field in fields}


def _write_json(zip_file: zipfile.ZipFile, path: str, payload: object) -> None:
    zip_file.writestr(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _safe_backup_path_part(value: object, fallback: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value or ""))
    safe = safe.strip("._-")
    return safe or fallback


def _backup_panel_users_payload(db: Session) -> dict[str, object]:
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()
    interfaces = db.execute(select(Interface).order_by(Interface.id.asc())).scalars().all()
    peers = db.execute(select(Peer).order_by(Peer.id.asc())).scalars().all()
    resources = db.execute(select(UserResource).order_by(UserResource.id.asc())).scalars().all()
    contact_links = db.execute(select(UserContactLink).order_by(UserContactLink.id.asc())).scalars().all()
    filters = db.execute(select(ResourceFilter).order_by(ResourceFilter.id.asc())).scalars().all()
    return {
        "users": [
            _row_dict(user, ["id", "login", "password_hash", "display_name", "region", "role", "expires_at", "is_active", "created_at"])
            for user in users
        ],
        "interfaces": [
            _row_dict(
                interface,
                [
                    "id",
                    "agent_interface_id",
                    "name",
                    "description",
                    "user_id",
                    "tic_server_id",
                    "tak_server_id",
                    "route_mode",
                    "listen_port",
                    "address_v4",
                    "address_v6",
                    "peer_limit",
                    "exclusion_filters_enabled",
                    "is_pending_owner",
                    "created_at",
                ],
            )
            for interface in interfaces
        ],
        "peers": [
            _row_dict(
                peer,
                [
                    "id",
                    "interface_id",
                    "slot",
                    "comment",
                    "is_enabled",
                    "block_filters_enabled",
                    "expires_at",
                    "handshake_at",
                    "traffic_7d_mb",
                    "traffic_30d_mb",
                    "created_at",
                ],
            )
            for peer in peers
        ],
        "user_resources": [
            _row_dict(resource, ["id", "user_id", "yandex_disk_url", "amnezia_vpn_finland", "outline_japan", "updated_at"])
            for resource in resources
        ],
        "user_contact_links": [_row_dict(link, ["id", "user_id", "value", "updated_at"]) for link in contact_links],
        "resource_filters": [
            _row_dict(
                item,
                [
                    "id",
                    "user_id",
                    "peer_id",
                    "name",
                    "kind",
                    "filter_type",
                    "scope",
                    "value",
                    "description",
                    "is_active",
                    "created_at",
                ],
            )
            for item in filters
        ],
    }


def _backup_panel_system_payload(db: Session, *, full: bool = False) -> dict[str, object]:
    settings_rows = db.execute(select(AppSetting).order_by(AppSetting.key.asc())).scalars().all()
    servers = db.execute(select(Server).order_by(Server.id.asc())).scalars().all()
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if full:
        query = query.where(
            or_(
                AuditLog.severity == "error",
                AuditLog.event_type.like("updates.%"),
                AuditLog.event_type.like("http.503"),
            )
        )
    else:
        query = query.where(AuditLog.severity == "error")
    logs = db.execute(query.limit(500)).scalars().all()
    return {
        "settings": [_row_dict(row, ["key", "value", "updated_at"]) for row in settings_rows],
        "servers": [
            _row_dict(
                server,
                [
                    "id",
                    "name",
                    "server_type",
                    "host",
                    "ssh_port",
                    "ssh_login",
                    "ssh_password",
                    "is_excluded",
                    "is_active",
                    "last_seen_at",
                    "created_at",
                ],
            )
            for server in servers
        ],
        "critical_audit_logs": [
            _row_dict(
                log,
                [
                    "id",
                    "event_type",
                    "severity",
                    "message",
                    "message_ru",
                    "actor_user_id",
                    "target_user_id",
                    "server_id",
                    "details",
                    "created_at",
                ],
            )
            for log in logs
        ],
    }


def _write_peer_configs(db: Session, zip_file: zipfile.ZipFile, manifest: dict[str, object]) -> None:
    results: list[dict[str, object]] = []
    peers = (
        db.execute(
            select(Peer)
            .options(joinedload(Peer.interface).joinedload(Interface.tic_server), joinedload(Peer.interface).joinedload(Interface.tak_server))
            .order_by(Peer.id.asc())
        )
        .unique()
        .scalars()
        .all()
    )
    for peer in peers:
        record = {"peer_id": peer.id, "interface_id": peer.interface_id, "slot": peer.slot, "status": "unavailable"}
        try:
            response = _run_peer_agent_action(db, "download_peer_config", peer.interface, peer)
            payload = _extract_download_payload(
                response,
                default_filename=f"{peer.interface.name}-{peer.slot}.conf",
                default_content_type="text/plain; charset=utf-8",
            )
            agent_filename = str(payload["filename"])
            interface_dir = (
                f"{peer.interface.tic_server_id}-"
                f"{peer.interface.id}-"
                f"{_safe_backup_path_part(peer.interface.name, f'interface-{peer.interface.id}')}"
            )
            filename = f"{peer.slot}.conf"
            archive_path = f"peer_configs/{interface_dir}/{filename}"
            zip_file.writestr(archive_path, payload["content"])
            record["status"] = "included"
            record["filename"] = archive_path
            record["agent_filename"] = agent_filename
        except ServerOperationUnavailableError as exc:
            record["error"] = str(exc)
        results.append(record)
    manifest["peer_configs"] = results


def _write_server_snapshots(db: Session, zip_file: zipfile.ZipFile, manifest: dict[str, object], backup_id: int) -> None:
    results: list[dict[str, object]] = []
    policy = server_backup_policy(db)
    servers = db.execute(
        select(Server)
        .where(Server.server_type.in_([ServerType.TIC, ServerType.TAK]))
        .order_by(Server.id.asc())
    ).scalars().all()
    for server in servers:
        record = {"server_id": server.id, "name": server.name, "server_type": server.server_type.value, "status": "unavailable"}
        try:
            response = _run_agent_executor_logged(
                db,
                _build_server_executor_payload(
                    action="create_server_backup",
                    server=server,
                    extra={
                        "backup_id": backup_id,
                        "backup_type": BackupType.FULL.value,
                        "backup_policy": policy,
                    },
                ),
            )
            payload = _extract_download_payload(
                response,
                default_filename=f"{server.name}-snapshot.zip",
                default_content_type="application/zip",
            )
            filename = str(payload["filename"]).replace("/", "_").replace("\\", "_")
            content = payload["content"]
            snapshot_path = f"server_snapshots/{server.id}-{filename}"
            zip_file.writestr(snapshot_path, content)
            record["status"] = "included"
            record["filename"] = snapshot_path
            record["size_bytes"] = len(content)
            record["sha256"] = hashlib.sha256(content).hexdigest()
        except ServerOperationUnavailableError as exc:
            record["error"] = str(exc)
        results.append(record)
    manifest["server_snapshots"] = results
    manifest["server_backup_policy"] = policy


def format_size_label(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{max(1, round(size_bytes / 1024))} КБ"
    return f"{size_bytes / (1024 * 1024):.1f} МБ"


def serialize_backup_record(record: BackupRecord) -> BackupRecordView:
    if record.size_bytes < 1024 * 1024:
        size_label = f"{max(1, round(record.size_bytes / 1024))} КБ"
    else:
        size_label = f"{record.size_bytes / (1024 * 1024):.1f} МБ"
    created_at_utc = record.created_at
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=UTC)
    created_at_local = created_at_utc.astimezone(MOSCOW_TZ)
    return BackupRecordView(
        id=record.id,
        backup_type=record.backup_type,
        status=record.status,
        filename=record.filename,
        size_bytes=record.size_bytes,
        size_label=size_label,
        contains_secrets=record.contains_secrets,
        created_by_login=record.created_by_user.login if record.created_by_user else None,
        created_at=created_at_utc,
        created_at_local=created_at_local,
        created_label=created_at_local.strftime("%d.%m.%Y %H:%M:%S"),
        completed_at=record.completed_at,
        error_message=record.error_message,
    )


def get_backups_page(db: Session, actor: User) -> BackupsPageView:
    require_admin(actor)
    records = (
        db.execute(select(BackupRecord).options(joinedload(BackupRecord.created_by_user)).order_by(BackupRecord.created_at.desc()))
        .scalars()
        .all()
    )
    storage_servers = db.execute(
        select(Server).where(Server.server_type == ServerType.STORAGE).order_by(Server.name.asc())
    ).scalars().all()
    return BackupsPageView(
        settings=get_backup_settings(db, actor),
        backups=[serialize_backup_record(record) for record in records],
        storage_server_options=serialize_server_options(storage_servers),
    )


def build_all_backups_archive(db: Session, actor: User) -> tuple[bytes, str]:
    require_admin(actor)
    records = db.execute(select(BackupRecord).order_by(BackupRecord.created_at.asc(), BackupRecord.id.asc())).scalars().all()
    manifest: dict[str, object] = {"created_at": utc_now().isoformat(), "backups": []}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for record in records:
            path = Path(record.storage_path)
            item = {
                "id": record.id,
                "backup_type": record.backup_type.value,
                "status": record.status,
                "filename": record.filename,
                "included": False,
            }
            if path.exists() and path.is_file():
                archive_name = f"backups/{record.id}-{record.filename}"
                zip_file.write(path, archive_name)
                item["included"] = True
                item["archive_path"] = archive_name
                item["size_bytes"] = path.stat().st_size
            else:
                item["error"] = "file_missing"
            manifest["backups"].append(item)
        _write_json(zip_file, "manifest.json", manifest)
    created_at_local = utc_now().astimezone(MOSCOW_TZ)
    filename = f"nelomai-all-backups-{created_at_local.strftime('%Y%m%d-%H%M%S')}.zip"
    write_audit_log(
        db,
        event_type="backups.download_all",
        severity="info",
        message="All panel backups archive downloaded.",
        message_ru="Скачан архив всех бэкапов панели.",
        actor_user_id=actor.id,
    )
    return buffer.getvalue(), filename


def create_backup(db: Session, actor: User, payload: BackupCreateRequest) -> BackupRecordView:
    require_admin(actor)
    storage_dir = backup_storage_path(db)
    storage_dir.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()
    job = create_panel_job(db, actor, "backup_create")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, f"Подготовка {payload.backup_type.value}-бэкапа")
    record = BackupRecord(
        backup_type=payload.backup_type,
        status="running",
        filename="pending.zip",
        storage_path="",
        contains_secrets=True,
        created_by_user_id=actor.id,
        manifest_json="{}",
        created_at=created_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    record_id = record.id

    created_at_local = created_at.astimezone(MOSCOW_TZ)
    filename = f"nelomai-{payload.backup_type.value}-{created_at_local.strftime('%Y%m%d-%H%M%S')}-{record_id}.zip"
    archive_path = storage_dir / filename
    manifest: dict[str, object] = {
        "backup_version": "1.0",
        "backup_type": payload.backup_type.value,
        "created_at": created_at.isoformat(),
        "created_at_local": created_at_local.isoformat(),
        "panel_version": get_panel_version(),
        "contains_secrets": True,
        "includes": {
            "users": payload.backup_type in {BackupType.USERS, BackupType.FULL},
            "system": payload.backup_type in {BackupType.SYSTEM, BackupType.FULL},
            "servers": payload.backup_type == BackupType.FULL,
            "peer_configs": payload.backup_type in {BackupType.USERS, BackupType.FULL},
        },
    }
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            if payload.backup_type in {BackupType.USERS, BackupType.FULL}:
                update_panel_job_progress(db, job, 25, "Записываем пользователей, интерфейсы и пиры")
                _write_json(zip_file, "panel/users.json", _backup_panel_users_payload(db))
                update_panel_job_progress(db, job, 40, "Добавляем конфигурации пиров")
                _write_peer_configs(db, zip_file, manifest)
            if payload.backup_type in {BackupType.SYSTEM, BackupType.FULL}:
                update_panel_job_progress(db, job, 55, "Записываем системные настройки панели")
                _write_json(zip_file, "panel/system.json", _backup_panel_system_payload(db, full=payload.backup_type == BackupType.FULL))
            if payload.backup_type == BackupType.FULL:
                update_panel_job_progress(db, job, 75, "Запрашиваем snapshot-файлы серверов")
                _write_server_snapshots(db, zip_file, manifest, record_id)
            update_panel_job_progress(db, job, 90, "Формируем manifest архива")
            _write_json(zip_file, "manifest.json", manifest)
        record = db.get(BackupRecord, record_id)
        if record is None:
            raise EntityNotFoundError("Backup record disappeared during backup creation")
        record.status = "completed"
        record.filename = filename
        record.storage_path = str(archive_path)
        record.size_bytes = archive_path.stat().st_size
        record.manifest_json = json.dumps(manifest, ensure_ascii=False)
        record.completed_at = utc_now()
        db.commit()
        db.refresh(record)
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Бэкап создан: {filename}")
        write_audit_log(
            db,
            event_type="backups.create",
            severity="info",
            message=f"Backup created: {filename}",
            message_ru=f"Создан бэкап {filename}.",
            actor_user_id=actor.id,
        )
    except Exception as exc:
        db.rollback()
        record = db.get(BackupRecord, record_id)
        if record is not None:
            record.status = "failed"
            record.filename = filename
            record.storage_path = str(archive_path)
            record.error_message = str(exc)
            record.completed_at = utc_now()
            record.manifest_json = json.dumps(manifest, ensure_ascii=False)
            db.commit()
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка создания бэкапа: {exc}")
        write_audit_log(
            db,
            event_type="backups.create_failed",
            severity="error",
            message=f"Backup failed: {exc}",
            message_ru=f"Не удалось создать бэкап: {exc}",
            actor_user_id=actor.id,
        )
    final_record = db.get(BackupRecord, record_id) if "record_id" in locals() else None
    if final_record is None:
        raise EntityNotFoundError("Backup record not found after backup operation")
    return serialize_backup_record(final_record)


def get_backup_download_path(db: Session, actor: User, backup_id: int) -> Path:
    require_admin(actor)
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise EntityNotFoundError("Backup not found")
    path = Path(record.storage_path)
    if not path.exists() or not path.is_file():
        raise EntityNotFoundError("Backup file not found")
    return path


def _latest_completed_full_backup(db: Session) -> BackupRecord | None:
    return (
        db.execute(
            select(BackupRecord)
            .where(BackupRecord.backup_type == BackupType.FULL, BackupRecord.status == "completed")
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
        .scalars()
        .first()
    )


def _latest_valid_full_backup_archive(db: Session) -> BackupRecord | None:
    records = (
        db.execute(
            select(BackupRecord)
            .where(BackupRecord.backup_type == BackupType.FULL, BackupRecord.status == "completed")
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
        .scalars()
        .all()
    )
    for record in records:
        path = Path(record.storage_path)
        if path.exists() and path.is_file() and zipfile.is_zipfile(path):
            return record
    return None


def verify_latest_full_backup_server_copies(db: Session, actor: User) -> BackupServerSnapshotVerifyView:
    require_admin(actor)
    job = create_panel_job(db, actor, "backup_verify_freshness")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Ищем последний полный BackUp")
    record = _latest_valid_full_backup_archive(db)
    try:
        if record is None:
            raise EntityNotFoundError("Full backup not found")
        update_panel_job_progress(db, job, 15, f"Читаем архив {record.filename}")
        path = get_backup_download_path(db, actor, record.id)

        with zipfile.ZipFile(path, "r") as zip_file:
            manifest = _load_backup_json(zip_file, "manifest.json")
            snapshots = manifest.get("server_snapshots", [])
            if not isinstance(snapshots, list):
                snapshots = []
            snapshot_items = [item for item in snapshots if isinstance(item, dict)]
            items: list[BackupServerSnapshotVerifyItemView] = []
            total = max(1, len(snapshot_items))
            for index, snapshot in enumerate(snapshot_items, start=1):
                update_panel_job_progress(db, job, 20 + int(index / total * 65), f"Проверяем snapshot {index}/{total}", log=index == 1 or index == total)
                server_id = _backup_int(snapshot.get("server_id"))
                server_name = str(snapshot.get("name") or "")
                server_type = str(snapshot.get("server_type") or "")
                snapshot_filename = snapshot.get("filename") if isinstance(snapshot.get("filename"), str) else None
                if server_id is None:
                    continue
                if snapshot.get("status") != "included" or not snapshot_filename:
                    items.append(
                        BackupServerSnapshotVerifyItemView(
                            server_id=server_id,
                            server_name=server_name,
                            server_type=server_type,
                            snapshot_filename=snapshot_filename,
                            status="skipped",
                            message=str(snapshot.get("error") or "Snapshot is not included in panel backup"),
                        )
                    )
                    continue

                try:
                    content = zip_file.read(snapshot_filename)
                except KeyError:
                    items.append(
                        BackupServerSnapshotVerifyItemView(
                            server_id=server_id,
                            server_name=server_name,
                            server_type=server_type,
                            snapshot_filename=snapshot_filename,
                            status="missing_in_panel",
                            message="Snapshot file is missing inside panel backup archive",
                        )
                    )
                    continue

                size_bytes = len(content)
                sha256 = hashlib.sha256(content).hexdigest()
                server = db.get(Server, server_id)
                if server is None:
                    items.append(
                        BackupServerSnapshotVerifyItemView(
                            server_id=server_id,
                            server_name=server_name,
                            server_type=server_type,
                            snapshot_filename=snapshot_filename,
                            size_bytes=size_bytes,
                            sha256=sha256,
                            status="server_missing",
                            message="Server is no longer connected to the panel",
                        )
                    )
                    continue

                try:
                    response = _run_agent_executor_logged(
                        db,
                        _build_server_executor_payload(
                            action="verify_server_backup_copy",
                            server=server,
                            extra={
                                "backup_id": record.id,
                                "backup_filename": record.filename,
                                "snapshot": {
                                    "filename": snapshot_filename,
                                    "size_bytes": size_bytes,
                                    "sha256": sha256,
                                },
                            },
                        ),
                    )
                    matches = bool(response.get("matches", response.get("ok", False)))
                    items.append(
                        BackupServerSnapshotVerifyItemView(
                            server_id=server.id,
                            server_name=server.name,
                            server_type=server.server_type.value,
                            snapshot_filename=snapshot_filename,
                            size_bytes=size_bytes,
                            sha256=sha256,
                            status="matched" if matches else "mismatch",
                            message=str(response.get("message") or ("Snapshot matches server copy" if matches else "Snapshot differs from server copy")),
                        )
                    )
                except ServerOperationUnavailableError as exc:
                    items.append(
                        BackupServerSnapshotVerifyItemView(
                            server_id=server.id,
                            server_name=server.name,
                            server_type=server.server_type.value,
                            snapshot_filename=snapshot_filename,
                            size_bytes=size_bytes,
                            sha256=sha256,
                            status="unavailable",
                            message=str(exc),
                        )
                    )

        overall_status = "matched" if items and all(item.status == "matched" for item in items) else "attention"
        if not items:
            overall_status = "empty"
        update_panel_job_progress(db, job, 92, f"Фиксируем результат проверки: {overall_status}")
        write_audit_log(
            db,
            event_type="backups.verify_server_copies",
            severity="info" if overall_status == "matched" else "warning",
            message=f"Verified server snapshots for backup {record.filename}: {overall_status}",
            message_ru=f"Проверены серверные snapshot-файлы последнего full backup: {overall_status}.",
            actor_user_id=actor.id,
            details=json.dumps(
                {
                    "backup_id": record.id,
                    "status": overall_status,
                    "items": [item.model_dump(mode="json") for item in items],
                },
                ensure_ascii=False,
            ),
        )
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Проверка свежести завершена: {overall_status}")
        return BackupServerSnapshotVerifyView(
            backup_id=record.id,
            filename=record.filename,
            created_at=record.created_at,
            status=overall_status,
            items=items,
        )
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка проверки свежести: {exc}")
        raise


def _load_backup_json(zip_file: zipfile.ZipFile, path: str) -> dict[str, object]:
    try:
        with zip_file.open(path) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
    except KeyError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidInputError(f"Backup file {path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidInputError(f"Backup file {path} has invalid structure")
    return payload


def _backup_items(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _backup_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _backup_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _backup_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _backup_enum(enum_type: type, value: object, default: object) -> object:
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return default


def _same_backup_interface(existing: Interface, backup_interface: dict[str, object], backup_user_login: str) -> bool:
    backup_name = str(backup_interface.get("name") or "")
    return existing.name == backup_name and existing.user is not None and existing.user.login == backup_user_login


def _restore_login_for_backup_user(backup_user: dict[str, object], overrides: dict[int, str] | None = None) -> str:
    backup_user_id = _backup_int(backup_user.get("id"))
    override = overrides.get(backup_user_id) if overrides is not None and backup_user_id is not None else None
    raw_login = override if override is not None else backup_user.get("login")
    return str(raw_login or "").strip().lower()


def _restore_interface_port(backup_interface: dict[str, object], overrides: dict[int, int] | None = None) -> int | None:
    backup_interface_id = _backup_int(backup_interface.get("id"))
    override = overrides.get(backup_interface_id) if overrides is not None and backup_interface_id is not None else None
    return _backup_int(override if override is not None else backup_interface.get("listen_port"))


def _restore_interface_address(backup_interface: dict[str, object], overrides: dict[int, str] | None = None) -> str:
    backup_interface_id = _backup_int(backup_interface.get("id"))
    override = overrides.get(backup_interface_id) if overrides is not None and backup_interface_id is not None else None
    return str(override if override is not None else backup_interface.get("address_v4") or "").strip()


def build_backup_restore_plan(
    db: Session,
    actor: User,
    backup_id: int,
    payload: BackupRestorePlanRequest | None = None,
) -> BackupRestorePlanView:
    require_admin(actor)
    job = create_panel_job(db, actor, "backup_restore_plan")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Начинаем проверку восстановления")
    try:
        plan = _build_backup_restore_plan_impl(db, actor, backup_id, payload, job=job)
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Проверка завершена: конфликтов {plan.summary.get('conflicts', 0)}")
        return plan
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка проверки восстановления: {exc}")
        raise


def _build_backup_restore_plan_impl(
    db: Session,
    actor: User,
    backup_id: int,
    payload: BackupRestorePlanRequest | None = None,
    *,
    job: PanelJob | None = None,
) -> BackupRestorePlanView:
    require_admin(actor)
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise EntityNotFoundError("Backup not found")
    if record.status != "completed":
        raise PermissionDeniedError("Only completed backups can be planned for restore")
    update_panel_job_progress(db, job, 15, "Проверяем запись бэкапа")
    path = get_backup_download_path(db, actor, backup_id)

    with zipfile.ZipFile(path, "r") as zip_file:
        update_panel_job_progress(db, job, 30, "Читаем manifest и payload архива")
        manifest = _load_backup_json(zip_file, "manifest.json")
        users_payload = _load_backup_json(zip_file, "panel/users.json")
        system_payload = _load_backup_json(zip_file, "panel/system.json")
        archive_files = sorted(zip_file.namelist())

    update_panel_job_progress(db, job, 45, "Разбираем пользователей, интерфейсы и пиры")
    backup_users = _backup_items(users_payload, "users")
    backup_interfaces = _backup_items(users_payload, "interfaces")
    backup_peers = _backup_items(users_payload, "peers")
    backup_resources = _backup_items(users_payload, "user_resources")
    backup_contact_links = _backup_items(users_payload, "user_contact_links")
    backup_filters = _backup_items(users_payload, "resource_filters")
    system_settings = _backup_items(system_payload, "settings")
    system_servers = _backup_items(system_payload, "servers")
    system_logs = _backup_items(system_payload, "critical_audit_logs")
    server_snapshots = manifest.get("server_snapshots", [])
    if not isinstance(server_snapshots, list):
        server_snapshots = []
    selected_ids = None if payload is None else set(payload.user_ids)

    update_panel_job_progress(db, job, 60, "Готовим карту данных для проверки конфликтов")
    interfaces_by_user: dict[int, list[dict[str, object]]] = {}
    for item in backup_interfaces:
        user_id = _backup_int(item.get("user_id"))
        if user_id is not None:
            interfaces_by_user.setdefault(user_id, []).append(item)

    peers_by_interface: dict[int, list[dict[str, object]]] = {}
    for item in backup_peers:
        interface_id = _backup_int(item.get("interface_id"))
        if interface_id is not None:
            peers_by_interface.setdefault(interface_id, []).append(item)

    existing_users = {user.login: user for user in db.execute(select(User)).scalars().all()}
    existing_interfaces = (
        db.execute(select(Interface).options(joinedload(Interface.user), joinedload(Interface.tic_server)).order_by(Interface.id.asc()))
        .unique()
        .scalars()
        .all()
    )
    existing_server_ids = {server.id for server in db.execute(select(Server)).scalars().all()}

    update_panel_job_progress(db, job, 75, "Проверяем конфликты восстановления")
    user_plans: list[BackupRestoreUserPlanView] = []
    total_conflicts = 0
    for backup_user in backup_users:
        backup_user_id = _backup_int(backup_user.get("id"))
        if backup_user_id is None:
            continue
        selected = selected_ids is None or backup_user_id in selected_ids
        if not selected:
            continue
        login = _restore_login_for_backup_user(backup_user)
        display_name = str(backup_user.get("display_name") or "-")
        existing_user = existing_users.get(login)
        user_interfaces = interfaces_by_user.get(backup_user_id, [])
        user_peer_count = sum(len(peers_by_interface.get(_backup_int(item.get("id")) or -1, [])) for item in user_interfaces)
        conflicts: list[BackupRestoreConflictView] = []
        if existing_user is not None:
            conflicts.append(
                BackupRestoreConflictView(
                    conflict_type="login",
                    severity="choice_required",
                    message=f"Login {login} already exists.",
                    current_owner=f"user:{existing_user.id}",
                    backup_user_id=backup_user_id,
                )
            )

        for backup_interface in user_interfaces:
            tic_server_id = _backup_int(backup_interface.get("tic_server_id"))
            tak_server_id = _backup_int(backup_interface.get("tak_server_id"))
            listen_port = _backup_int(backup_interface.get("listen_port"))
            address_v4 = str(backup_interface.get("address_v4") or "")
            interface_name = str(backup_interface.get("name") or "")
            if tic_server_id is None:
                conflicts.append(
                    BackupRestoreConflictView(
                        conflict_type="tic_server",
                        severity="choice_required",
                        message=f"Interface {interface_name} has no Tic server in backup.",
                        backup_user_id=backup_user_id,
                        backup_interface_id=_backup_int(backup_interface.get("id")),
                    )
                )
                continue
            if tic_server_id not in existing_server_ids:
                conflicts.append(
                    BackupRestoreConflictView(
                        conflict_type="tic_server",
                        severity="choice_required",
                        message=f"Tic server {tic_server_id} is not connected to the panel.",
                        backup_user_id=backup_user_id,
                        backup_interface_id=_backup_int(backup_interface.get("id")),
                    )
                )
            if tak_server_id is not None and tak_server_id not in existing_server_ids:
                conflicts.append(
                    BackupRestoreConflictView(
                        conflict_type="tak_server",
                        severity="choice_required",
                        message=f"Tak server {tak_server_id} is not connected to the panel.",
                        backup_user_id=backup_user_id,
                        backup_interface_id=_backup_int(backup_interface.get("id")),
                    )
                )
            for existing_interface in existing_interfaces:
                if existing_interface.tic_server_id != tic_server_id:
                    continue
                same_interface = _same_backup_interface(existing_interface, backup_interface, login)
                if listen_port is not None and existing_interface.listen_port == listen_port:
                    conflicts.append(
                        BackupRestoreConflictView(
                            conflict_type="listen_port",
                            severity="same_interface" if same_interface else "choice_required",
                            message=(
                                f"Port {listen_port} is already used by the same interface {interface_name}."
                                if same_interface
                                else f"Port {listen_port} is already used on Tic server {tic_server_id}."
                            ),
                            current_owner=f"interface:{existing_interface.id}:{existing_interface.name}",
                            backup_user_id=backup_user_id,
                            backup_interface_id=_backup_int(backup_interface.get("id")),
                        )
                    )
                if address_v4 and existing_interface.address_v4 == address_v4:
                    conflicts.append(
                        BackupRestoreConflictView(
                            conflict_type="address_v4",
                            severity="same_interface" if same_interface else "choice_required",
                            message=(
                                f"IPv4 {address_v4} is already used by the same interface {interface_name}."
                                if same_interface
                                else f"IPv4 {address_v4} is already used on Tic server {tic_server_id}."
                            ),
                            current_owner=f"interface:{existing_interface.id}:{existing_interface.name}",
                            backup_user_id=backup_user_id,
                            backup_interface_id=_backup_int(backup_interface.get("id")),
                        )
                    )

        blocking_conflicts = [item for item in conflicts if item.severity == "choice_required"]
        total_conflicts += len(blocking_conflicts)
        status_value = "new"
        if existing_user is not None:
            status_value = "login_conflict"
        if blocking_conflicts:
            status_value = "conflict"
        elif conflicts:
            status_value = "same_interface"
        user_plans.append(
            BackupRestoreUserPlanView(
                backup_user_id=backup_user_id,
                login=login,
                display_name=display_name,
                status=status_value,
                selected=selected,
                existing_user_id=existing_user.id if existing_user else None,
                interface_count=len(user_interfaces),
                peer_count=user_peer_count,
                conflicts=conflicts,
            )
        )

    has_user_payload = "panel/users.json" in archive_files and bool(users_payload)
    can_restore_users = (
        record.backup_type in {BackupType.USERS, BackupType.FULL}
        and has_user_payload
        and bool(user_plans)
    )
    restore_scope = "user_data_only" if can_restore_users else "preview_only"
    update_panel_job_progress(db, job, 90, "Формируем план восстановления")
    warnings: list[str] = []
    if record.backup_type in {BackupType.USERS, BackupType.FULL} and not has_user_payload:
        warnings.append("This backup has no panel/users.json. User restore is unavailable.")
    elif not can_restore_users:
        warnings.append("This backup does not contain selected user restore data.")
    if record.backup_type == BackupType.SYSTEM:
        warnings.append("System backup restore is preview-only for now.")
    if record.backup_type == BackupType.FULL:
        warnings.append("Full backup restore currently applies only the selected user data.")
    if record.contains_secrets:
        warnings.append("This backup contains secrets and peer configs when available.")
    if record.backup_type == BackupType.FULL and not server_snapshots:
        warnings.append("Full backup has no server snapshots in manifest.")

    return BackupRestorePlanView(
        backup_id=record.id,
        backup_type=record.backup_type,
        filename=record.filename,
        backup_version=str(manifest.get("backup_version") or "unknown"),
        contains_secrets=bool(manifest.get("contains_secrets", record.contains_secrets)),
        can_restore_users=can_restore_users,
        can_restore_system=False,
        can_restore_server_snapshots=False,
        restore_scope=restore_scope,
        summary={
            "users": len(user_plans),
            "backup_users_total": len(backup_users),
            "interfaces": sum(item.interface_count for item in user_plans),
            "peers": sum(item.peer_count for item in user_plans),
            "resources": len(backup_resources),
            "contact_links": len(backup_contact_links),
            "filters": len(backup_filters),
            "conflicts": total_conflicts,
            "server_snapshots": len(server_snapshots),
            "selection_active": selected_ids is not None,
            "archive_files": len(archive_files),
            "peer_config_files": len([name for name in archive_files if name.startswith("peer_configs/")]),
            "has_user_payload": has_user_payload,
        },
        system_summary={
            "settings": len(system_settings),
            "servers": len(system_servers),
            "critical_logs": len(system_logs),
            "has_system_data": bool(system_payload),
        },
        archive_files=archive_files[:50],
        users=user_plans,
        server_snapshots=[item for item in server_snapshots if isinstance(item, dict)],
        warnings=warnings,
    )


def restore_backup_users(
    db: Session,
    actor: User,
    backup_id: int,
    payload: BackupRestoreApplyRequest,
) -> BackupRestoreApplyView:
    require_admin(actor)
    job = create_panel_job(db, actor, "backup_restore_users")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Начинаем восстановление пользователей")
    try:
        record = db.get(BackupRecord, backup_id)
        if record is None:
            raise EntityNotFoundError("Backup not found")
        if record.backup_type not in {BackupType.USERS, BackupType.FULL}:
            raise PermissionDeniedError("Only user data from users/full backups can be restored")
        selected_ids = set(payload.user_ids)
        if not selected_ids:
            raise InvalidInputError("Select at least one backup user to restore")

        login_overrides = {int(key): value for key, value in payload.user_login_overrides.items()}
        port_overrides = {int(key): int(value) for key, value in payload.interface_port_overrides.items()}
        address_overrides = {int(key): value for key, value in payload.interface_address_overrides.items()}

        update_panel_job_progress(db, job, 15, "Проверяем план и конфликты")
        plan = _build_backup_restore_plan_impl(db, actor, backup_id, BackupRestorePlanRequest(user_ids=list(selected_ids)))
        if not plan.users:
            raise InvalidInputError("Selected users were not found in backup")

        update_panel_job_progress(db, job, 25, "Читаем данные пользователей из архива")
        path = get_backup_download_path(db, actor, backup_id)
        with zipfile.ZipFile(path, "r") as zip_file:
            users_payload = _load_backup_json(zip_file, "panel/users.json")

        backup_users = _backup_items(users_payload, "users")
        backup_interfaces = _backup_items(users_payload, "interfaces")
        backup_peers = _backup_items(users_payload, "peers")
        backup_resources = _backup_items(users_payload, "user_resources")
        backup_contact_links = _backup_items(users_payload, "user_contact_links")
        backup_filters = _backup_items(users_payload, "resource_filters")

        users_by_backup_id = {
            backup_user_id: item
            for item in backup_users
            if (backup_user_id := _backup_int(item.get("id"))) is not None and backup_user_id in selected_ids
        }
        interfaces_by_backup_id = {
            backup_interface_id: item
            for item in backup_interfaces
            if (backup_interface_id := _backup_int(item.get("id"))) is not None
            and _backup_int(item.get("user_id")) in users_by_backup_id
        }

        user_id_map: dict[int, int] = {}
        interface_id_map: dict[int, int] = {}
        peer_id_map: dict[int, int] = {}
        restored_filters = 0

        update_panel_job_progress(db, job, 35, "Проверяем возможность записи в текущую базу")
        existing_users = {user.login: user for user in db.execute(select(User)).scalars().all()}
        existing_interfaces = db.execute(select(Interface).order_by(Interface.id.asc())).scalars().all()
        existing_server_ids = {server.id for server in db.execute(select(Server)).scalars().all()}

        for backup_user_id, backup_user in users_by_backup_id.items():
            login = _restore_login_for_backup_user(backup_user, login_overrides)
            if not login:
                raise InvalidInputError("Backup user login is empty")
            if existing_users.get(login) is not None:
                raise PermissionDeniedError("Resolve backup restore conflicts before applying")

        for backup_interface_id, backup_interface in interfaces_by_backup_id.items():
            tic_server_id = _backup_int(backup_interface.get("tic_server_id"))
            tak_server_id = _backup_int(backup_interface.get("tak_server_id"))
            listen_port = _restore_interface_port(backup_interface, port_overrides)
            address_v4 = _restore_interface_address(backup_interface, address_overrides)
            if tic_server_id is None or listen_port is None or not address_v4:
                raise InvalidInputError("Backup interface has incomplete required fields")
            if tic_server_id not in existing_server_ids:
                raise PermissionDeniedError("Resolve backup restore conflicts before applying")
            if tak_server_id is not None and tak_server_id not in existing_server_ids:
                raise PermissionDeniedError("Resolve backup restore conflicts before applying")
            for existing_interface in existing_interfaces:
                if existing_interface.tic_server_id != tic_server_id:
                    continue
                if existing_interface.listen_port == listen_port:
                    raise PermissionDeniedError("Resolve backup restore conflicts before applying")
                if existing_interface.address_v4 == address_v4:
                    raise PermissionDeniedError("Resolve backup restore conflicts before applying")

        update_panel_job_progress(db, job, 50, "Восстанавливаем пользователей")
        for backup_user_id, backup_user in users_by_backup_id.items():
            login = _restore_login_for_backup_user(backup_user, login_overrides)
            if not login:
                raise InvalidInputError("Backup user login is empty")
            user = User(
                login=login,
                password_hash=str(backup_user.get("password_hash") or ""),
                display_name=str(backup_user.get("display_name") or "-") or "-",
                region=_backup_enum(UserRegion, backup_user.get("region"), None),
                role=_backup_enum(UserRole, backup_user.get("role"), UserRole.USER),
                expires_at=_backup_datetime(backup_user.get("expires_at")),
                is_active=_backup_bool(backup_user.get("is_active"), True),
                created_at=_backup_datetime(backup_user.get("created_at")) or utc_now(),
            )
            db.add(user)
            db.flush()
            user_id_map[backup_user_id] = user.id

        update_panel_job_progress(db, job, 62, "Восстанавливаем ресурсы и каналы связи")
        for resource in backup_resources:
            backup_user_id = _backup_int(resource.get("user_id"))
            restored_user_id = user_id_map.get(backup_user_id or -1)
            if restored_user_id is None:
                continue
            db.add(
                UserResource(
                    user_id=restored_user_id,
                    yandex_disk_url=resource.get("yandex_disk_url"),
                    amnezia_vpn_finland=resource.get("amnezia_vpn_finland"),
                    outline_japan=resource.get("outline_japan"),
                    updated_at=_backup_datetime(resource.get("updated_at")) or utc_now(),
                )
            )

        for link in backup_contact_links:
            backup_user_id = _backup_int(link.get("user_id"))
            restored_user_id = user_id_map.get(backup_user_id or -1)
            if restored_user_id is None:
                continue
            db.add(
                UserContactLink(
                    user_id=restored_user_id,
                    value=link.get("value"),
                    updated_at=_backup_datetime(link.get("updated_at")) or utc_now(),
                )
            )

        update_panel_job_progress(db, job, 72, "Восстанавливаем интерфейсы")
        for backup_interface_id, backup_interface in interfaces_by_backup_id.items():
            restored_user_id = user_id_map.get(_backup_int(backup_interface.get("user_id")) or -1)
            tic_server_id = _backup_int(backup_interface.get("tic_server_id"))
            listen_port = _restore_interface_port(backup_interface, port_overrides)
            address_v4 = _restore_interface_address(backup_interface, address_overrides)
            if restored_user_id is None or tic_server_id is None or listen_port is None:
                raise InvalidInputError("Backup interface has incomplete required fields")
            interface = Interface(
                agent_interface_id=backup_interface.get("agent_interface_id"),
                name=str(backup_interface.get("name") or "").strip(),
                description=backup_interface.get("description"),
                user_id=restored_user_id,
                tic_server_id=tic_server_id,
                tak_server_id=_backup_int(backup_interface.get("tak_server_id")),
                route_mode=_backup_enum(RouteMode, backup_interface.get("route_mode"), RouteMode.STANDALONE),
                listen_port=listen_port,
                address_v4=address_v4,
                address_v6=backup_interface.get("address_v6"),
                peer_limit=_backup_int(backup_interface.get("peer_limit")) or 5,
                exclusion_filters_enabled=_backup_bool(backup_interface.get("exclusion_filters_enabled"), True),
                is_pending_owner=_backup_bool(backup_interface.get("is_pending_owner"), False),
                created_at=_backup_datetime(backup_interface.get("created_at")) or utc_now(),
            )
            db.add(interface)
            db.flush()
            interface_id_map[backup_interface_id] = interface.id

        update_panel_job_progress(db, job, 82, "Восстанавливаем пиры")
        for backup_peer in backup_peers:
            backup_peer_id = _backup_int(backup_peer.get("id"))
            restored_interface_id = interface_id_map.get(_backup_int(backup_peer.get("interface_id")) or -1)
            if backup_peer_id is None or restored_interface_id is None:
                continue
            peer = Peer(
                interface_id=restored_interface_id,
                slot=_backup_int(backup_peer.get("slot")) or 1,
                comment=backup_peer.get("comment"),
                is_enabled=_backup_bool(backup_peer.get("is_enabled"), True),
                block_filters_enabled=_backup_bool(backup_peer.get("block_filters_enabled"), True),
                expires_at=_backup_datetime(backup_peer.get("expires_at")),
                handshake_at=_backup_datetime(backup_peer.get("handshake_at")),
                traffic_7d_mb=_backup_int(backup_peer.get("traffic_7d_mb")) or 0,
                traffic_30d_mb=_backup_int(backup_peer.get("traffic_30d_mb")) or 0,
                created_at=_backup_datetime(backup_peer.get("created_at")) or utc_now(),
            )
            db.add(peer)
            db.flush()
            peer_id_map[backup_peer_id] = peer.id

        update_panel_job_progress(db, job, 90, "Восстанавливаем пользовательские фильтры")
        for backup_filter in backup_filters:
            backup_user_id = _backup_int(backup_filter.get("user_id"))
            backup_peer_id = _backup_int(backup_filter.get("peer_id"))
            restored_user_id = user_id_map.get(backup_user_id or -1)
            restored_peer_id = peer_id_map.get(backup_peer_id or -1)
            if restored_user_id is None and restored_peer_id is None:
                continue
            if backup_filter.get("scope") == FilterScope.GLOBAL.value:
                continue
            db.add(
                ResourceFilter(
                    user_id=restored_user_id,
                    peer_id=restored_peer_id,
                    name=str(backup_filter.get("name") or "Restored filter"),
                    kind=_backup_enum(FilterKind, backup_filter.get("kind"), FilterKind.EXCLUSION),
                    filter_type=_backup_enum(FilterType, backup_filter.get("filter_type"), FilterType.IP),
                    scope=FilterScope.USER,
                    value=str(backup_filter.get("value") or ""),
                    description=backup_filter.get("description"),
                    is_active=_backup_bool(backup_filter.get("is_active"), True),
                    created_at=_backup_datetime(backup_filter.get("created_at")) or utc_now(),
                )
            )
            restored_filters += 1

        db.commit()
        write_audit_log(
            db,
            event_type="backups.restore_users",
            severity="warning",
            message=f"Restored {len(user_id_map)} users from backup {plan.filename}.",
            message_ru=f"Восстановлено пользователей из бэкапа: {len(user_id_map)}.",
            actor_user_id=actor.id,
            details=json.dumps(
                {
                    "backup_id": backup_id,
                    "backup_filename": plan.filename,
                    "backup_user_ids": sorted(selected_ids),
                    "restored_users": len(user_id_map),
                    "restored_interfaces": len(interface_id_map),
                    "restored_peers": len(peer_id_map),
                    "restored_filters": restored_filters,
                },
                ensure_ascii=False,
            ),
        )
        refreshed_plan = _build_backup_restore_plan_impl(db, actor, backup_id, BackupRestorePlanRequest(user_ids=list(selected_ids)))
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Восстановлено пользователей: {len(user_id_map)}")
        return BackupRestoreApplyView(
            status="restored",
            restored_users=len(user_id_map),
            restored_interfaces=len(interface_id_map),
            restored_peers=len(peer_id_map),
            restored_filters=restored_filters,
            plan=refreshed_plan,
        )
    except Exception as exc:
        db.rollback()
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка восстановления: {exc}")
        raise


def delete_backup(db: Session, actor: User, backup_id: int) -> None:
    require_admin(actor)
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise EntityNotFoundError("Backup not found")
    _delete_backup_file(db, record)
    db.delete(record)
    db.commit()
    write_audit_log(
        db,
        event_type="backups.delete",
        severity="info",
        message=f"Backup deleted: {record.filename}",
        message_ru=f"Удалён бэкап {record.filename}.",
        actor_user_id=actor.id,
    )


def _delete_backup_file(db: Session, record: BackupRecord) -> int:
    same_path_exists = db.execute(
        select(BackupRecord.id)
        .where(BackupRecord.storage_path == record.storage_path, BackupRecord.id != record.id)
        .limit(1)
    ).first()
    if same_path_exists is not None:
        return 0
    path = Path(record.storage_path)
    if path.exists() and path.is_file():
        size = path.stat().st_size
        path.unlink()
        return size
    return max(0, record.size_bytes or 0)


def latest_backup_to_protect(db: Session) -> BackupRecord | None:
    completed = db.execute(
        select(BackupRecord)
        .where(BackupRecord.status == "completed")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
    ).scalars().first()
    if completed is not None:
        return completed
    return db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())).scalars().first()


def delete_panel_backups_except_latest(db: Session, actor: User) -> BackupBulkDeleteView:
    require_admin(actor)
    protected = latest_backup_to_protect(db)
    protected_id = protected.id if protected is not None else None
    deleted_count = 0
    freed_bytes = 0
    records = db.execute(select(BackupRecord).order_by(BackupRecord.created_at.asc(), BackupRecord.id.asc())).scalars().all()
    for record in records:
        if record.id == protected_id:
            continue
        freed_bytes += _delete_backup_file(db, record)
        db.delete(record)
        deleted_count += 1
    db.commit()
    write_audit_log(
        db,
        event_type="backups.delete_all_except_latest",
        severity="warning",
        message=f"Panel backups deleted except latest: {deleted_count}.",
        message_ru=f"Удалены бэкапы панели кроме последнего: {deleted_count}.",
        actor_user_id=actor.id,
        details=f"deleted_count={deleted_count}; freed_bytes={freed_bytes}; protected_backup_id={protected_id or ''}",
    )
    return BackupBulkDeleteView(
        deleted_count=deleted_count,
        freed_bytes=freed_bytes,
        freed_size_label=format_size_label(freed_bytes),
        protected_backup_id=protected_id,
    )


def cleanup_server_backup_copies(db: Session, actor: User) -> ServerBackupCleanupView:
    require_admin(actor)
    job = create_panel_job(db, actor, "backup_cleanup_server_copies")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 5, "Готовим очистку бэкапов на серверах")
    servers = db.execute(
        select(Server)
        .where(Server.server_type.in_([ServerType.TIC, ServerType.TAK]))
        .order_by(Server.server_type.asc(), Server.name.asc())
    ).scalars().all()
    try:
        items: list[ServerBackupCleanupItemView] = []
        total = max(1, len(servers))
        for index, server in enumerate(servers, start=1):
            update_panel_job_progress(db, job, 10 + int(index / total * 75), f"Очищаем сервер {server.name} ({index}/{total})")
            try:
                server_id = server.id
                server_name = server.name
                server_type_value = server.server_type.value
                response = _run_agent_executor_logged(
                    db,
                    _build_server_executor_payload(
                        action="cleanup_server_backups",
                        server=server,
                        extra={
                            # Panel keeps its own backup records. This command is only
                            # for future Tic/Tak Node-agents to clean local server copies.
                            "keep_latest_count": 1,
                            "backup_policy": server_backup_policy(db),
                        },
                    ),
                    actor_user_id=actor.id,
                )
                deleted_count = int(response.get("deleted_count") or 0)
                message = str(response.get("message") or "Server backup cleanup completed")
                items.append(
                    ServerBackupCleanupItemView(
                        server_id=server_id,
                        server_name=server_name,
                        server_type=server_type_value,
                        status="completed",
                        message=message,
                        deleted_count=deleted_count,
                    )
                )
            except ServerOperationUnavailableError as exc:
                items.append(
                    ServerBackupCleanupItemView(
                        server_id=server_id,
                        server_name=server_name,
                        server_type=server_type_value,
                        status="unavailable",
                        message=str(exc),
                        deleted_count=None,
                    )
                )
        overall_status = "completed" if all(item.status == "completed" for item in items) else "partial"
        update_panel_job_progress(db, job, 92, f"Фиксируем результат очистки серверов: {overall_status}")
        write_audit_log(
            db,
            event_type="backups.cleanup_server_copies",
            severity="warning" if overall_status == "completed" else "error",
            message=f"Server backup cleanup finished with status: {overall_status}.",
            message_ru=f"Очистка бэкапов на серверах завершена со статусом: {overall_status}.",
            actor_user_id=actor.id,
            details=json.dumps([item.model_dump() for item in items], ensure_ascii=False),
        )
        job.status = PanelJobStatus.COMPLETED
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Очистка серверных копий завершена: {overall_status}")
        return ServerBackupCleanupView(status=overall_status, items=items)
    except Exception as exc:
        job.status = PanelJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = utc_now()
        update_panel_job_progress(db, job, 100, f"Ошибка очистки серверных копий: {exc}")
        raise


def cleanup_old_backups(db: Session, actor: User, now: datetime | None = None) -> BackupCleanupView:
    require_admin(actor)
    values = get_basic_settings(db)
    retention_days = int(values.get("backup_retention_days", "30"))
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cutoff = now.astimezone(UTC) - timedelta(days=retention_days)
    latest_full = db.execute(
        select(BackupRecord)
        .where(BackupRecord.backup_type == BackupType.FULL, BackupRecord.status == "completed")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
    ).scalars().first()
    protected_full_backup_id = latest_full.id if latest_full is not None else None
    deleted_count = 0
    freed_bytes = 0

    records = db.execute(select(BackupRecord).order_by(BackupRecord.created_at.asc(), BackupRecord.id.asc())).scalars().all()
    for record in records:
        if record.id == protected_full_backup_id:
            continue
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at.astimezone(UTC) >= cutoff:
            continue
        freed_bytes += _delete_backup_file(db, record)
        db.delete(record)
        deleted_count += 1

    db.commit()
    if deleted_count:
        write_audit_log(
            db,
            event_type="backups.cleanup_old",
            severity="info",
            message=f"Old backups cleanup deleted {deleted_count} records.",
            message_ru=f"Удалены старые бэкапы: {deleted_count}.",
            actor_user_id=actor.id,
            details=(
                f"retention_days={retention_days}; deleted_count={deleted_count}; "
                f"freed_bytes={freed_bytes}; protected_full_backup_id={protected_full_backup_id or ''}"
            ),
        )
    return BackupCleanupView(
        deleted_count=deleted_count,
        freed_bytes=freed_bytes,
        freed_size_label=format_size_label(freed_bytes),
        retention_days=retention_days,
        protected_full_backup_id=protected_full_backup_id,
    )


def _metric_seed(value: int) -> int:
    return (value * 37) + 11


def _read_cpu_percent() -> float:
    if os.name != "posix":
        return 0.0
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
        time.sleep(0.05)
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            second_line = handle.readline().strip()
        first_parts = [int(part) for part in first_line.split()[1:]]
        second_parts = [int(part) for part in second_line.split()[1:]]
        if len(first_parts) < 4 or len(second_parts) < 4:
            return 0.0
        first_idle = first_parts[3] + (first_parts[4] if len(first_parts) > 4 else 0)
        second_idle = second_parts[3] + (second_parts[4] if len(second_parts) > 4 else 0)
        first_total = sum(first_parts)
        second_total = sum(second_parts)
        total_delta = second_total - first_total
        idle_delta = second_idle - first_idle
        if total_delta <= 0:
            return 0.0
        usage = 100.0 * (1.0 - (idle_delta / total_delta))
        return round(max(0.0, min(usage, 100.0)), 1)
    except (FileNotFoundError, OSError, ValueError):
        return 0.0


def _read_ram_percent() -> float:
    try:
        if os.name == "posix":
            meminfo: dict[str, int] = {}
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                    for line in handle:
                        if ":" not in line:
                            continue
                        key, raw_value = line.split(":", 1)
                        value_token = raw_value.strip().split()[0]
                        meminfo[key] = int(value_token)
                total_kb = meminfo.get("MemTotal")
                available_kb = meminfo.get("MemAvailable")
                if total_kb and available_kb is not None and total_kb > 0:
                    total = total_kb * 1024
                    available = available_kb * 1024
                else:
                    raise ValueError("incomplete meminfo")
            except (FileNotFoundError, OSError, ValueError):
                page_size = os.sysconf("SC_PAGE_SIZE")
                total_pages = os.sysconf("SC_PHYS_PAGES")
                available_pages = os.sysconf("SC_AVPHYS_PAGES")
                total = page_size * total_pages
                available = page_size * available_pages
        elif os.name == "nt":
            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0.0
            total = int(status.ullTotalPhys)
            available = int(status.ullAvailPhys)
        else:
            return 0.0
        if total <= 0:
            return 0.0
        usage = 100.0 * ((total - available) / total)
        return round(max(0.0, min(usage, 100.0)), 1)
    except (AttributeError, OSError, ValueError):
        return 0.0


def _read_disk_metrics(path: Path) -> tuple[float, float, float]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return 0.0, 0.0, 0.0
    if usage.total <= 0:
        return 0.0, 0.0, 0.0
    total_gb = round(usage.total / (1024**3), 1)
    used_gb = round((usage.total - usage.free) / (1024**3), 1)
    percent = round((used_gb / total_gb) * 100, 1) if total_gb > 0 else 0.0
    return used_gb, total_gb, max(0.0, min(percent, 100.0))


def _build_panel_server_metrics() -> dict[str, float | None]:
    disk_used_gb, disk_total_gb, disk_percent = _read_disk_metrics(Path.cwd())
    return {
        "cpu_percent": _read_cpu_percent(),
        "ram_percent": _read_ram_percent(),
        "disk_total_gb": disk_total_gb,
        "disk_percent": disk_percent,
        "disk_used_gb": disk_used_gb,
        "traffic_mbps": None,
    }


def _build_unavailable_server_metrics() -> dict[str, float | None]:
    return {
        "cpu_percent": 0.0,
        "ram_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_percent": 0.0,
        "disk_used_gb": 0.0,
        "traffic_mbps": None,
    }


def _runtime_metrics_payload(response: dict[str, object]) -> dict[str, object] | None:
    runtime_payload = response.get("runtime")
    runtime_data = runtime_payload if isinstance(runtime_payload, dict) else response
    if not isinstance(runtime_data, dict):
        return None
    metrics = runtime_data.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    top_level_metrics = response.get("metrics")
    if isinstance(top_level_metrics, dict):
        return top_level_metrics
    return None


def _metric_float(metrics: dict[str, object] | None, key: str) -> float | None:
    if not metrics:
        return None
    value = metrics.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metric_int(metrics: dict[str, object] | None, key: str) -> int | None:
    if not metrics:
        return None
    value = metrics.get(key)
    if isinstance(value, int):
        return value
    return None


def _build_server_metrics_from_runtime(response: dict[str, object] | None) -> dict[str, float | None]:
    metrics = _runtime_metrics_payload(response or {})
    if not metrics:
        return _build_unavailable_server_metrics()
    return {
        "cpu_percent": float(_metric_float(metrics, "cpu_percent") or 0.0),
        "ram_percent": float(_metric_float(metrics, "ram_percent") or 0.0),
        "disk_total_gb": float(_metric_float(metrics, "disk_total_gb") or 0.0),
        "disk_percent": float(_metric_float(metrics, "disk_percent") or 0.0),
        "disk_used_gb": float(_metric_float(metrics, "disk_used_gb") or 0.0),
        "traffic_mbps": None,
    }


def _format_bytes_label(value: int | None) -> str | None:
    if value is None or value < 0:
        return None
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    numeric = float(value)
    for unit in units:
        if numeric < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(numeric)} {unit}"
            return f"{numeric:.1f} {unit}"
        numeric /= 1024.0
    return None


def _format_uptime_label(seconds: int | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes or not parts:
        parts.append(f"{minutes}м")
    return " ".join(parts)


def _server_status(server: Server | None) -> str:
    if server is None:
        return "unknown"
    if server.is_excluded:
        return "excluded"
    if server.last_seen_at is None:
        return "unknown"
    return "online" if server.is_active else "offline"


def _build_server_metrics(seed_value: int, available: bool, traffic_enabled: bool) -> dict[str, float | None]:
    _ = (seed_value, available, traffic_enabled)
    return _build_unavailable_server_metrics()


def _server_metrics_note(
    server: Server | None,
    status: str,
    runtime_metrics: dict[str, object] | None = None,
) -> str:
    if server is None:
        return "Нет данных."
    if status == "online" and runtime_metrics:
        parts: list[str] = []
        uptime_label = _format_uptime_label(_metric_int(runtime_metrics, "uptime_seconds"))
        if uptime_label:
            parts.append(f"Uptime: {uptime_label}")
        load_1 = _metric_float(runtime_metrics, "load_1m")
        load_5 = _metric_float(runtime_metrics, "load_5m")
        if load_1 is not None and load_5 is not None:
            parts.append(f"Load: {load_1:.2f} / {load_5:.2f}")
        iface = runtime_metrics.get("network_interface")
        rx_label = _format_bytes_label(_metric_int(runtime_metrics, "network_rx_bytes"))
        tx_label = _format_bytes_label(_metric_int(runtime_metrics, "network_tx_bytes"))
        if isinstance(iface, str) and iface.strip() and (rx_label or tx_label):
            parts.append(
                f"Сеть {iface.strip()}: Получено {rx_label or 'нет данных'} / Отправлено {tx_label or 'нет данных'}"
            )
        if parts:
            return " · ".join(parts)
    if status == "online" and server.last_seen_at is not None:
        return "Подключение проверено; реальные метрики сервера пока не собираются."
    if status == "offline":
        return "Проверка не удалась."
    if status == "excluded":
        return "Сервер исключён из окружения панели."
    return "Нет данных."


def _build_server_card(
    *,
    key: str,
    name: str,
    host: str,
    available: bool,
    seed_value: int,
    traffic_enabled: bool,
    selected_id: int | None = None,
    options: list[Server] | None = None,
    runtime_response: dict[str, object] | None = None,
    metrics_note: str | None = None,
) -> ServerCardView:
    metrics = _build_server_metrics_from_runtime(runtime_response) if runtime_response else _build_server_metrics(seed_value, available, traffic_enabled)
    return ServerCardView(
        key=key,
        name=name,
        host=host,
        available=available,
        status="online" if available else "offline",
        metrics_note=metrics_note or ("Подключение проверено; реальные метрики сервера пока не собираются." if available else "Нет данных."),
        cpu_percent=float(metrics["cpu_percent"]),
        ram_percent=float(metrics["ram_percent"]),
        disk_used_gb=float(metrics["disk_used_gb"]),
        disk_total_gb=float(metrics["disk_total_gb"]),
        disk_percent=float(metrics["disk_percent"]),
        traffic_mbps=float(metrics["traffic_mbps"]) if metrics["traffic_mbps"] is not None else None,
        selected_id=selected_id,
        options=serialize_server_options(options or []),
    )


def _build_panel_server_card(*, host: str) -> ServerCardView:
    metrics = _build_panel_server_metrics()
    return ServerCardView(
        key="panel",
        name="Panel Server",
        host=host,
        available=True,
        status="online",
        metrics_note="Локальные метрики panel-host.",
        cpu_percent=float(metrics["cpu_percent"]),
        ram_percent=float(metrics["ram_percent"]),
        disk_used_gb=float(metrics["disk_used_gb"]),
        disk_total_gb=float(metrics["disk_total_gb"]),
        disk_percent=float(metrics["disk_percent"]),
        traffic_mbps=None,
        selected_id=None,
        options=[],
    )


def _collect_live_server_runtime_map(db: Session, servers: list[Server]) -> dict[int, dict[str, object]]:
    runtime_map: dict[int, dict[str, object]] = {}
    for server in servers:
        if _server_status(server) != "online" or server.is_excluded:
            continue
        response = _get_live_server_runtime_response_cached(db, server)
        if response is None:
            continue
        runtime_map[server.id] = response
    return runtime_map


def _get_live_server_runtime_response_cached(
    db: Session,
    server: Server,
    *,
    force: bool = False,
) -> dict[str, object] | None:
    cached = _SERVER_RUNTIME_CACHE.get(server.id)
    if (
        not force
        and cached is not None
        and (time.time() - cached[0]) < SERVER_RUNTIME_CACHE_TTL_SECONDS
    ):
        return dict(cached[1])
    try:
        response = _run_agent_executor_quiet(
            _build_server_executor_payload(action="verify_server_runtime", server=server)
        )
    except ServerOperationUnavailableError:
        return None
    _sync_server_runtime_peer_stats(db, server, response)
    _SERVER_RUNTIME_CACHE[server.id] = (time.time(), dict(response))
    return response


def get_live_server_metrics_map(
    db: Session,
    actor: User,
    server_ids: list[int],
) -> dict[int, dict[str, object]]:
    require_admin(actor)
    unique_ids = []
    seen: set[int] = set()
    for server_id in server_ids:
        if server_id in seen:
            continue
        seen.add(server_id)
        unique_ids.append(server_id)
    if not unique_ids:
        return {}
    servers = db.execute(select(Server).where(Server.id.in_(unique_ids))).scalars().all()
    result: dict[int, dict[str, object]] = {}
    for server in servers:
        status = _server_status(server)
        runtime_response = _get_live_server_runtime_response_cached(db, server) if status == "online" and not server.is_excluded else None
        metrics = _build_server_metrics_from_runtime(runtime_response) if runtime_response else _build_unavailable_server_metrics()
        result[server.id] = {
            "server_id": server.id,
            "status": status,
            "metrics_note": _server_metrics_note(server, status, _runtime_metrics_payload(runtime_response) if runtime_response else None),
            "cpu_percent": float(metrics["cpu_percent"]),
            "ram_percent": float(metrics["ram_percent"]),
            "disk_used_gb": float(metrics["disk_used_gb"]),
            "disk_total_gb": float(metrics["disk_total_gb"]),
            "disk_percent": float(metrics["disk_percent"]),
            "traffic_mbps": float(metrics["traffic_mbps"]) if metrics["traffic_mbps"] is not None else None,
        }
    return result


def _select_server(servers: list[Server], selected_id: int | None) -> Server | None:
    if not servers:
        return None
    if selected_id is not None:
        for server in servers:
            if server.id == selected_id:
                return server
    return servers[0]


def get_admin_page_data(
    db: Session,
    actor: User,
    tic_server_id: int | None = None,
    tak_server_id: int | None = None,
    client_scope: str = "all",
    active_tab: str = "overview",
    settings_view: str = "basic",
    filter_scope: str = "all",
    filter_kind: FilterKind = FilterKind.EXCLUSION,
) -> AdminPageView:
    purge_expired_peers(db)
    require_admin(actor)
    ensure_default_settings(db)
    active_tab = active_tab if active_tab in {"overview", "settings", "clients", "access"} else "overview"
    settings_view = settings_view if settings_view in {
        "basic",
        "logs",
        "updates",
        "backups",
        "shared_peers",
        "filters",
        "block_filters",
    } else "basic"

    tic_servers = db.execute(
        select(Server).where(Server.server_type == ServerType.TIC).order_by(Server.id.asc())
    ).scalars().all()
    tak_servers = db.execute(
        select(Server).where(Server.server_type == ServerType.TAK).order_by(Server.id.asc())
    ).scalars().all()
    selected_tic = _select_server(tic_servers, tic_server_id)
    selected_tak = _select_server(tak_servers, tak_server_id)
    need_version_summary = False
    panel_update_summary = PanelUpdateCheckView(**_check_panel_updates_summary(db)) if need_version_summary else None
    agent_update_summaries = (
        [
            _server_agent_update_summary(db, server)
            for server in [selected_tic, selected_tak]
            if server is not None
        ]
        if need_version_summary
        else []
    )

    interfaces: list[Interface] = []
    available_interfaces: list[Interface] = []
    if active_tab == "overview":
        interfaces = db.execute(
            select(Interface)
            .options(joinedload(Interface.peers), joinedload(Interface.user), joinedload(Interface.tic_server))
            .order_by(Interface.created_at.asc(), Interface.id.asc())
        ).unique().scalars().all()
        available_interfaces = [interface for interface in interfaces if interface.is_pending_owner]
    elif active_tab == "clients":
        available_interfaces = db.execute(
            select(Interface)
            .options(joinedload(Interface.user))
            .where(Interface.user_id.is_(None))
            .order_by(Interface.created_at.asc(), Interface.id.asc())
        ).unique().scalars().all()

    clients: list[User] = []
    if active_tab == "clients":
        ensure_users_have_resources(db)
        clients = db.execute(
            select(User)
            .options(joinedload(User.interfaces), joinedload(User.resources), joinedload(User.contact_link_record))
            .order_by(User.role.asc(), User.created_at.asc(), User.id.asc())
        ).unique().scalars().all()
        if client_scope == "without_interfaces":
            clients = [user for user in clients if user.role == UserRole.ADMIN or len(user.interfaces) == 0]

    filters = get_admin_filters_view(db, scope_filter=filter_scope, kind=filter_kind) if active_tab == "settings" and settings_view in {"filters", "block_filters"} else []

    access_users: list[User] = []
    access_users_without_expiry: list[User] = []
    if active_tab == "access":
        access_candidates = db.execute(
            select(User)
            .where(User.role != UserRole.ADMIN)
            .order_by(User.created_at.asc(), User.id.asc())
        ).scalars().all()
        access_users = [
            user
            for user in access_candidates
            if normalize_utc_datetime(user.expires_at) is not None
        ]
        access_users.sort(key=lambda user: normalize_utc_datetime(user.expires_at) or datetime.max.replace(tzinfo=UTC))
        access_users_without_expiry = [
            user
            for user in access_candidates
            if normalize_utc_datetime(user.expires_at) is None
        ]
        access_users_without_expiry.sort(key=lambda user: (user.display_name or user.login).lower())

    show_remote_live_metrics = active_tab == "overview"
    live_runtime_map: dict[int, dict[str, object]] = {}
    panel_server = _build_panel_server_card(host=urlparse(settings.panel_public_base_url).hostname or "panel-host")
    tic_card = _build_server_card(
        key="tic",
        name=selected_tic.name if selected_tic else "Tic Server",
        host=selected_tic.host if selected_tic else "not-configured",
        available=selected_tic.is_active if selected_tic and _server_status(selected_tic) == "online" else False,
        seed_value=selected_tic.id if selected_tic else 101,
        traffic_enabled=True,
        selected_id=selected_tic.id if selected_tic else None,
        options=tic_servers,
        runtime_response=live_runtime_map.get(selected_tic.id) if selected_tic else None,
    )
    tic_card.status = _server_status(selected_tic)
    tic_card.last_seen_at = selected_tic.last_seen_at if selected_tic else None
    tic_card.metrics_note = _server_metrics_note(
        selected_tic,
        tic_card.status,
        _runtime_metrics_payload(live_runtime_map.get(selected_tic.id, {})) if selected_tic else None,
    )
    tak_card = _build_server_card(
        key="tak",
        name=selected_tak.name if selected_tak else "Tak Server",
        host=selected_tak.host if selected_tak else "not-configured",
        available=selected_tak.is_active if selected_tak and _server_status(selected_tak) == "online" else False,
        seed_value=selected_tak.id if selected_tak else 202,
        traffic_enabled=True,
        selected_id=selected_tak.id if selected_tak else None,
        options=tak_servers,
        runtime_response=live_runtime_map.get(selected_tak.id) if selected_tak else None,
    )
    tak_card.status = _server_status(selected_tak)
    tak_card.last_seen_at = selected_tak.last_seen_at if selected_tak else None
    tak_card.metrics_note = _server_metrics_note(
        selected_tak,
        tak_card.status,
        _runtime_metrics_payload(live_runtime_map.get(selected_tak.id, {})) if selected_tak else None,
    )
    backup_status = "ok"
    try:
        path = backup_storage_path(db)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".nelomai-admin-beta-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception:
        backup_status = "error"
    latest_backup_status = "ok" if _latest_completed_full_backup(db) is not None else "warning"
    runtime_status = "error" if not settings.peer_agent_command else "ok"
    tak_tunnel_status = "ok"
    for server in [*tic_servers, *tak_servers]:
        if not server.is_active and runtime_status == "ok":
            runtime_status = "warning"
    via_tak_fallback_exists = db.execute(
        select(Interface.id)
        .where(
            Interface.route_mode == RouteMode.VIA_TAK,
            Interface.tak_tunnel_fallback_active.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()
    if via_tak_fallback_exists is not None:
        tak_tunnel_status = "warning"
    beta_readiness = (
        _build_beta_readiness_summary(
            backup_status=backup_status,
            latest_backup_status=latest_backup_status,
            runtime_status=runtime_status,
            tak_tunnel_status=tak_tunnel_status,
        )
        if active_tab in {"overview", "settings"}
        else None
    )

    return serialize_admin_page(
        panel_server=panel_server,
        tic_server=tic_card,
        tak_server=tak_card,
        show_remote_live_metrics=show_remote_live_metrics,
        beta_readiness=beta_readiness,
        panel_update_summary=panel_update_summary,
        agent_update_summaries=agent_update_summaries,
        interfaces=interfaces,
        available_interfaces=available_interfaces,
        available_tic_servers=tic_servers,
        available_tak_servers=tak_servers,
        settings=get_basic_settings(db),
        filters=filters,
        clients=clients,
        access_users=access_users,
        access_users_without_expiry=access_users_without_expiry,
    )


def get_servers_page_data(
    db: Session,
    actor: User,
    view: str = "servers",
    bucket: str = "active",
    server_type: str = "all",
    location: str = "",
    sort: str = "load_desc",
    selected_server_id: int | None = None,
    selected_bootstrap_task_id: int | None = None,
) -> ServersPageView:
    purge_expired_peers(db)
    require_admin(actor)
    view = view if view in {"servers", "tunnels"} else "servers"
    bucket = bucket if bucket in {"active", "excluded"} else "active"
    server_type = server_type if server_type in {"all", "tic", "tak", "storage"} else "all"
    location = location.strip()
    sort = sort if sort in {"load_desc", "load_asc"} else "load_desc"
    servers = db.execute(
        select(Server)
        .options(joinedload(Server.tic_interfaces).joinedload(Interface.peers), joinedload(Server.tak_interfaces))
        .order_by(Server.id.asc())
    ).unique().scalars().all()
    bootstrap_tasks = db.execute(
        select(ServerBootstrapTask)
        .where(ServerBootstrapTask.status.in_(["running", "input_required"]))
        .order_by(ServerBootstrapTask.updated_at.desc(), ServerBootstrapTask.id.desc())
    ).scalars().all()
    live_runtime_map: dict[int, dict[str, object]] = {}

    active_servers: list[ServerListItemView] = []
    excluded_servers: list[ServerListItemView] = []
    pending_bootstrap_tasks: list[ServerBootstrapListItemView] = []
    tak_tunnel_pairs: list[TakTunnelPairStateView] = []
    backup_status = "ok"
    try:
        path = backup_storage_path(db)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".nelomai-servers-beta-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception:
        backup_status = "error"
    latest_backup_status = "ok" if _latest_completed_full_backup(db) is not None else "warning"
    runtime_status = "error" if not settings.peer_agent_command else "ok"
    tak_tunnel_status = "ok"
    bootstrap_status_labels = {
        "bootstrapping": "Настраивается",
        "confirmation_required": "Ожидает подтверждение",
        "failed": "Ошибка",
    }
    for task in bootstrap_tasks:
        if server_type != "all" and task.server_type.value != server_type:
            continue
        if location:
            if task.server_type == ServerType.TIC:
                if (_tic_region_label(task.tic_region) or "").lower() != location.lower():
                    continue
            elif task.server_type == ServerType.TAK:
                if location.lower() not in (task.tak_country or "").lower():
                    continue
            else:
                continue
        panel_job = db.get(PanelJob, task.panel_job_id) if task.panel_job_id else None
        status_value = "confirmation_required" if task.status == "input_required" else ("bootstrapping" if task.status == "running" else task.status)
        pending_bootstrap_tasks.append(
            ServerBootstrapListItemView(
                id=task.id,
                name=task.server_name,
                host=task.host,
                server_type=task.server_type.value,
                tic_region=task.tic_region.value if task.tic_region is not None else None,
                tak_country=task.tak_country,
                ssh_port=task.ssh_port,
                status=status_value,
                status_label=bootstrap_status_labels.get(status_value, status_value),
                logs=_task_logs(task),
                last_error=task.last_error,
                panel_job_id=panel_job.id if panel_job else None,
                panel_job_status=panel_job.status.value if panel_job else None,
                panel_job_stage=panel_job.current_stage if panel_job else None,
                panel_job_progress=max(0, min(100, int(panel_job.progress_percent or 0))) if panel_job else None,
                bootstrap_command_profile=task.bootstrap_command_profile,
                bootstrap_packages=_task_bootstrap_package_list(task.bootstrap_packages_json),
                bootstrap_safe_init_packages=_task_bootstrap_package_list(task.bootstrap_safe_init_packages_json),
                bootstrap_full_only_packages=_task_bootstrap_package_list(task.bootstrap_full_only_packages_json),
                bootstrap_snapshot=_task_bootstrap_snapshot(task),
                bootstrap_pending_command=_task_pending_bootstrap_command(task),
                bootstrap_steps=_task_bootstrap_steps(task),
                bootstrap_last_step_error=_task_bootstrap_last_step_error(task),
            )
        )
    for server in servers:
        server_kind = server.server_type.value
        if server_type != "all" and server_kind != server_type:
            continue
        location_label = _server_location_label(server)
        if location:
            if server.server_type == ServerType.TIC:
                if (location_label or "").lower() != location.lower():
                    continue
            elif server.server_type == ServerType.TAK:
                if location.lower() not in (location_label or "").lower():
                    continue
            else:
                continue
        interface_count = len(server.tic_interfaces)
        tak_fallback_interfaces = sorted(
            (interface.name for interface in server.tic_interfaces if interface.tak_tunnel_fallback_active),
            key=str.lower,
        )
        tak_recovered_interfaces = sorted(
            (
                interface.name
                for interface in server.tic_interfaces
                if not interface.tak_tunnel_fallback_active and interface.tak_tunnel_last_status == "recovered"
            ),
            key=str.lower,
        )
        endpoint_count = len(server.tak_interfaces) if server.server_type == ServerType.TAK else len(server.tic_interfaces)
        peer_count = sum(len(interface.peers) for interface in server.tic_interfaces) if server.server_type == ServerType.TIC else 0
        status = _server_status(server)
        available = status == "online"
        runtime_response = live_runtime_map.get(server.id)
        metrics = _build_server_metrics_from_runtime(runtime_response) if runtime_response else _build_server_metrics(server.id + interface_count + endpoint_count, available, server.server_type in {ServerType.TIC, ServerType.TAK})
        item = ServerListItemView(
            id=server.id,
            name=server.name,
            host=server.host,
            server_type=server_kind,
            tic_region=server.tic_region.value if server.tic_region is not None else None,
            tic_region_label=_tic_region_label(server.tic_region),
            tak_country=server.tak_country,
            location_label=location_label,
            available=available,
            status=status,
            last_seen_at=server.last_seen_at,
            metrics_note=_server_metrics_note(server, status, _runtime_metrics_payload(runtime_response) if runtime_response else None),
            ssh_port=server.ssh_port,
            cpu_percent=float(metrics["cpu_percent"]),
            ram_percent=float(metrics["ram_percent"]),
            disk_used_gb=float(metrics["disk_used_gb"]),
            disk_total_gb=float(metrics["disk_total_gb"]),
            disk_percent=float(metrics["disk_percent"]),
            traffic_mbps=float(metrics["traffic_mbps"]) if metrics["traffic_mbps"] is not None else None,
            interface_count=interface_count,
            endpoint_count=endpoint_count,
            peer_count=peer_count,
            is_excluded=server.is_excluded,
            owner_interface_names=sorted(interface.name for interface in server.tic_interfaces),
            endpoint_interface_names=sorted(interface.name for interface in server.tak_interfaces),
            tak_fallback_interface_count=len(tak_fallback_interfaces),
            tak_fallback_interface_names=tak_fallback_interfaces,
            tak_recovered_interface_count=len(tak_recovered_interfaces),
            tak_recovered_interface_names=tak_recovered_interfaces,
            agent_update_status=None,
            agent_version=None,
            contract_version=None,
            current_version=None,
            latest_version=None,
            update_available=False,
        )
        if server.is_excluded:
            excluded_servers.append(item)
        else:
            active_servers.append(item)
        if server.server_type in {ServerType.TIC, ServerType.TAK} and not server.is_active:
            runtime_status = "warning" if runtime_status == "ok" else runtime_status
        if item.tak_fallback_interface_count > 0:
            tak_tunnel_status = "warning"

    pair_interfaces: dict[tuple[int, int], list[Interface]] = {}
    for server in servers:
        for interface in server.tic_interfaces:
            if interface.route_mode == RouteMode.VIA_TAK and interface.tak_server_id is not None:
                pair_interfaces.setdefault((interface.tic_server_id, interface.tak_server_id), []).append(interface)
    repair_state = _load_tak_tunnel_repair_state(db)
    now = utc_now()
    for (tic_id, tak_id), items in sorted(
        pair_interfaces.items(),
        key=lambda item: (item[1][0].tic_server.name.lower(), item[1][0].tak_server.name.lower()),
    ):
        sample = items[0]
        state = dict(repair_state.get(_tak_tunnel_pair_key(tic_id, tak_id)) or {})
        failure_count = int(state.get("failure_count") or 0)
        cooldown_until = _tak_tunnel_parse_datetime(state.get("cooldown_until"))
        manual_attention_required = bool(state.get("manual_attention_required"))
        fallback_count = sum(1 for interface in items if interface.tak_tunnel_fallback_active)
        recovered_count = sum(
            1 for interface in items if interface.tak_tunnel_last_status == "recovered" and not interface.tak_tunnel_fallback_active
        )
        if manual_attention_required:
            status = "manual_attention_required"
        elif cooldown_until is not None and cooldown_until > now:
            status = "cooldown"
        elif fallback_count > 0:
            status = "fallback"
        elif recovered_count > 0:
            status = "recovered"
        else:
            status = "active"
        tak_tunnel_pairs.append(
            TakTunnelPairStateView(
                tic_server_id=tic_id,
                tic_server_name=sample.tic_server.name,
                tak_server_id=tak_id,
                tak_server_name=sample.tak_server.name if sample.tak_server is not None else f"Tak {tak_id}",
                pair_label=f"{sample.tic_server.name} → {sample.tak_server.name if sample.tak_server is not None else tak_id}",
                status=status,
                status_label=_tak_tunnel_status_label_ui(status),
                fallback_interface_count=fallback_count,
                recovered_interface_count=recovered_count,
                failure_count=failure_count,
                cooldown_until=cooldown_until,
                manual_attention_required=manual_attention_required,
                diagnostics_url=f"/admin/diagnostics?focused_tic_server_id={tic_id}&focused_tak_server_id={tak_id}#check-tak_tunnels",
            )
        )

    reverse = sort != "load_asc"
    active_servers.sort(key=lambda item: (item.interface_count, item.endpoint_count, item.peer_count, item.id), reverse=reverse)
    excluded_servers.sort(key=lambda item: (item.interface_count, item.endpoint_count, item.peer_count, item.id), reverse=reverse)
    visible_servers = excluded_servers if bucket == "excluded" else active_servers
    selected_server = next((item for item in visible_servers if item.id == selected_server_id), None)
    selected_server_agent_update: ServerAgentUpdateView | None = None
    detail = None
    if selected_server is not None:
        source = next(server for server in servers if server.id == selected_server.id)
        selected_server_agent_update = _server_agent_update_summary(db, source)
        detail = ServerDetailView(
            id=selected_server.id,
            name=selected_server.name,
            host=selected_server.host,
            server_type=selected_server.server_type,
            tic_region=source.tic_region.value if source.tic_region is not None else None,
            tic_region_label=_tic_region_label(source.tic_region),
            tak_country=source.tak_country,
            location_label=_server_location_label(source),
            status=selected_server.status,
            last_seen_at=source.last_seen_at,
            metrics_note=selected_server.metrics_note,
            ssh_port=selected_server.ssh_port,
            ssh_login=source.ssh_login,
            cpu_percent=selected_server.cpu_percent,
            ram_percent=selected_server.ram_percent,
            disk_used_gb=selected_server.disk_used_gb,
            disk_total_gb=selected_server.disk_total_gb,
            disk_percent=selected_server.disk_percent,
            traffic_mbps=selected_server.traffic_mbps,
            interface_count=selected_server.interface_count,
            endpoint_count=selected_server.endpoint_count,
            peer_count=selected_server.peer_count,
            is_excluded=selected_server.is_excluded,
            owner_interface_names=selected_server.owner_interface_names,
            endpoint_interface_names=selected_server.endpoint_interface_names,
            tak_fallback_interface_count=selected_server.tak_fallback_interface_count,
            tak_fallback_interface_names=selected_server.tak_fallback_interface_names,
            tak_recovered_interface_count=selected_server.tak_recovered_interface_count,
            tak_recovered_interface_names=selected_server.tak_recovered_interface_names,
        )
    beta_readiness = _build_beta_readiness_summary(
        backup_status=backup_status,
        latest_backup_status=latest_backup_status,
        runtime_status=runtime_status,
        tak_tunnel_status=tak_tunnel_status,
    )
    return serialize_servers_page(
        active_servers,
        excluded_servers,
        pending_bootstrap_tasks,
        tak_tunnel_pairs,
        beta_readiness,
        selected_server_agent_update,
        view,
        bucket,
        selected_type=server_type,
        selected_location=location,
        selected_sort=sort,
        selected_server=detail,
        selected_bootstrap_task_id=selected_bootstrap_task_id,
    )


def get_server_by_id(db: Session, server_id: int) -> Server:
    server = db.execute(select(Server).where(Server.id == server_id)).scalar_one_or_none()
    if server is None:
        raise EntityNotFoundError("Server not found")
    return server


def repair_tak_tunnel_pair(db: Session, actor: User, *, tic_server_id: int, tak_server_id: int) -> None:
    require_admin(actor)
    tic_server = get_server_by_id(db, tic_server_id)
    tak_server = get_server_by_id(db, tak_server_id)
    if tic_server.server_type != ServerType.TIC:
        raise EntityNotFoundError("Tic server not found")
    if tak_server.server_type != ServerType.TAK:
        raise EntityNotFoundError("Tak server not found")
    repair_state = _load_tak_tunnel_repair_state(db)
    pair_state = dict(repair_state.get(_tak_tunnel_pair_key(tic_server_id, tak_server_id)) or {})
    failure_count_before_repair = int(pair_state.get("failure_count") or 0)
    manual_attention_before_repair = bool(pair_state.get("manual_attention_required"))
    tunnel_id, repair_strategy = _repair_tak_tunnel_transport(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        actor_user_id=actor.id,
        allow_reprovision=True,
    )
    if _tak_tunnel_register_success(repair_state, tic_server_id=tic_server_id, tak_server_id=tak_server_id, now=utc_now()):
        _save_tak_tunnel_repair_state(db, repair_state)
    write_audit_log(
        db,
        event_type="tak_tunnels.manual_repaired",
        severity="info",
        message=f"Tak tunnel manually repaired: tic={tic_server.name}, tak={tak_server.name}",
        message_ru=f"Туннель Tic/Tak восстановлен вручную: {tic_server.name} → {tak_server.name}",
        actor_user_id=actor.id,
        server_id=tic_server.id,
        details=json.dumps(
            {
                "tic_server_id": tic_server.id,
                "tic_server_name": tic_server.name,
                "tak_server_id": tak_server.id,
                "tak_server_name": tak_server.name,
                "tunnel_id": tunnel_id,
                "repair_strategy": repair_strategy,
                "failure_count_before_repair": failure_count_before_repair,
                "manual_attention_before_repair": manual_attention_before_repair,
                "interface_names": sorted(
                    interface.name
                    for interface in db.execute(
                        select(Interface).where(
                            Interface.tic_server_id == tic_server.id,
                            Interface.tak_server_id == tak_server.id,
                            Interface.route_mode == RouteMode.VIA_TAK,
                        )
                    ).scalars().all()
                ),
            },
            ensure_ascii=False,
        ),
        commit=False,
    )
    _reconcile_tak_tunnel_routes(db)


def clear_tak_tunnel_backoff(db: Session, actor: User, *, tic_server_id: int, tak_server_id: int) -> None:
    require_admin(actor)
    tic_server = get_server_by_id(db, tic_server_id)
    tak_server = get_server_by_id(db, tak_server_id)
    if tic_server.server_type != ServerType.TIC:
        raise EntityNotFoundError("Tic server not found")
    if tak_server.server_type != ServerType.TAK:
        raise EntityNotFoundError("Tak server not found")
    repair_state = _load_tak_tunnel_repair_state(db)
    pair_key = _tak_tunnel_pair_key(tic_server_id, tak_server_id)
    pair_state = dict(repair_state.get(pair_key) or {})
    failure_count_before_clear = int(pair_state.get("failure_count") or 0)
    manual_attention_before_clear = bool(pair_state.get("manual_attention_required"))
    cooldown_until_before_clear = str(pair_state.get("cooldown_until") or "")
    if pair_key in repair_state:
        repair_state.pop(pair_key, None)
        _save_tak_tunnel_repair_state(db, repair_state)
    write_audit_log(
        db,
        event_type="tak_tunnels.backoff_cleared",
        severity="info",
        message=f"Tak tunnel backoff cleared: tic={tic_server.name}, tak={tak_server.name}",
        message_ru=f"Backoff туннеля Tic/Tak сброшен: {tic_server.name} → {tak_server.name}",
        actor_user_id=actor.id,
        server_id=tic_server.id,
        details=json.dumps(
            {
                "tic_server_id": tic_server.id,
                "tic_server_name": tic_server.name,
                "tak_server_id": tak_server.id,
                "tak_server_name": tak_server.name,
                "failure_count_before_clear": failure_count_before_clear,
                "manual_attention_before_clear": manual_attention_before_clear,
                "cooldown_until_before_clear": cooldown_until_before_clear,
                "interface_names": sorted(
                    interface.name
                    for interface in db.execute(
                        select(Interface).where(
                            Interface.tic_server_id == tic_server.id,
                            Interface.tak_server_id == tak_server.id,
                            Interface.route_mode == RouteMode.VIA_TAK,
                        )
                    ).scalars().all()
                ),
            },
            ensure_ascii=False,
        ),
        commit=False,
    )
    db.commit()


def get_server_bootstrap_task(db: Session, task_id: int) -> ServerBootstrapTask:
    task = db.execute(select(ServerBootstrapTask).where(ServerBootstrapTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise EntityNotFoundError("Server bootstrap task not found")
    return task


def _task_logs(task: ServerBootstrapTask) -> list[str]:
    try:
        value = json.loads(task.logs_json or "[]")
    except json.JSONDecodeError:
        value = []
    return [str(item) for item in value]


def _set_task_logs(task: ServerBootstrapTask, lines: list[str]) -> None:
    task.logs_json = json.dumps(lines, ensure_ascii=False)


def _task_bootstrap_snapshot(task: ServerBootstrapTask) -> ServerBootstrapSnapshotView | None:
    if not task.bootstrap_snapshot_json:
        return None
    try:
        value = json.loads(task.bootstrap_snapshot_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    pending_raw = value.get("pending_input")
    pending = None
    if isinstance(pending_raw, dict):
        pending = ServerBootstrapPendingInputView(
            key=str(pending_raw.get("key")) if pending_raw.get("key") is not None else None,
            kind=str(pending_raw.get("kind")) if pending_raw.get("kind") is not None else None,
            prompt=str(pending_raw.get("prompt")) if pending_raw.get("prompt") is not None else None,
            step_index=int(pending_raw.get("step_index")) if pending_raw.get("step_index") is not None else None,
        )
    return ServerBootstrapSnapshotView(
        mode=str(value.get("mode")) if value.get("mode") is not None else None,
        transport=str(value.get("transport")) if value.get("transport") is not None else None,
        applied=bool(value.get("applied", False)),
        planned=bool(value.get("planned", True)),
        command_count=int(value.get("command_count", 0) or 0),
        executed_step_count=int(value.get("executed_step_count", 0) or 0),
        current_step_index=int(value.get("current_step_index")) if value.get("current_step_index") is not None else None,
        current_step_status=str(value.get("current_step_status")) if value.get("current_step_status") is not None else None,
        resume_from_step=int(value.get("resume_from_step", 1) or 1),
        waiting_for_input=bool(value.get("waiting_for_input", False)),
        pending_input=pending,
    )


def _task_pending_bootstrap_command(task: ServerBootstrapTask) -> str | None:
    if task.status != "input_required" or not task.input_key or not re.fullmatch(r"bootstrap_step_\d+_confirm", task.input_key):
        return None
    wait_line = next(
        (line for line in reversed(_task_logs(task)) if re.match(r"^WAIT step \d+: ", line)),
        None,
    )
    if not wait_line:
        return None
    match = re.match(r"^WAIT step \d+: (.+)$", wait_line)
    if not match:
        return None
    value = str(match.group(1) or "").strip()
    return value or None


def _task_bootstrap_steps(task: ServerBootstrapTask, limit: int = 4) -> list[ServerBootstrapStepView]:
    status_labels = {
        "completed": "Завершён",
        "planned": "Запланирован",
        "input_required": "Ожидает ввод",
        "confirmation_required": "Требует подтверждение",
        "failed": "Ошибка",
        "error": "Ошибка",
    }
    if not task.bootstrap_execution_json:
        return []
    try:
        value = json.loads(task.bootstrap_execution_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, dict):
        return []
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list):
        return []
    steps: list[ServerBootstrapStepView] = []
    for raw_step in raw_steps[-limit:]:
        if not isinstance(raw_step, dict):
            continue
        command = str(raw_step.get("command") or "").strip()
        if not command:
            continue
        steps.append(
            ServerBootstrapStepView(
                index=int(raw_step.get("index", 0) or 0),
                status=str(raw_step.get("status") or "unknown"),
                status_label=status_labels.get(str(raw_step.get("status") or "unknown"), "Неизвестно"),
                command=command,
                note=str(raw_step.get("note")) if raw_step.get("note") is not None else None,
                stdout=str(raw_step.get("stdout")).strip() if raw_step.get("stdout") not in {None, ""} else None,
                stderr=str(raw_step.get("stderr")).strip() if raw_step.get("stderr") not in {None, ""} else None,
            )
        )
    return steps


def _task_bootstrap_last_step_error(task: ServerBootstrapTask) -> str | None:
    for step in reversed(_task_bootstrap_steps(task, limit=20)):
        if step.status in {"failed", "error"}:
            return step.stderr or step.note or step.command
        if step.stderr:
            return step.stderr
    return None


def _set_task_bootstrap_snapshot(task: ServerBootstrapTask, snapshot: dict[str, object] | None) -> None:
    task.bootstrap_snapshot_json = json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None


def _set_task_bootstrap_execution(task: ServerBootstrapTask, execution: dict[str, object] | None) -> None:
    task.bootstrap_execution_json = json.dumps(execution, ensure_ascii=False) if execution is not None else None


def _set_task_bootstrap_profile(task: ServerBootstrapTask, bootstrap_plan: dict[str, object] | None) -> None:
    value = bootstrap_plan if isinstance(bootstrap_plan, dict) else {}
    task.bootstrap_command_profile = str(value.get("command_profile") or "").strip() or None
    packages = value.get("packages")
    safe_init_packages = value.get("safe_init_packages")
    full_only_packages = value.get("full_only_packages")
    task.bootstrap_packages_json = json.dumps(packages, ensure_ascii=False) if isinstance(packages, list) else None
    task.bootstrap_safe_init_packages_json = json.dumps(safe_init_packages, ensure_ascii=False) if isinstance(safe_init_packages, list) else None
    task.bootstrap_full_only_packages_json = json.dumps(full_only_packages, ensure_ascii=False) if isinstance(full_only_packages, list) else None


def _task_bootstrap_package_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _mirror_server_bootstrap_job(db: Session, task: ServerBootstrapTask) -> None:
    if not task.panel_job_id:
        return
    job = db.get(PanelJob, task.panel_job_id)
    if job is None:
        return

    now = utc_now()
    task_logs = _task_logs(task)
    job_logs = _job_logs(job)
    for line in task_logs:
        if line not in job_logs:
            job_logs.append(line)
    _set_job_logs(job, job_logs)

    if task.status == "input_required":
        job.status = PanelJobStatus.RUNNING
        job.started_at = job.started_at or now
        job.progress_percent = max(job.progress_percent or 0, 55)
        job.current_stage = "Ожидает ввод администратора"
        job.error_message = None
        job.completed_at = None
    elif task.status == "running":
        job.status = PanelJobStatus.RUNNING
        job.started_at = job.started_at or now
        job.progress_percent = max(job.progress_percent or 0, 35)
        job.current_stage = "Настройка сервера выполняется"
        job.error_message = None
        job.completed_at = None
    elif task.status == "completed":
        job.status = PanelJobStatus.COMPLETED
        job.started_at = job.started_at or now
        job.progress_percent = 100
        job.current_stage = "Сервер добавлен и готов к проверке"
        job.error_message = None
        job.completed_at = job.completed_at or now
    elif task.status == "failed":
        job.status = PanelJobStatus.FAILED
        job.started_at = job.started_at or now
        job.progress_percent = 100
        job.current_stage = "Ошибка добавления сервера"
        job.error_message = task.last_error or "Bootstrap сервера завершился ошибкой."
        job.completed_at = job.completed_at or now

    job.updated_at = now
    db.add(job)


def _humanize_bootstrap_error(message: str) -> str:
    normalized = message.lower()
    if "not configured" in normalized:
        return "Node-agent для настройки серверов не настроен в панели."
    if "timed out" in normalized or "timeout" in normalized:
        return "Сервер не отвечает: истекло время ожидания ответа Node-agent."
    if "failed to start" in normalized:
        return "Не удалось запустить локальный bridge для подключения к серверу."
    if "invalid json" in normalized:
        return "Node-agent вернул некорректный ответ, настройка остановлена."
    if "ssh" in normalized:
        return f"Ошибка SSH при настройке сервера: {message}"
    return message


def _humanize_bootstrap_error_v2(message: str) -> str:
    raw = message.strip()
    error_code = ""
    details = raw
    match = re.match(r"^\[([a-z0-9_]+)\]\s*(.*)$", raw, re.IGNORECASE)
    if match:
        error_code = match.group(1).lower()
        details = match.group(2).strip() or raw
    normalized = raw.lower()
    if error_code == "ssh_connection_refused":
        return f"Сервер отклонил SSH-подключение: {details}"
    if error_code == "ssh_timeout":
        return f"Истёк таймаут SSH-подключения: {details}"
    if error_code == "ssh_auth_failed":
        return f"SSH-аутентификация не прошла: {details}"
    if error_code == "ssh_host_key_mismatch":
        return f"Проверка SSH host key не прошла: {details}"
    if error_code == "ssh_host_unreachable":
        return f"Сервер недоступен по SSH: {details}"
    if error_code == "sshpass_missing":
        return f"На хосте агента отсутствует sshpass для парольного SSH: {details}"
    if error_code == "ssh_execution_blocked":
        return "SSH bootstrap заблокирован в настройках агента. Включите NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH=1."
    if error_code == "ssh_transport_failed":
        return f"Транспорт SSH завершился ошибкой: {details}"
    if error_code == "ssh_command_failed":
        return f"Удалённая SSH-команда завершилась ошибкой: {details}"
    if error_code == "local_command_failed":
        return f"Локальная bootstrap-команда завершилась ошибкой: {details}"
    if "not configured" in normalized:
        return "Node-agent для настройки серверов не настроен в панели."
    if "timed out" in normalized or "timeout" in normalized:
        return "Сервер не отвечает: истекло время ожидания ответа Node-agent."
    if "failed to start" in normalized:
        return "Не удалось запустить локальный bridge для подключения к серверу."
    if "invalid json" in normalized:
        return "Node-agent вернул некорректный ответ, настройка остановлена."
    if "ssh" in normalized:
        return f"Ошибка SSH при настройке сервера: {raw}"
    return raw


def _serialize_server_bootstrap_task(task: ServerBootstrapTask) -> ServerBootstrapTaskView:
    return ServerBootstrapTaskView(
        id=task.id,
        status=task.status,
        logs=_task_logs(task),
        bootstrap_command_profile=task.bootstrap_command_profile,
        bootstrap_packages=_task_bootstrap_package_list(task.bootstrap_packages_json),
        bootstrap_safe_init_packages=_task_bootstrap_package_list(task.bootstrap_safe_init_packages_json),
        bootstrap_full_only_packages=_task_bootstrap_package_list(task.bootstrap_full_only_packages_json),
        input_prompt=task.input_prompt,
        input_key=task.input_key,
        input_kind=task.input_kind,
        bootstrap_snapshot=_task_bootstrap_snapshot(task),
        bootstrap_steps=_task_bootstrap_steps(task),
        bootstrap_last_step_error=_task_bootstrap_last_step_error(task),
        server_id=task.server_id,
        last_error=task.last_error,
    )


def _mark_server_bootstrap_failed(db: Session, task: ServerBootstrapTask, message: str) -> ServerBootstrapTaskView:
    message = _humanize_bootstrap_error_v2(message)
    logs = _task_logs(task)
    if not logs or logs[-1] != message:
        logs.append(message)
    task.status = "failed"
    task.input_prompt = None
    task.input_key = None
    task.input_kind = None
    _set_task_bootstrap_snapshot(task, None)
    _set_task_bootstrap_execution(task, None)
    task.last_error = message
    _set_task_logs(task, logs)
    _mirror_server_bootstrap_job(db, task)
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_server_bootstrap_task(task)


def _finalize_server_bootstrap_success(db: Session, task: ServerBootstrapTask) -> None:
    existing = db.execute(select(Server).where(Server.name == task.server_name)).scalar_one_or_none()
    if existing is None:
        server = Server(
            name=task.server_name,
            server_type=task.server_type,
            tic_region=task.tic_region,
            tak_country=task.tak_country,
            host=task.host,
            ssh_port=task.ssh_port,
            ssh_login=task.ssh_login,
            ssh_password=task.ssh_password,
            is_active=False,
            is_excluded=False,
            last_seen_at=None,
        )
        db.add(server)
        db.flush()
    else:
        server = existing
        server.tic_region = task.tic_region
        server.tak_country = task.tak_country
        server.host = task.host
        server.ssh_port = task.ssh_port
        server.ssh_login = task.ssh_login
        server.ssh_password = task.ssh_password
    task.server_id = server.id
    task.status = "completed"
    task.input_prompt = None
    task.input_key = None
    task.input_kind = None


def _apply_server_bootstrap_response(db: Session, task: ServerBootstrapTask, response: dict[str, object]) -> None:
    logs = _task_logs(task)
    response_task_id = response.get("task_id")
    if isinstance(response_task_id, int) and response_task_id > 0:
        task.agent_task_id = response_task_id
    bootstrap_snapshot = response.get("bootstrap_snapshot")
    _set_task_bootstrap_snapshot(task, bootstrap_snapshot if isinstance(bootstrap_snapshot, dict) else None)
    bootstrap_plan = response.get("bootstrap_plan")
    _set_task_bootstrap_profile(task, bootstrap_plan if isinstance(bootstrap_plan, dict) else None)
    execution_result = bootstrap_plan.get("execution_result") if isinstance(bootstrap_plan, dict) else None
    _set_task_bootstrap_execution(task, execution_result if isinstance(execution_result, dict) else None)
    for line in response.get("logs", []) if isinstance(response.get("logs"), list) else []:
        logs.append(str(line))
    status_value = str(response.get("status") or ("completed" if response.get("ok", True) else "failed")).lower()
    if status_value in {"input_required", "confirmation_required"}:
        task.status = "input_required"
        task.input_prompt = str(response.get("input_prompt") or response.get("message") or "Требуется ввод")
        task.input_key = str(response.get("input_key") or "value")
        task.input_kind = str(response.get("input_kind") or ("confirm" if status_value == "confirmation_required" else "text"))
        task.last_error = None
    elif status_value == "running":
        task.status = "running"
        task.input_prompt = None
        task.input_key = None
        task.input_kind = None
        task.last_error = None
    elif response.get("ok", True) is False or status_value == "failed":
        task.status = "failed"
        task.input_prompt = None
        task.input_key = None
        task.input_kind = None
        task.last_error = _humanize_bootstrap_error_v2(str(response.get("error") or response.get("last_error") or "Bootstrap failed"))
        if task.last_error:
            logs.append(task.last_error)
    else:
        _finalize_server_bootstrap_success(db, task)
        logs.append("Bootstrap завершён.")
        task.last_error = None
    _set_task_logs(task, logs)
    _mirror_server_bootstrap_job(db, task)
    db.add(task)
    db.commit()
    db.refresh(task)


def _validate_server_create_payload(
    db: Session,
    payload: ServerCreate,
) -> tuple[str, str, str, str, str | None, TicRegion | None, str | None]:
    name = payload.name.strip()
    host = payload.host.strip()
    ssh_login = payload.ssh_login.strip()
    ssh_password = payload.ssh_password.strip()
    if not name or not host or not ssh_login or not ssh_password:
        raise PermissionDeniedError("Server connection fields must be filled in")
    tic_region: TicRegion | None = None
    tak_country: str | None = None
    if payload.server_type == ServerType.TIC.value:
        if not payload.tic_region:
            raise PermissionDeniedError("Tic region is required")
        tic_region = TicRegion(payload.tic_region)
    elif payload.server_type == ServerType.TAK.value:
        tak_country = (payload.tak_country or "").strip()
        if not tak_country:
            raise PermissionDeniedError("Tak country is required")
    existing = db.execute(select(Server).where(Server.name == name)).scalar_one_or_none()
    if existing is not None:
        raise PermissionDeniedError("Server name already exists")
    settings_values = get_basic_settings(db)
    repo_url = settings_values["nelomai_git_repo"]
    if not repo_url.strip():
        raise PermissionDeniedError("Git repository URL is required for the selected server type")
    return name, host, ssh_login, ssh_password, repo_url.strip(), tic_region, tak_country


def _build_server_bootstrap_context(task: ServerBootstrapTask) -> dict[str, object]:
    return {
        "task_id": task.agent_task_id or task.id,
        "server": {
            "name": task.server_name,
            "server_type": task.server_type.value,
            "tic_region": task.tic_region.value if task.tic_region is not None else None,
            "tak_country": task.tak_country,
            "host": task.host,
            "ssh_port": task.ssh_port,
            "ssh_login": task.ssh_login,
            "ssh_password": _decrypt_ssh_password(task.ssh_password),
        },
        "repository_url": task.repository_url,
        "os_family": "ubuntu",
        "os_version": "22.04",
    }


def create_server_bootstrap_task(db: Session, actor: User, payload: ServerCreate) -> ServerBootstrapTaskView:
    require_admin(actor)
    name, host, ssh_login, ssh_password, repo_url, tic_region, tak_country = _validate_server_create_payload(db, payload)
    job = create_panel_job(db, actor, "server_bootstrap")
    job.status = PanelJobStatus.RUNNING
    job.started_at = utc_now()
    update_panel_job_progress(db, job, 10, f"Подготовка bootstrap сервера {name}")

    task = ServerBootstrapTask(
        panel_job_id=job.id,
        server_name=name,
        server_type=ServerType(payload.server_type),
        tic_region=tic_region,
        tak_country=tak_country,
        host=host,
        ssh_port=payload.ssh_port,
        ssh_login=ssh_login,
        ssh_password=_encrypt_ssh_password(ssh_password),
        repository_url=repo_url,
        status="running",
    )
    _set_task_logs(
        task,
        [
            "Подготовка bootstrap-задачи...",
            "Проверяем SSH-параметры...",
            "Ожидаем ответ Node-agent...",
        ],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _mirror_server_bootstrap_job(db, task)
    db.commit()

    try:
        response = _run_agent_executor_logged(
            db,
            _build_server_executor_payload(
                action="bootstrap_server",
                extra=_build_server_bootstrap_context(task),
            ),
            actor_user_id=actor.id,
            interactive=True,
        )
    except ServerOperationUnavailableError as exc:
        return _mark_server_bootstrap_failed(db, task, str(exc))

    _apply_server_bootstrap_response(db, task, response)
    return _serialize_server_bootstrap_task(task)


def get_server_bootstrap_task_view(db: Session, actor: User, task_id: int) -> ServerBootstrapTaskView:
    require_admin(actor)
    task = get_server_bootstrap_task(db, task_id)
    if task.status != "running":
        _mirror_server_bootstrap_job(db, task)
        db.commit()
        return _serialize_server_bootstrap_task(task)
    try:
        response = _run_agent_executor_logged(
            db,
            _build_server_executor_payload(
                action="bootstrap_server_status",
                extra=_build_server_bootstrap_context(task),
            ),
            actor_user_id=actor.id,
            interactive=True,
        )
    except ServerOperationUnavailableError as exc:
        return _mark_server_bootstrap_failed(db, task, str(exc))
    _apply_server_bootstrap_response(db, task, response)
    return _serialize_server_bootstrap_task(task)


def submit_server_bootstrap_input(
    db: Session,
    actor: User,
    task_id: int,
    payload: ServerBootstrapInput,
) -> ServerBootstrapTaskView:
    require_admin(actor)
    task = get_server_bootstrap_task(db, task_id)
    if task.status != "input_required":
        raise PermissionDeniedError("Bootstrap task does not require input")
    _mirror_server_bootstrap_job(db, task)
    db.commit()
    try:
        response = _run_agent_executor_logged(
            db,
            _build_server_executor_payload(
                action="bootstrap_server_input",
                extra={
                    **_build_server_bootstrap_context(task),
                    "input": {
                        "key": task.input_key or "value",
                        "kind": task.input_kind or "text",
                        "value": payload.value or "",
                    },
                },
            ),
            actor_user_id=actor.id,
            interactive=True,
        )
    except ServerOperationUnavailableError as exc:
        return _mark_server_bootstrap_failed(db, task, str(exc))
    _apply_server_bootstrap_response(db, task, response)
    return _serialize_server_bootstrap_task(task)


def create_server_record(db: Session, actor: User, payload: ServerCreate) -> Server:
    require_admin(actor)
    name, host, ssh_login, ssh_password, repo_url, tic_region, tak_country = _validate_server_create_payload(db, payload)
    # Panel-side bootstrap contract for a blank Ubuntu 22.04 host.
    # The future Node-agent can request extra confirmation/input through this step
    # without moving bootstrap logic into the panel itself.
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(
            action="bootstrap_server",
            extra={
                "server": {
                    "name": name,
                    "server_type": payload.server_type,
                    "tic_region": tic_region.value if tic_region is not None else None,
                    "tak_country": tak_country,
                    "host": host,
                    "ssh_port": payload.ssh_port,
                    "ssh_login": ssh_login,
                    "ssh_password": ssh_password,
                },
                "repository_url": repo_url.strip() or None,
                "os_family": "ubuntu",
                "os_version": "22.04",
            },
        ),
        actor_user_id=actor.id,
    )

    server = Server(
        name=name,
        server_type=ServerType(payload.server_type),
        tic_region=tic_region,
        tak_country=tak_country,
        host=host,
        ssh_port=payload.ssh_port,
        ssh_login=ssh_login,
        ssh_password=_encrypt_ssh_password(ssh_password),
        is_active=False,
        is_excluded=False,
        last_seen_at=None,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def restart_server_agent(db: Session, actor: User, server_id: int) -> None:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if server.is_excluded:
        raise PermissionDeniedError("Server is excluded")
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(action="restart_server_agent", server=server),
        actor_user_id=actor.id,
    )
    server.is_active = True
    server.last_seen_at = utc_now()
    db.add(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.restart_agent",
        severity="info",
        message=f"Server agent restarted: {server.name}.",
        message_ru=f"Агент сервера перезагружен: {server.name}.",
        actor_user_id=actor.id,
        server_id=server.id,
    )
    _trigger_tak_tunnel_reconcile(db)


def verify_server_status(db: Session, actor: User, server_id: int) -> bool:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if server.is_excluded:
        raise PermissionDeniedError("Server is excluded")
    response = _run_agent_executor_logged(
        db,
        _build_server_executor_payload(action="verify_server_status", server=server),
        actor_user_id=actor.id,
    )
    is_active = bool(response.get("is_active", True))
    server.is_active = is_active
    server.last_seen_at = utc_now() if is_active else None
    db.add(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.refresh_status",
        severity="info" if is_active else "warning",
        message=f"Server status refreshed: {server.name}; active={is_active}.",
        message_ru=f"Статус сервера обновлён: {server.name}; {'онлайн' if is_active else 'не отвечает'}.",
        actor_user_id=actor.id,
        server_id=server.id,
    )
    _trigger_tak_tunnel_reconcile(db)
    return is_active


def _build_server_runtime_view(response: dict[str, object], server: Server) -> ServerRuntimeCheckView:
    checks: list[ServerRuntimeCheckItemView] = []
    runtime_payload = response.get("runtime")
    runtime_data = runtime_payload if isinstance(runtime_payload, dict) else response
    raw_checks = runtime_data.get("checks") if isinstance(runtime_data, dict) else None
    if isinstance(raw_checks, list):
        for index, item in enumerate(raw_checks):
            if not isinstance(item, dict):
                continue
            checks.append(
                ServerRuntimeCheckItemView(
                    key=str(item.get("key") or f"check_{index + 1}"),
                    label=str(item.get("label") or item.get("title") or item.get("key") or f"check_{index + 1}"),
                    status=str(
                        item.get("status")
                        or ("ok" if bool(item.get("ok", item.get("ready", False))) else "error")
                    ),
                    message=str(item.get("message") or item.get("detail") or ""),
                )
            )
    return ServerRuntimeCheckView(
        server_id=server.id,
        ready=bool(runtime_data.get("ready", False)) if isinstance(runtime_data, dict) else False,
        mode=str(runtime_data.get("mode") or "unknown") if isinstance(runtime_data, dict) else "unknown",
        runtime_root=str(runtime_data.get("runtime_root")) if isinstance(runtime_data, dict) and runtime_data.get("runtime_root") else None,
        wireguard_root=str(runtime_data.get("wireguard_root")) if isinstance(runtime_data, dict) and runtime_data.get("wireguard_root") else None,
        peers_root=str(runtime_data.get("peers_root")) if isinstance(runtime_data, dict) and runtime_data.get("peers_root") else None,
        checks=checks,
    )


def _parse_runtime_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sync_server_runtime_peer_stats(db: Session, server: Server, response: dict[str, object]) -> None:
    runtime_payload = response.get("runtime")
    runtime_data = runtime_payload if isinstance(runtime_payload, dict) else response
    live_interfaces = runtime_data.get("live_interfaces") if isinstance(runtime_data, dict) else None
    if not isinstance(live_interfaces, list):
        return
    interfaces = (
        db.execute(
            select(Interface)
            .options(joinedload(Interface.peers))
            .where(Interface.tic_server_id == server.id)
        )
        .unique()
        .scalars()
        .all()
    )
    interface_by_agent_id = {
        str(interface.agent_interface_id or "").strip(): interface
        for interface in interfaces
        if str(interface.agent_interface_id or "").strip()
    }
    changed = False
    for interface_snapshot in live_interfaces:
        if not isinstance(interface_snapshot, dict):
            continue
        agent_interface_id = str(interface_snapshot.get("agent_interface_id") or "").strip()
        if not agent_interface_id:
            continue
        interface = interface_by_agent_id.get(agent_interface_id)
        if interface is None:
            continue
        peers_by_slot = {int(peer.slot): peer for peer in interface.peers}
        seen_slots: set[int] = set()
        peer_snapshots = interface_snapshot.get("peers")
        if isinstance(peer_snapshots, list):
            for peer_snapshot in peer_snapshots:
                if not isinstance(peer_snapshot, dict):
                    continue
                slot = int(peer_snapshot.get("slot") or 0)
                if slot <= 0:
                    continue
                peer = peers_by_slot.get(slot)
                if peer is None:
                    continue
                seen_slots.add(slot)
                handshake_at = _parse_runtime_datetime(peer_snapshot.get("latest_handshake_at"))
                rx_bytes = peer_snapshot.get("transfer_rx_bytes")
                tx_bytes = peer_snapshot.get("transfer_tx_bytes")
                rx_value = int(rx_bytes) if isinstance(rx_bytes, int) else None
                tx_value = int(tx_bytes) if isinstance(tx_bytes, int) else None
                if peer.handshake_at != handshake_at:
                    peer.handshake_at = handshake_at
                    changed = True
                if peer.live_rx_bytes != rx_value:
                    peer.live_rx_bytes = rx_value
                    changed = True
                if peer.live_tx_bytes != tx_value:
                    peer.live_tx_bytes = tx_value
                    changed = True
                db.add(peer)
        for peer in interface.peers:
            if int(peer.slot) in seen_slots:
                continue
            if peer.handshake_at is not None or peer.live_rx_bytes is not None or peer.live_tx_bytes is not None:
                peer.handshake_at = None
                peer.live_rx_bytes = None
                peer.live_tx_bytes = None
                changed = True
                db.add(peer)
    if changed:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()


def _fetch_server_runtime_view(db: Session, server: Server, *, actor_user_id: int | None = None) -> ServerRuntimeCheckView:
    response = _run_agent_executor_logged(
        db,
        _build_server_executor_payload(action="verify_server_runtime", server=server),
        actor_user_id=actor_user_id,
    )
    return _build_server_runtime_view(response, server)


def verify_server_runtime(db: Session, actor: User, server_id: int) -> ServerRuntimeCheckView:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if server.is_excluded:
        raise PermissionDeniedError("Server is excluded")
    response = _run_agent_executor_logged(
        db,
        _build_server_executor_payload(action="verify_server_runtime", server=server),
        actor_user_id=actor.id,
    )
    _sync_server_runtime_peer_stats(db, server, response)
    runtime_view = _build_server_runtime_view(response, server)
    ready = runtime_view.ready
    write_audit_log(
        db,
        event_type="servers.verify_runtime",
        severity="info" if ready else "warning",
        message=f"Server runtime checked: {server.name}; ready={ready}.",
        message_ru=f"Runtime агента проверен: {server.name}; {'готов' if ready else 'не готов'}.",
        actor_user_id=actor.id,
        server_id=server.id,
        details=json.dumps(runtime_view.model_dump(), ensure_ascii=False),
    )
    return runtime_view


def reboot_server_host(db: Session, actor: User, server_id: int) -> None:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if server.is_excluded:
        raise PermissionDeniedError("Server is excluded")
    _run_agent_executor_logged(
        db,
        _build_server_executor_payload(action="reboot_server", server=server),
        actor_user_id=actor.id,
    )
    server.is_active = False
    server.last_seen_at = None
    db.add(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.reboot",
        severity="warning",
        message=f"Server reboot command sent: {server.name}.",
        message_ru=f"Команда перезагрузки сервера отправлена: {server.name}.",
        actor_user_id=actor.id,
        server_id=server.id,
    )
    _trigger_tak_tunnel_reconcile(db)


def exclude_server_record(db: Session, actor: User, server_id: int) -> None:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if server.is_excluded:
        raise PermissionDeniedError("Server is already excluded")
    affected_interfaces = len(server.tic_interfaces)
    affected_peers = 0
    server.is_excluded = True
    server.is_active = False
    server.last_seen_at = None
    for interface in server.tic_interfaces:
        for peer in interface.peers:
            peer.is_enabled = False
            affected_peers += 1
            db.add(peer)
    db.add(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.exclude",
        severity="warning",
        message=f"Server excluded: {server.name}. Interfaces affected: {affected_interfaces}; peers disabled: {affected_peers}.",
        message_ru=f"Сервер исключён: {server.name}. Затронуто интерфейсов: {affected_interfaces}; выключено пиров: {affected_peers}.",
        actor_user_id=actor.id,
        server_id=server.id,
    )
    _trigger_tak_tunnel_reconcile(db)


def restore_server_record(db: Session, actor: User, server_id: int) -> None:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if not server.is_excluded:
        raise PermissionDeniedError("Server is not excluded")
    server.is_excluded = False
    server.is_active = False
    server.last_seen_at = None
    db.add(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.restore",
        severity="info",
        message=f"Server restored to panel environment: {server.name}.",
        message_ru=f"Сервер восстановлен в окружение панели: {server.name}.",
        actor_user_id=actor.id,
        server_id=server.id,
    )
    _trigger_tak_tunnel_reconcile(db)


def delete_server_record(db: Session, actor: User, server_id: int) -> None:
    require_admin(actor)
    server = get_server_by_id(db, server_id)
    if not server.is_excluded:
        raise PermissionDeniedError("Only excluded servers can be deleted")
    server_name = server.name
    removed_interfaces = len(server.tic_interfaces)
    detached_endpoints = len(server.tak_interfaces)
    for interface in list(server.tic_interfaces):
        db.delete(interface)
    for interface in list(server.tak_interfaces):
        interface.tak_server_id = None
        interface.route_mode = RouteMode.STANDALONE
        db.add(interface)
    db.delete(server)
    db.commit()
    write_audit_log(
        db,
        event_type="servers.delete",
        severity="warning",
        message=f"Excluded server deleted: {server_name}. Removed interfaces: {removed_interfaces}; detached endpoints: {detached_endpoints}.",
        message_ru=f"Исключённый сервер удалён: {server_name}. Удалено интерфейсов: {removed_interfaces}; отвязано endpoint: {detached_endpoints}.",
        actor_user_id=actor.id,
        details=f"deleted_server_id={server_id}; removed_interfaces={removed_interfaces}; detached_endpoints={detached_endpoints}",
    )
    _trigger_tak_tunnel_reconcile(db)


def get_interface_by_id(db: Session, interface_id: int) -> Interface:
    interface = db.execute(
        select(Interface)
        .options(joinedload(Interface.peers), joinedload(Interface.user), joinedload(Interface.tic_server))
        .where(Interface.id == interface_id)
    ).unique().scalar_one_or_none()
    if interface is None:
        raise EntityNotFoundError("Interface not found")
    return interface


def ensure_interface_is_valid(interface: Interface) -> None:
    if interface.tic_server and interface.tic_server.is_excluded:
        raise PermissionDeniedError("Invalid interfaces cannot be assigned")


def get_peer_by_id(db: Session, peer_id: int) -> Peer:
    peer = db.execute(
        select(Peer)
        .options(joinedload(Peer.interface).joinedload(Interface.user))
        .where(Peer.id == peer_id)
    ).unique().scalar_one_or_none()
    if peer is None:
        raise EntityNotFoundError("Peer not found")
    if normalize_utc_datetime(peer.expires_at) is not None and normalize_utc_datetime(peer.expires_at) <= utc_now():
        db.delete(peer)
        db.commit()
        raise EntityNotFoundError("Peer not found")
    return peer


def _extract_server_suffix(name: str) -> str:
    tokens = name.replace("(", " ").replace(")", " ").split()
    for token in tokens:
        normalized = token.strip().lower()
        if len(normalized) == 2 and normalized[0].isdigit() and normalized[1].isalpha():
            return normalized[1]
    return ""


def _get_matching_tak_server(db: Session, tic_server: Server) -> Server | None:
    tic_suffix = _extract_server_suffix(tic_server.name)
    if not tic_suffix:
        return None
    tak_servers = db.execute(select(Server).where(Server.server_type == ServerType.TAK)).scalars().all()
    for server in tak_servers:
        if _extract_server_suffix(server.name) == tic_suffix:
            return server
    return None


def _find_first_free_port(db: Session) -> int:
    used_ports = set(db.execute(select(Interface.listen_port)).scalars().all())
    port = 10001
    while port in used_ports:
        port += 1
    return port


def _find_first_free_address_slot(db: Session) -> int:
    used_slots: set[int] = set()
    for address in db.execute(select(Interface.address_v4)).scalars().all():
        try:
            parts = address.split("/")[0].split(".")
            used_slots.add(int(parts[2]))
        except (IndexError, ValueError, AttributeError):
            continue
    slot = 1
    while slot in used_slots:
        slot += 1
    return slot


def _load_interface_creation_context(
    db: Session,
    *,
    tic_server_id: int,
    tak_server_id: int | None,
) -> tuple[Server, Server | None, RouteMode]:
    tic_server = db.execute(
        select(Server).where(Server.id == tic_server_id, Server.server_type == ServerType.TIC)
    ).scalar_one_or_none()
    if tic_server is None:
        raise EntityNotFoundError("Tic server not found")

    tak_server = None
    route_mode = RouteMode.STANDALONE
    if tak_server_id is not None:
        tak_server = db.execute(
            select(Server).where(Server.id == tak_server_id, Server.server_type == ServerType.TAK)
        ).scalar_one_or_none()
        if tak_server is None:
            raise EntityNotFoundError("Tak server not found")
        route_mode = RouteMode.VIA_TAK
    return tic_server, tak_server, route_mode


def _ensure_interface_name_available(db: Session, name: str) -> str:
    normalized = name.strip()
    existing = db.execute(select(Interface).where(func.lower(Interface.name) == normalized.lower())).scalar_one_or_none()
    if existing is not None:
        raise PermissionDeniedError("Interface name already exists")
    return normalized


def _ensure_interface_network_values_available(
    db: Session,
    *,
    tic_server_id: int,
    listen_port: int,
    address_v4: str,
) -> None:
    port_taken = db.execute(
        select(Interface.id).where(Interface.tic_server_id == tic_server_id, Interface.listen_port == listen_port)
    ).scalar_one_or_none()
    if port_taken is not None:
        raise PermissionDeniedError("Listen port is already used on this Tic server")
    address_taken = db.execute(
        select(Interface.id).where(Interface.tic_server_id == tic_server_id, Interface.address_v4 == address_v4)
    ).scalar_one_or_none()
    if address_taken is not None:
        raise PermissionDeniedError("IPv4 address is already used on this Tic server")


def _first_free_listen_port_from_db(db: Session, *, tic_server_id: int, start: int = 10001) -> int:
    used_ports = set(
        db.execute(select(Interface.listen_port).where(Interface.tic_server_id == tic_server_id)).scalars().all()
    )
    port = start
    while port in used_ports:
        port += 1
    return port


def _first_free_address_v4_from_db(db: Session, *, tic_server_id: int) -> str:
    rows = db.execute(select(Interface.address_v4).where(Interface.tic_server_id == tic_server_id)).scalars().all()
    used_slots: set[int] = set()
    for value in rows:
        raw = str(value or "").strip()
        if not raw:
            continue
        ip_part = raw.split("/", 1)[0]
        parts = ip_part.split(".")
        if len(parts) != 4:
            continue
        try:
            slot = int(parts[2])
        except ValueError:
            continue
        if 1 <= slot <= 254:
            used_slots.add(slot)
    slot = 1
    while slot in used_slots:
        slot += 1
    return f"10.8.{slot}.1/24"


def prepare_interface_creation(db: Session, actor: User, payload: InterfacePrepareRequest) -> InterfaceAllocationView:
    require_admin(actor)
    name = _ensure_interface_name_available(db, payload.name)
    tic_server, tak_server, route_mode = _load_interface_creation_context(
        db,
        tic_server_id=payload.tic_server_id,
        tak_server_id=payload.tak_server_id,
    )
    response = _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "prepare_interface",
            _build_interface_executor_context(
                interface_id=0,
                name=name,
                tic_server=tic_server,
                tak_server=tak_server,
                route_mode=route_mode,
            ),
            exclusion_filters_enabled=exclusion_filters_enabled(db),
            block_filters_enabled=block_filters_enabled(db),
        ),
        actor_user_id=actor.id,
    )
    listen_port = response.get("listen_port")
    address_v4 = response.get("address_v4")
    if not isinstance(listen_port, int):
        raise ServerOperationUnavailableError("Tic server did not return listen_port")
    if not isinstance(address_v4, str) or not address_v4.strip():
        raise ServerOperationUnavailableError("Tic server did not return address_v4")
    db_listen_port = _first_free_listen_port_from_db(db, tic_server_id=tic_server.id)
    db_address_v4 = _first_free_address_v4_from_db(db, tic_server_id=tic_server.id)
    if listen_port != db_listen_port:
        listen_port = db_listen_port
    if address_v4.strip() != db_address_v4:
        address_v4 = db_address_v4
    _ensure_interface_network_values_available(
        db,
        tic_server_id=tic_server.id,
        listen_port=listen_port,
        address_v4=address_v4.strip(),
    )
    return InterfaceAllocationView(
        listen_port=listen_port,
        address_v4=address_v4.strip(),
        route_mode=route_mode,
    )


def _persist_interface_record(
    db: Session,
    *,
    actor: User,
    name: str,
    tic_server: Server,
    tak_server: Server | None,
    route_mode: RouteMode,
    listen_port: int,
    address_v4: str,
    peer_limit: int,
    agent_interface_id: str | None = None,
) -> Interface:
    interface = Interface(
        agent_interface_id=agent_interface_id,
        name=name,
        user_id=actor.id,
        tic_server_id=tic_server.id,
        tak_server_id=tak_server.id if tak_server else None,
        route_mode=route_mode,
        listen_port=listen_port,
        address_v4=address_v4,
        address_v6=None,
        peer_limit=peer_limit,
        is_pending_owner=True,
    )
    db.add(interface)
    db.flush()

    for slot in range(1, peer_limit + 1):
        db.add(Peer(interface_id=interface.id, slot=slot, comment=None, is_enabled=False))

    db.commit()
    db.refresh(interface)
    return interface


def rotate_tak_tunnel_pair(db: Session, actor: User, *, tic_server_id: int, tak_server_id: int) -> None:
    require_admin(actor)
    tic_server = get_server_by_id(db, tic_server_id)
    tak_server = get_server_by_id(db, tak_server_id)
    if tic_server.server_type != ServerType.TIC:
        raise InvalidInputError("Tic server not found")
    if tak_server.server_type != ServerType.TAK:
        raise InvalidInputError("Tak server not found")
    tunnel_id, artifact_revision = _rotate_tak_tunnel_transport(
        db,
        tic_server=tic_server,
        tak_server=tak_server,
        actor_user_id=actor.id,
    )
    write_audit_log(
        db,
        event_type="tak_tunnels.artifacts_rotated",
        severity="info",
        message=f"Tak tunnel artifacts rotated: tic={tic_server.name}, tak={tak_server.name}",
        message_ru=f"Артефакты туннеля Tic/Tak ротированы: {tic_server.name} → {tak_server.name}",
        actor_user_id=actor.id,
        server_id=tic_server.id,
        details=json.dumps(
            {
                "tic_server_id": tic_server.id,
                "tic_server_name": tic_server.name,
                "tak_server_id": tak_server.id,
                "tak_server_name": tak_server.name,
                "tunnel_id": tunnel_id,
                "artifact_revision": artifact_revision,
            },
            ensure_ascii=False,
        ),
    )
    db.flush()


def create_interface_record(db: Session, actor: User, payload: InterfaceCreate) -> Interface:
    require_admin(actor)
    name = _ensure_interface_name_available(db, payload.name)
    tic_server, tak_server, route_mode = _load_interface_creation_context(
        db,
        tic_server_id=payload.tic_server_id,
        tak_server_id=payload.tak_server_id,
    )

    if payload.peer_limit not in {5, 10, 15, 20}:
        raise PermissionDeniedError("Peer limit must be 5, 10, 15 or 20")
    if payload.listen_port is None:
        raise PermissionDeniedError("Listen port must be selected before creating the interface")
    if not payload.address_v4 or not payload.address_v4.strip():
        raise PermissionDeniedError("IPv4 address must be selected before creating the interface")

    listen_port = payload.listen_port
    address_v4 = payload.address_v4.strip()
    _ensure_interface_network_values_available(
        db,
        tic_server_id=tic_server.id,
        listen_port=listen_port,
        address_v4=address_v4,
    )
    tunnel_attached = False
    if tak_server is not None and route_mode == RouteMode.VIA_TAK:
        if (
            _count_via_tak_interfaces_for_pair(
                db,
                tic_server_id=tic_server.id,
                tak_server_id=tak_server.id,
            )
            == 0
        ):
            _provision_and_attach_tak_tunnel(
                db,
                tic_server=tic_server,
                tak_server=tak_server,
                actor_user_id=actor.id,
            )
            tunnel_attached = True
    try:
        response = _run_agent_executor_logged(
            db,
            _build_tic_executor_payload(
                "create_interface",
                _build_interface_executor_context(
                    interface_id=0,
                    name=name,
                    tic_server=tic_server,
                    tak_server=tak_server,
                    route_mode=route_mode,
                    listen_port=listen_port,
                    address_v4=address_v4,
                ),
                exclusion_filters_enabled=exclusion_filters_enabled(db),
                block_filters_enabled=block_filters_enabled(db),
            ),
            actor_user_id=actor.id,
        )
    except Exception:
        if tunnel_attached:
            try:
                _detach_tak_tunnel_pair(
                    db,
                    tic_server=tic_server,
                    tak_server=tak_server,
                    actor_user_id=actor.id,
                )
            except Exception:
                pass
        raise
    agent_interface_id = response.get("agent_interface_id") or response.get("server_interface_id")
    return _persist_interface_record(
        db,
        actor=actor,
        name=name,
        tic_server=tic_server,
        tak_server=tak_server,
        route_mode=route_mode,
        listen_port=listen_port,
        address_v4=address_v4,
        peer_limit=payload.peer_limit,
        agent_interface_id=str(agent_interface_id).strip() if agent_interface_id else None,
    )


def toggle_interface_state(db: Session, actor: User, interface_id: int) -> bool:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    next_state = not any(peer.is_enabled for peer in interface.peers)
    if interface.is_pending_owner and next_state:
        raise PermissionDeniedError("Assign an owner before enabling the interface")
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "toggle_interface",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"is_enabled": next_state}},
        ),
        actor_user_id=actor.id,
    )
    _refresh_via_tak_interface_runtime(db, interface, actor_user_id=actor.id)
    for peer in interface.peers:
        peer.is_enabled = next_state
        db.add(peer)
    db.commit()
    return next_state


def update_interface_peer_limit(
    db: Session,
    actor: User,
    interface_id: int,
    payload: InterfacePeerLimitUpdate,
) -> int:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    if payload.peer_limit not in {5, 10, 15, 20}:
        raise PermissionDeniedError("Peer limit must be 5, 10, 15 or 20")
    if payload.peer_limit < len(interface.peers):
        peers_to_remove = sorted(interface.peers, key=lambda peer: peer.slot, reverse=True)[: len(interface.peers) - payload.peer_limit]
        for peer in peers_to_remove:
            db.delete(peer)
    interface.peer_limit = payload.peer_limit
    db.add(interface)
    db.commit()
    return interface.peer_limit


def update_interface_route_mode(
    db: Session,
    actor: User,
    interface_id: int,
    payload: InterfaceRouteModeUpdate,
) -> RouteMode:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    previous_tak_server = interface.tak_server
    previous_route_mode = interface.route_mode
    next_mode = payload.route_mode
    if next_mode == RouteMode.VIA_TAK and interface.tak_server_id is None:
        raise PermissionDeniedError("Tak server is required for via_tak mode")
    if interface.tak_server_id is None:
        next_mode = RouteMode.STANDALONE
    if next_mode == RouteMode.VIA_TAK and interface.tak_server is not None:
        _provision_and_attach_tak_tunnel(
            db,
            tic_server=interface.tic_server,
            tak_server=interface.tak_server,
            actor_user_id=actor.id,
        )
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "update_interface_route_mode",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"route_mode": next_mode.value}},
        ),
        actor_user_id=actor.id,
    )
    interface.route_mode = next_mode
    if next_mode != RouteMode.VIA_TAK:
        interface.tak_tunnel_fallback_active = False
        interface.tak_tunnel_last_status = None
    db.add(interface)
    db.flush()
    if (
        previous_tak_server is not None
        and previous_route_mode == RouteMode.VIA_TAK
        and next_mode != RouteMode.VIA_TAK
        and _count_via_tak_interfaces_for_pair(
            db,
            tic_server_id=interface.tic_server_id,
            tak_server_id=previous_tak_server.id,
        )
        == 0
    ):
        _detach_tak_tunnel_pair(
            db,
            tic_server=interface.tic_server,
            tak_server=previous_tak_server,
            actor_user_id=actor.id,
        )
    db.commit()
    db.refresh(interface)
    return interface.route_mode if interface.tak_server_id else RouteMode.STANDALONE


def update_interface_tak_server(
    db: Session,
    actor: User,
    interface_id: int,
    payload: InterfaceTakServerUpdate,
) -> tuple[int | None, RouteMode]:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    previous_tak_server = interface.tak_server
    previous_route_mode = interface.route_mode
    next_tak_server = None
    next_route_mode = interface.route_mode
    if payload.tak_server_id is not None:
        _, next_tak_server, next_route_mode = _load_interface_creation_context(
            db,
            tic_server_id=interface.tic_server_id,
            tak_server_id=payload.tak_server_id,
        )
    else:
        next_route_mode = RouteMode.STANDALONE
    context = _build_interface_executor_context(
        interface_id=interface.id,
        name=interface.name,
        tic_server=interface.tic_server,
        tak_server=next_tak_server,
        route_mode=next_route_mode,
        listen_port=interface.listen_port,
        address_v4=interface.address_v4,
    )
    if next_tak_server is not None and next_route_mode == RouteMode.VIA_TAK:
        _provision_and_attach_tak_tunnel(
            db,
            tic_server=interface.tic_server,
            tak_server=next_tak_server,
            actor_user_id=actor.id,
        )
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "update_interface_tak_server",
            context,
            exclusion_filters_enabled=exclusion_filters_enabled(db) and interface.exclusion_filters_enabled,
            block_filters_enabled=block_filters_enabled(db),
            extra={
                "target_state": {
                    "tak_server_id": next_tak_server.id if next_tak_server else None,
                    "route_mode": next_route_mode.value,
                }
            },
        ),
        actor_user_id=actor.id,
    )
    interface.tak_server_id = next_tak_server.id if next_tak_server else None
    interface.route_mode = next_route_mode if next_tak_server else RouteMode.STANDALONE
    if next_tak_server is None or next_route_mode != RouteMode.VIA_TAK:
        interface.tak_tunnel_fallback_active = False
        interface.tak_tunnel_last_status = None
    db.add(interface)
    db.flush()
    if (
        previous_tak_server is not None
        and previous_route_mode == RouteMode.VIA_TAK
        and (
            next_tak_server is None
            or next_route_mode != RouteMode.VIA_TAK
            or previous_tak_server.id != next_tak_server.id
        )
        and _count_via_tak_interfaces_for_pair(
            db,
            tic_server_id=interface.tic_server_id,
            tak_server_id=previous_tak_server.id,
        )
        == 0
    ):
        _detach_tak_tunnel_pair(
            db,
            tic_server=interface.tic_server,
            tak_server=previous_tak_server,
            actor_user_id=actor.id,
        )
    db.commit()
    db.refresh(interface)
    return interface.tak_server_id, interface.route_mode if interface.tak_server_id else RouteMode.STANDALONE


def update_interface_exclusion_filters(
    db: Session,
    actor: User,
    interface_id: int,
    payload: InterfaceExclusionFiltersUpdate,
) -> bool:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    next_enabled = payload.enabled if exclusion_filters_enabled(db) else False
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "update_interface_exclusion_filters",
            interface,
            exclusion_filters_enabled=exclusion_filters_enabled(db) and next_enabled,
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"exclusion_filters_enabled": next_enabled}},
        ),
        actor_user_id=actor.id,
    )
    interface.exclusion_filters_enabled = next_enabled
    db.add(interface)
    db.commit()
    db.refresh(interface)
    return interface.exclusion_filters_enabled


def create_peer_for_interface(db: Session, actor: User, interface_id: int, preview_mode: bool) -> int:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    interface = get_interface_by_id(db, interface_id)
    if actor.role != UserRole.ADMIN and interface.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own interfaces")
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    if len(interface.peers) >= interface.peer_limit:
        raise PermissionDeniedError("Peer limit reached")
    next_slot = next_free_peer_slot(db, interface.id)
    if next_slot > interface.peer_limit:
        raise PermissionDeniedError("No free peer slot available")
    peer = Peer(
        interface_id=interface.id,
        slot=next_slot,
        comment=None,
        is_enabled=interface.user.role != UserRole.ADMIN,
        block_filters_enabled=True,
    )
    db.add(peer)
    db.commit()
    return peer.id


def toggle_peer_state(db: Session, actor: User, peer_id: int, preview_mode: bool) -> bool:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    peer = get_peer_by_id(db, peer_id)
    if actor.role != UserRole.ADMIN and peer.interface.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own peers")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    next_state = not peer.is_enabled
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "toggle_peer",
            peer.interface,
            peer,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, peer.interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"is_enabled": next_state}},
        ),
        actor_user_id=actor.id,
    )
    peer.is_enabled = next_state
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return peer.is_enabled


def update_peer_comment(db: Session, actor: User, peer_id: int, payload: PeerCommentUpdate, preview_mode: bool) -> str | None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    peer = get_peer_by_id(db, peer_id)
    if actor.role != UserRole.ADMIN and peer.interface.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own peers")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    peer.comment = payload.comment.strip() if payload.comment and payload.comment.strip() else None
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return peer.comment


def recreate_peer(db: Session, actor: User, peer_id: int, preview_mode: bool) -> Peer:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    peer = get_peer_by_id(db, peer_id)
    if actor.role != UserRole.ADMIN and peer.interface.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own peers")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    _run_peer_agent_action(db, "recreate_peer", peer.interface, peer, actor_user_id=actor.id)
    peer.handshake_at = None
    peer.traffic_7d_mb = 0
    peer.traffic_30d_mb = 0
    if peer.interface.is_pending_owner:
        peer.is_enabled = False
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return peer


def delete_peer(db: Session, actor: User, peer_id: int, preview_mode: bool) -> None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    peer = get_peer_by_id(db, peer_id)
    if actor.role != UserRole.ADMIN and peer.interface.user_id != actor.id:
        raise PermissionDeniedError("Users can edit only their own peers")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    _run_peer_agent_action(db, "delete_peer", peer.interface, peer, actor_user_id=actor.id)
    peer = get_peer_by_id(db, peer_id)
    db.delete(peer)
    db.commit()


def download_peer_config(db: Session, actor: User, peer_id: int) -> dict[str, object]:
    peer = get_peer_by_id(db, peer_id)
    if actor.role != UserRole.ADMIN and peer.interface.user_id != actor.id:
        raise PermissionDeniedError("Users can download only their own peers")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    response = _run_peer_agent_action(db, "download_peer_config", peer.interface, peer, actor_user_id=actor.id)
    return _extract_download_payload(
        response,
        default_filename=f"{peer.interface.name}-{peer.slot}.conf",
        default_content_type="text/plain; charset=utf-8",
    )


def build_peer_qr_code(db: Session, actor: User, peer_id: int) -> dict[str, object]:
    payload = download_peer_config(db, actor, peer_id)
    raw_content = payload.get("content")
    if isinstance(raw_content, bytes):
        content = raw_content.decode("utf-8", errors="strict").strip()
    elif isinstance(raw_content, str):
        content = raw_content.strip()
    else:
        content = ""
    if not content:
        raise InvalidInputError("Peer config is empty")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
        image_factory=SvgPathImage,
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image()
    buffer = io.BytesIO()
    image.save(buffer)
    svg_bytes = buffer.getvalue()
    data_url = f"data:image/svg+xml;base64,{base64.b64encode(svg_bytes).decode('ascii')}"
    return {
        "filename": str(payload.get("filename") or f"peer-{peer_id}.conf"),
        "data_url": data_url,
    }


def download_peer_config_public(db: Session, peer_id: int, token_id: str) -> dict[str, object]:
    peer = get_peer_by_id(db, peer_id)
    link = db.execute(
        select(PeerDownloadLink).where(PeerDownloadLink.token_id == token_id, PeerDownloadLink.peer_id == peer.id)
    ).scalar_one_or_none()
    if link is None or link.revoked_at is not None:
        raise EntityNotFoundError("Download link not found")
    now = utc_now()
    peer_expires_at = normalize_utc_datetime(peer.expires_at)
    if peer_expires_at is not None and peer_expires_at <= now:
        raise EntityNotFoundError("Peer expired")
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    response = _run_peer_agent_action(db, "download_peer_config", peer.interface, peer)
    return _extract_download_payload(
        response,
        default_filename=f"{peer.interface.name}-{peer.slot}.conf",
        default_content_type="text/plain; charset=utf-8",
    )


def download_interface_bundle(db: Session, actor: User, interface_id: int) -> dict[str, object]:
    interface = get_interface_by_id(db, interface_id)
    if actor.role != UserRole.ADMIN and interface.user_id != actor.id:
        raise PermissionDeniedError("Users can download only their own interfaces")
    ensure_interface_is_valid(interface)
    _ensure_interface_uses_tic_agent(interface)
    response = _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "download_interface_bundle",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
        ),
        actor_user_id=actor.id,
    )
    return _extract_download_payload(
        response,
        default_filename=f"{interface.name}.zip",
        default_content_type="application/zip",
    )


def update_peer_expiry(db: Session, actor: User, peer_id: int, payload: PeerExpiryUpdate, preview_mode: bool) -> datetime | None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    require_admin(actor)
    peer = get_peer_by_id(db, peer_id)
    ensure_interface_is_valid(peer.interface)
    peer.expires_at = normalize_utc_datetime(payload.expires_at)
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return peer.expires_at


def update_peer_block_filters(
    db: Session,
    actor: User,
    peer_id: int,
    payload: PeerBlockFiltersUpdate,
    preview_mode: bool,
) -> bool:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    require_admin(actor)
    peer = get_peer_by_id(db, peer_id)
    ensure_interface_is_valid(peer.interface)
    _ensure_interface_uses_tic_agent(peer.interface)
    next_enabled = payload.enabled if block_filters_enabled(db) else False
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "update_peer_block_filters",
            peer.interface,
            peer,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, peer.interface),
            block_filters_enabled=block_filters_enabled(db) and next_enabled,
            extra={
                # Panel-side contract for the future Tic Node-agent: block rules are
                # applied per peer, while global block rules still exist as shared input.
                "target_state": {"block_filters_enabled": next_enabled}
            },
        ),
        actor_user_id=actor.id,
    )
    peer.block_filters_enabled = next_enabled
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return peer.block_filters_enabled


def update_user_expires_at(db: Session, actor: User, user_id: int, payload: UserExpiresUpdate, preview_mode: bool) -> datetime | None:
    if preview_mode:
        raise PermissionDeniedError("Preview mode is read-only")
    require_admin(actor)
    user = get_user_by_id(db, user_id)
    user.expires_at = normalize_utc_datetime(payload.expires_at)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.expires_at


def generate_peer_download_link(db: Session, actor: User, peer_id: int, base_url: str) -> str:
    require_admin(actor)
    peer = get_peer_by_id(db, peer_id)
    ensure_interface_is_valid(peer.interface)
    token_id = secrets.token_urlsafe(24)
    link = PeerDownloadLink(
        token_id=token_id,
        peer_id=peer.id,
        created_by_user_id=actor.id,
        expires_at=normalize_utc_datetime(peer.expires_at),
        revoked_at=None,
    )
    db.add(link)
    db.commit()
    token = create_peer_download_token(peer.id, token_id)
    write_audit_log(
        db,
        event_type="peer_links.create",
        severity="info",
        message=f"Public peer download link created for {peer.interface.name} peer {peer.slot}.",
        message_ru=f"Создана ссылка на скачивание для {peer.interface.name}, пир {peer.slot}.",
        actor_user_id=actor.id,
        target_user_id=peer.interface.user_id,
    )
    return f"{base_url.rstrip('/')}/downloads/peer/{quote(token)}"


def serialize_shared_peer_link(link: PeerDownloadLink) -> SharedPeerLinkView:
    now = utc_now()
    peer = link.peer
    interface = peer.interface
    user = interface.user
    link_expires_at = normalize_utc_datetime(link.expires_at)
    peer_expires_at = normalize_utc_datetime(peer.expires_at)
    effective_expires_at = peer_expires_at or link_expires_at
    is_expired = effective_expires_at is not None and effective_expires_at <= now
    return SharedPeerLinkView(
        id=link.id,
        peer_id=peer.id,
        interface_name=interface.name,
        peer_slot=peer.slot,
        user_id=user.id,
        user_login=user.login,
        user_display_name=user.display_name,
        peer_expires_at=peer_expires_at,
        link_expires_at=effective_expires_at,
        created_at=link.created_at,
        revoked_at=link.revoked_at,
        is_revoked=link.revoked_at is not None,
        is_expired=is_expired,
        is_lifetime=effective_expires_at is None,
    )


def get_shared_peer_links_page(db: Session, actor: User) -> SharedPeerLinksPageView:
    require_admin(actor)
    purge_expired_peers(db)
    revoked_visible_after = utc_now() - timedelta(hours=1)
    links = db.execute(
        select(PeerDownloadLink)
        .options(
            joinedload(PeerDownloadLink.peer).joinedload(Peer.interface).joinedload(Interface.user),
        )
        .where(or_(PeerDownloadLink.revoked_at.is_(None), PeerDownloadLink.revoked_at >= revoked_visible_after))
        .order_by(PeerDownloadLink.created_at.desc(), PeerDownloadLink.id.desc())
    ).unique().scalars().all()
    items = [serialize_shared_peer_link(link) for link in links]
    return SharedPeerLinksPageView(
        links=items,
        active_count=sum(1 for item in items if not item.is_revoked and not item.is_expired),
        lifetime_count=sum(1 for item in items if not item.is_revoked and not item.is_expired and item.is_lifetime),
    )


def serialize_registration_link(link: RegistrationLink, base_url: str) -> RegistrationLinkView:
    is_used = link.used_at is not None
    is_revoked = link.revoked_at is not None
    status_label = "использована" if is_used else ("отозвана" if is_revoked else "не использована")
    return RegistrationLinkView(
        id=link.id,
        url=_registration_link_url(link.token_id, base_url),
        comment=link.comment,
        auto_create_interfaces=bool(link.auto_create_interfaces),
        created_at=link.created_at,
        created_by_login=link.created_by_user.login if link.created_by_user else None,
        used_at=link.used_at,
        used_by_login=link.used_by_user.login if link.used_by_user else None,
        revoked_at=link.revoked_at,
        is_used=is_used,
        is_revoked=is_revoked,
        status_label=status_label,
    )


def _find_default_registration_servers(db: Session) -> tuple[Server, Server]:
    tic_server = db.execute(
        select(Server).where(Server.server_type == ServerType.TIC).order_by(Server.id.asc())
    ).scalars().first()
    if tic_server is None:
        raise InvalidInputError("No Tic server is configured for automatic interface creation")
    tak_server = db.execute(
        select(Server).where(Server.server_type == ServerType.TAK).order_by(Server.id.asc())
    ).scalars().first()
    if tak_server is None:
        raise InvalidInputError("No Tak server is configured for automatic interface creation")
    return tic_server, tak_server


def _interface_name_exists(db: Session, name: str) -> bool:
    normalized = name.strip()
    if not normalized:
        return False
    existing = db.execute(
        select(Interface.id).where(func.lower(Interface.name) == normalized.lower())
    ).scalar_one_or_none()
    return existing is not None


def _unique_interface_name_from_seed(db: Session, seed: str, *, mutable_length: int) -> str:
    candidate = seed.strip()
    if not candidate:
        raise InvalidInputError("Interface name seed is empty")
    if not _interface_name_exists(db, candidate):
        return candidate
    mutable_length = max(1, min(mutable_length, len(candidate)))
    seen: set[str] = {candidate.lower()}
    for _ in range(512):
        position = secrets.randbelow(mutable_length)
        replacement = secrets.choice(string.ascii_lowercase)
        mutated = list(candidate)
        mutated[position] = replacement
        next_candidate = "".join(mutated)
        lowered = next_candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        if not _interface_name_exists(db, next_candidate):
            return next_candidate
    raise InvalidInputError("Unable to generate a unique interface name from login")


def _build_auto_interface_names(db: Session, login: str) -> tuple[str, str]:
    login_seed = login.strip().lower()
    standalone_seed = login_seed[:8]
    via_tak_prefix = login_seed[:5]
    via_tak_seed = f"{via_tak_prefix}TAK"
    return (
        _unique_interface_name_from_seed(db, standalone_seed, mutable_length=len(standalone_seed)),
        _unique_interface_name_from_seed(db, via_tak_seed, mutable_length=len(via_tak_prefix)),
    )


def _resolve_registration_actor(db: Session, link: RegistrationLink) -> User:
    actor = link.created_by_user
    if actor is not None and actor.role == UserRole.ADMIN:
        return actor
    actor = db.execute(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())
    ).scalars().first()
    if actor is None:
        raise InvalidInputError("No admin user is available for automatic interface creation")
    return actor


def _auto_create_registration_interfaces(db: Session, link: RegistrationLink, user: User) -> None:
    if not link.auto_create_interfaces:
        return
    actor = _resolve_registration_actor(db, link)
    tic_server, tak_server = _find_default_registration_servers(db)
    standalone_name, via_tak_name = _build_auto_interface_names(db, user.login)
    for name, tak_server_id in (
        (standalone_name, None),
        (via_tak_name, tak_server.id),
    ):
        allocation = prepare_interface_creation(
            db,
            actor,
            InterfacePrepareRequest(
                name=name,
                tic_server_id=tic_server.id,
                tak_server_id=tak_server_id,
            ),
        )
        interface = create_interface_record(
            db,
            actor,
            InterfaceCreate(
                name=name,
                tic_server_id=tic_server.id,
                tak_server_id=tak_server_id,
                listen_port=allocation.listen_port,
                address_v4=allocation.address_v4,
                peer_limit=5,
            ),
        )
        assign_interface_to_user(db, actor, interface.id, user.id)


def generate_registration_link(db: Session, actor: User, base_url: str, *, auto_create_interfaces: bool = False) -> RegistrationLinkView:
    require_admin(actor)
    if auto_create_interfaces:
        _find_default_registration_servers(db)
    link = RegistrationLink(
        token_id=secrets.token_urlsafe(24),
        comment=None,
        auto_create_interfaces=auto_create_interfaces,
        created_by_user_id=actor.id,
        revoked_at=None,
        used_at=None,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    write_audit_log(
        db,
        event_type="registration_links.create",
        severity="info",
        message=f"Registration link created by {actor.login}.",
        message_ru=f"Создана ссылка регистрации администратором {actor.login}.",
        actor_user_id=actor.id,
    )
    return serialize_registration_link(link, base_url)


def get_registration_links_page(
    db: Session,
    actor: User,
    *,
    base_url: str,
    created_link_id: int | None = None,
) -> RegistrationLinksPageView:
    require_admin(actor)
    recent_used_after = utc_now() - timedelta(days=REGISTRATION_USED_LINK_RETENTION_DAYS)
    links = db.execute(
        select(RegistrationLink)
        .options(
            joinedload(RegistrationLink.created_by_user),
            joinedload(RegistrationLink.used_by_user),
        )
        .where(
            or_(
                RegistrationLink.used_at.is_(None),
                RegistrationLink.used_at >= recent_used_after,
            )
        )
        .order_by(RegistrationLink.created_at.desc(), RegistrationLink.id.desc())
    ).unique().scalars().all()
    unused_links = [serialize_registration_link(link, base_url) for link in links if link.used_at is None and link.revoked_at is None]
    revoked_links = [serialize_registration_link(link, base_url) for link in links if link.used_at is None and link.revoked_at is not None]
    recent_used_links = [serialize_registration_link(link, base_url) for link in links if link.used_at is not None]
    created_link = None
    if created_link_id is not None:
        record = db.get(RegistrationLink, created_link_id)
        if record is not None:
            created_link = serialize_registration_link(record, base_url)
    return RegistrationLinksPageView(
        unused_links=unused_links,
        revoked_links=revoked_links,
        recent_used_links=recent_used_links,
        created_link=created_link,
        unused_count=len(unused_links),
        revoked_count=len(revoked_links),
        recent_used_count=len(recent_used_links),
    )


def revoke_unused_registration_links(db: Session, actor: User) -> int:
    require_admin(actor)
    links = db.execute(
        select(RegistrationLink)
        .where(RegistrationLink.used_at.is_(None), RegistrationLink.revoked_at.is_(None))
        .order_by(RegistrationLink.id.asc())
    ).scalars().all()
    if not links:
        return 0
    now = utc_now()
    for link in links:
        link.revoked_at = now
        db.add(link)
    db.commit()
    write_audit_log(
        db,
        event_type="registration_links.revoke_unused",
        severity="warning",
        message=f"Unused registration links revoked by {actor.login}.",
        message_ru=f"Администратор {actor.login} отозвал все неиспользованные ссылки регистрации.",
        actor_user_id=actor.id,
        details=f"revoked_count={len(links)}",
    )
    return len(links)


def update_registration_link_comment(db: Session, actor: User, link_id: int, comment: str | None) -> None:
    require_admin(actor)
    link = db.get(RegistrationLink, link_id)
    if link is None:
        raise EntityNotFoundError("Registration link not found")
    normalized_comment = str(comment or "").strip()
    if len(normalized_comment) > 255:
        raise InvalidInputError("Registration link comment is too long")
    link.comment = normalized_comment or None
    db.add(link)
    db.commit()


def get_public_registration_link(db: Session, token_id: str) -> RegistrationLink:
    link = db.execute(
        select(RegistrationLink)
        .options(joinedload(RegistrationLink.created_by_user), joinedload(RegistrationLink.used_by_user))
        .where(RegistrationLink.token_id == token_id)
    ).scalar_one_or_none()
    if link is None or link.revoked_at is not None or link.used_at is not None:
        raise EntityNotFoundError("Registration link is invalid or already used")
    return link


def register_user_via_link(db: Session, token_id: str, payload: PublicRegistrationCreate) -> User:
    link = get_public_registration_link(db, token_id)
    login, password, display_name, communication_channel, region = _validate_public_registration_payload(db, payload)
    user = User(
        login=login,
        password_hash=get_password_hash(password),
        display_name=display_name,
        region=region,
        role=UserRole.USER,
        expires_at=None,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserResource(user_id=user.id))
    db.add(UserContactLink(user_id=user.id, value=communication_channel))
    link.used_by_user_id = user.id
    link.used_at = utc_now()
    db.add(link)
    db.commit()
    db.refresh(user)
    try:
        _auto_create_registration_interfaces(db, link, user)
    except Exception:
        db.rollback()
        cleanup_user = db.get(User, user.id)
        cleanup_link = db.get(RegistrationLink, link.id)
        actor = _resolve_registration_actor(db, link)
        if cleanup_user is not None:
            _delete_interfaces_for_user(db, cleanup_user, actor_user_id=actor.id if actor is not None else None)
            db.delete(cleanup_user)
        if cleanup_link is not None:
            cleanup_link.used_by_user_id = None
            cleanup_link.used_at = None
            db.add(cleanup_link)
        db.commit()
        raise
    write_audit_log(
        db,
        event_type="registration_links.used",
        severity="info",
        message=f"Registration link used for {user.login}.",
        message_ru=f"По ссылке регистрации создан аккаунт {user.login}.",
        target_user_id=user.id,
        details=f"registration_link_id={link.id};auto_create_interfaces={1 if link.auto_create_interfaces else 0}",
    )
    return user


def revoke_peer_download_link(db: Session, actor: User, link_id: int) -> None:
    require_admin(actor)
    link = db.get(PeerDownloadLink, link_id)
    if link is None:
        raise EntityNotFoundError("Download link not found")
    if link.revoked_at is None:
        link.revoked_at = utc_now()
        db.add(link)
        db.commit()
        write_audit_log(
            db,
            event_type="peer_links.revoke",
            severity="warning",
            message=f"Public peer download link revoked: {link.id}.",
            message_ru=f"Ссылка на скачивание пира отозвана: {link.id}.",
            actor_user_id=actor.id,
            target_user_id=link.peer.interface.user_id if link.peer and link.peer.interface else None,
        )


def revoke_peer_download_links(db: Session, actor: User, lifetime_only: bool = False) -> int:
    require_admin(actor)
    query = select(PeerDownloadLink).where(PeerDownloadLink.revoked_at.is_(None))
    if lifetime_only:
        query = query.join(PeerDownloadLink.peer).where(Peer.expires_at.is_(None))
    links = db.execute(query).scalars().all()
    now = utc_now()
    for link in links:
        link.revoked_at = now
        db.add(link)
    db.commit()
    write_audit_log(
        db,
        event_type="peer_links.revoke_bulk",
        severity="warning",
        message=f"Public peer download links revoked: {len(links)}; lifetime_only={lifetime_only}.",
        message_ru=(
            f"Отозваны бессрочные ссылки на скачивание пиров: {len(links)}."
            if lifetime_only
            else f"Отозваны все активные ссылки на скачивание пиров: {len(links)}."
        ),
        actor_user_id=actor.id,
    )
    return len(links)


def assign_interface_to_user(db: Session, actor: User, interface_id: int, user_id: int) -> None:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    user = get_user_by_id(db, user_id)
    ensure_interface_is_valid(interface)
    if interface.is_pending_owner is False:
        raise PermissionDeniedError("Only interfaces waiting for owner can be assigned")
    if count_user_interfaces(db, user.id) >= 5:
        raise PermissionDeniedError("One user can have at most five interfaces")
    interface.user_id = user.id
    interface.is_pending_owner = False
    for peer in interface.peers:
        peer.is_enabled = True
        db.add(peer)
    db.add(interface)
    db.commit()
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "toggle_interface",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"is_enabled": True}},
        ),
        actor_user_id=actor.id,
    )


def _delete_interface_runtime(db: Session, interface: Interface, *, actor_user_id: int | None = None) -> None:
    if not settings.peer_agent_command:
        return
    _ensure_interface_uses_tic_agent(interface)
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "delete_interface",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
        ),
        actor_user_id=actor_user_id,
    )


def _delete_interfaces_for_user(db: Session, user: User, *, actor_user_id: int | None = None) -> None:
    interfaces = (
        db.execute(
            select(Interface)
            .options(
                joinedload(Interface.peers),
                joinedload(Interface.tic_server),
                joinedload(Interface.tak_server),
            )
            .where(Interface.user_id == user.id)
            .order_by(Interface.id.asc())
        )
        .unique()
        .scalars()
        .all()
    )
    for interface in interfaces:
        _delete_interface_runtime(db, interface, actor_user_id=actor_user_id)
    for interface in interfaces:
        db.delete(interface)


def unassign_interface_from_user(db: Session, actor: User, interface_id: int, user_id: int) -> None:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    user = get_user_by_id(db, user_id)
    if interface.user_id != user.id:
        raise PermissionDeniedError("Interface is not attached to this user")
    interface.user_id = actor.id
    interface.is_pending_owner = True
    for peer in interface.peers:
        peer.is_enabled = False
        db.add(peer)
    db.add(interface)
    db.commit()
    _run_agent_executor_logged(
        db,
        _build_tic_executor_payload(
            "toggle_interface",
            interface,
            exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
            block_filters_enabled=block_filters_enabled(db),
            extra={"target_state": {"is_enabled": False}},
        ),
        actor_user_id=actor.id,
    )


def delete_pending_interface(db: Session, actor: User, interface_id: int) -> None:
    require_admin(actor)
    interface = get_interface_by_id(db, interface_id)
    if interface.is_pending_owner is False:
        raise PermissionDeniedError("Only interfaces waiting for owner can be deleted")
    if any(peer.is_enabled for peer in interface.peers):
        raise PermissionDeniedError("Disable the interface before deleting it")
    _delete_interface_runtime(db, interface, actor_user_id=actor.id)
    db.delete(interface)
    db.commit()


def create_user_with_interfaces(db: Session, actor: User, payload: AdminUserCreate) -> User:
    require_admin(actor)
    login = normalize_login(payload.login)
    existing = db.execute(select(User).where(User.login == login)).scalar_one_or_none()
    if existing is not None:
        raise PermissionDeniedError("Login already exists")
    interface_ids = list(dict.fromkeys(payload.interface_ids))
    if len(interface_ids) > 5:
        raise PermissionDeniedError("One user can have at most five interfaces")

    interfaces = []
    if interface_ids:
        interfaces = db.execute(select(Interface).where(Interface.id.in_(interface_ids))).scalars().all()
        if len(interfaces) != len(interface_ids):
            raise EntityNotFoundError("Some interfaces were not found")
        if any(interface.is_pending_owner is False for interface in interfaces):
            raise PermissionDeniedError("Only unassigned admin-owned interfaces can be attached")

    user = User(
        login=login,
        password_hash=get_password_hash(payload.password),
        display_name=payload.display_name.strip() if payload.display_name and payload.display_name.strip() else "-",
        role=UserRole.USER,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )
    db.add(user)
    db.flush()
    db.add(UserResource(user_id=user.id))
    db.add(UserContactLink(user_id=user.id, value=(payload.communication_channel.strip() if payload.communication_channel else None)))

    for interface in interfaces:
        interface.user_id = user.id
        interface.is_pending_owner = False
        for peer in interface.peers:
            peer.is_enabled = True
            db.add(peer)
        db.add(interface)

    db.commit()
    for interface in interfaces:
        _run_agent_executor_logged(
            db,
            _build_tic_executor_payload(
                "toggle_interface",
                interface,
                exclusion_filters_enabled=interface_exclusion_filters_enabled(db, interface),
                block_filters_enabled=block_filters_enabled(db),
                extra={"target_state": {"is_enabled": True}},
            ),
            actor_user_id=actor.id,
        )
        _refresh_via_tak_interface_runtime(db, interface, actor_user_id=actor.id)
    return user


def update_user_contact_link(db: Session, actor: User, user_id: int, payload: UserContactLinkUpdate) -> str | None:
    require_admin(actor)
    user = get_user_by_id(db, user_id)
    record = get_or_create_user_contact_link(db, user)
    record.value = payload.value.strip() if payload.value and payload.value.strip() else None
    db.add(record)
    db.commit()
    db.refresh(record)
    return record.value


def update_user_display_name(db: Session, actor: User, user_id: int, payload: UserDisplayNameUpdate) -> str:
    require_admin(actor)
    user = get_user_by_id(db, user_id)
    user.display_name = payload.value.strip() if payload.value and payload.value.strip() else "-"
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.display_name


def delete_user_account(db: Session, actor: User, user_id: int) -> None:
    require_admin(actor)
    user = get_user_by_id(db, user_id)
    if user.role == UserRole.ADMIN:
        raise PermissionDeniedError("Admin user cannot be deleted")
    _delete_interfaces_for_user(db, user, actor_user_id=actor.id)
    db.delete(user)
    db.commit()


def next_free_peer_slot(db: Session, interface_id: int) -> int:
    used_slots = db.execute(
        select(Peer.slot).where(Peer.interface_id == interface_id).order_by(Peer.slot.asc())
    ).scalars().all()
    expected = 1
    for slot in used_slots:
        if slot != expected:
            return expected
        expected += 1
    return expected


def count_user_interfaces(db: Session, user_id: int) -> int:
    return db.execute(
        select(func.count(Interface.id)).where(Interface.user_id == user_id, Interface.is_pending_owner.is_(False))
    ).scalar_one()
