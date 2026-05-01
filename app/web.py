from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import Base, SessionLocal, engine, get_db
from app.models import FilterKind, Interface, Server, User, UserRole
from app.runtime_schema import apply_legacy_runtime_schema_updates
from app.schemas import (
    AdminFilterDeleteRequest,
    AuditLogCleanupRequest,
    AuditLogSettingsUpdate,
    AdminUserCreate,
    BackupCreateRequest,
    BackupBulkDeleteView,
    BackupRestoreApplyRequest,
    BackupRestoreApplyView,
    BackupRestorePlanRequest,
    BackupRestorePlanView,
    BackupServerSnapshotVerifyView,
    ServerBackupCleanupView,
    BackupSettingsUpdate,
    BasicSettingsUpdate,
    FilterCreate,
    FilterUpdate,
    FilterView,
    InterfaceCreate,
    InterfaceExclusionFiltersUpdate,
    InterfacePrepareRequest,
    InterfacePeerLimitUpdate,
    InterfaceRouteModeUpdate,
    InterfaceTakServerUpdate,
    LoginForm,
    PeerCommentUpdate,
    PeerBlockFiltersUpdate,
    PeerExpiryUpdate,
    PanelUpdateCheckView,
    ResourceItemView,
    ServerAgentUpdateApplyRequest,
    ServerAgentUpdateListView,
    ServerBootstrapInput,
    ServerBootstrapTaskView,
    ServerCreate,
    ServerRuntimeCheckView,
    UpdateSettingsUpdate,
    UserContactLinkUpdate,
    UserDisplayNameUpdate,
    UserExpiresUpdate,
    UserResourceUpdate,
)
from app.security import create_access_token, decode_access_token, decode_peer_download_token


def normalize_service_error_detail(detail: str) -> str:
    override_map = {
        "Server bootstrap task not found": "Задача настройки сервера не найдена.",
        "Bootstrap task does not require input": "Сейчас задача настройки сервера не ожидает ввод.",
        "Invalid interfaces cannot be assigned": "Недействительный интерфейс нельзя привязать к пользователю.",
        "Only excluded servers can be deleted": "Полностью удалить можно только исключённый сервер.",
        "Tak server is required for via_tak mode": "Для режима via_tak у интерфейса должен быть выбран Tak-сервер.",
        "Exclusion filters are disabled": "Фильтры исключения выключены в настройках.",
        "Block filters are disabled": "Фильтры блока выключены в настройках.",
    }
    if detail in override_map:
        return override_map[detail]
    return detail
from app.services import (
    EntityNotFoundError,
    InvalidInputError,
    PermissionDeniedError,
    ServerOperationUnavailableError,
    authenticate_user,
    create_filter,
    create_global_filter,
    create_interface_record,
    create_server_bootstrap_task,
    create_server_record,
    prepare_interface_creation,
    create_peer_for_interface,
    create_user_with_interfaces,
    delete_filters_bulk,
    delete_filter,
    delete_all_audit_logs,
    delete_audit_logs_older_than,
    delete_peer,
    download_interface_bundle,
    download_peer_config,
    download_peer_config_public,
    generate_peer_download_link,
    delete_user_account,
    delete_user_resources,
    ensure_can_edit_filter,
    ensure_can_write_user_resources,
    ensure_seed_data,
    get_basic_settings,
    get_audit_logs_page,
    get_admin_page_data,
    get_dashboard_data,
    get_filter_by_id,
    get_filters_view,
    get_agent_contract_page,
    get_panel_jobs_page,
    get_panel_diagnostics_page,
    get_server_bootstrap_task_view,
    get_servers_page_data,
    get_shared_peer_links_page,
    get_user_resources_view,
    exclude_server_record,
    recreate_peer,
    restore_server_record,
    revoke_peer_download_link,
    revoke_peer_download_links,
    run_expired_peers_cleanup_job,
    cancel_panel_job,
    has_problem_panel_jobs,
    delete_server_record,
    reboot_server_host,
    restart_server_agent,
    verify_server_status,
    verify_server_runtime,
    require_admin,
    resolve_target_user,
    assign_interface_to_user,
    toggle_interface_state,
    toggle_peer_state,
    unassign_interface_from_user,
    delete_pending_interface,
    update_interface_exclusion_filters,
    update_interface_peer_limit,
    update_interface_route_mode,
    update_interface_tak_server,
    update_peer_comment,
    update_peer_block_filters,
    update_peer_expiry,
    update_user_contact_link,
    update_user_display_name,
    update_user_expires_at,
    update_basic_settings,
    update_audit_log_settings,
    update_filter,
    submit_server_bootstrap_input,
    update_user_resources,
    human_error_message_ru,
    check_panel_updates,
    check_server_agent_updates,
    apply_server_agent_updates,
    create_backup,
    build_all_backups_archive,
    build_backup_restore_plan,
    cleanup_server_backup_copies,
    delete_panel_backups_except_latest,
    restore_backup_users,
    run_panel_diagnostics,
    delete_backup,
    get_backup_download_path,
    get_backups_page,
    run_scheduled_backup_if_due,
    update_backup_settings,
    verify_latest_full_backup_server_copies,
    write_audit_log,
    update_git_settings,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
moscow_tz = ZoneInfo("Europe/Moscow")

Base.metadata.create_all(bind=engine)
apply_legacy_runtime_schema_updates(engine)


def format_handshake(value):
    if value is None:
        return "Нет данных"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(moscow_tz).strftime("%d.%m.%Y %H:%M:%S")


templates.env.filters["handshake_moscow"] = format_handshake
templates.env.filters["datetime_moscow"] = format_handshake


def audit_event_type_for_status(status_code: int) -> str:
    if status_code in {400, 401, 403, 404, 503}:
        return f"http.{status_code}"
    return "http.error"


async def audit_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 400:
        with SessionLocal() as db:
            actor_user_id = None
            token = request.cookies.get("access_token")
            if token:
                try:
                    payload = decode_access_token(token)
                    subject = payload.get("sub")
                    user = db.query(User).filter(User.login == subject).one_or_none() if subject else None
                    actor_user_id = user.id if user else None
                except Exception:
                    actor_user_id = None
            detail = str(exc.detail or "")
            event_type = audit_event_type_for_status(exc.status_code)
            write_audit_log(
                db,
                event_type=event_type,
                severity="error",
                message=f"{request.method} {request.url.path}: {detail}",
                message_ru=human_error_message_ru(event_type, detail),
                actor_user_id=actor_user_id,
                details=detail,
            )
    return await http_exception_handler(request, exc)


def is_preview_mode(request: Request, current_user: User) -> bool:
    preview_flag = request.query_params.get("preview", "0")
    return current_user.role == UserRole.ADMIN and preview_flag in {"1", "true", "yes", "on"}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"})
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"}) from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"})

    user = db.query(User).filter(User.login == subject).one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"})
    return user


def raise_service_http_error(exc: Exception) -> None:
    detail = normalize_service_error_detail(str(exc))
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
    if isinstance(exc, PermissionDeniedError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from exc
    if isinstance(exc, InvalidInputError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    if isinstance(exc, ServerOperationUnavailableError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail) from exc
    raise exc


def normalize_service_error_detail(detail: str) -> str:
    exact_map = {
        "User not found": "Пользователь не найден.",
        "Filter not found": "Фильтр не найден.",
        "Interface not found": "Интерфейс не найден.",
        "Peer not found": "Пир не найден.",
        "Tic server not found": "Выбранный Tic-сервер не найден.",
        "Tak server not found": "Выбранный Tak-сервер не найден.",
        "Selected Tic server can be paired only with its matching Tak server": "Для выбранного Tic-сервера доступен только соответствующий Tak-сервер.",
        "Listen port is already used on this Tic server": "Выбранный listen_port уже занят на этом Tic-сервере.",
        "IPv4 address is already used on this Tic server": "Выбранный IPv4-адрес уже занят на этом Tic-сервере.",
        "Tic server did not return listen_port": "Tic-сервер не вернул свободный listen_port.",
        "Tic server did not return address_v4": "Tic-сервер не вернул свободный address_v4.",
        "Peer limit must be 5, 10, 15 or 20": "Лимит пиров должен быть 5, 10, 15 или 20.",
        "Listen port must be selected before creating the interface": "Сначала выберите или подтвердите listen_port для интерфейса.",
        "IPv4 address must be selected before creating the interface": "Сначала выберите или подтвердите IPv4-адрес для интерфейса.",
        "Preview mode is read-only": "Режим просмотра доступен только для чтения.",
        "Peer limit reached": "Достигнут лимит пиров для этого интерфейса.",
        "No free peer slot available": "Свободных слотов для нового пира больше нет.",
        "Login already exists": "Пользователь с таким логином уже существует.",
        "Some interfaces were not found": "Часть выбранных интерфейсов не найдена.",
        "Interface name already exists": "Интерфейс с таким именем уже существует.",
        "Users can download only their own peers": "Можно скачивать только свои пиры.",
        "Users can download only their own interfaces": "Можно скачивать только свои интерфейсы.",
        "Peer server executor is not configured": "Агент Tic-сервера не настроен.",
        "Peer server executor timed out": "Tic-сервер не ответил вовремя.",
        "Peer server executor returned invalid JSON": "Tic-сервер вернул некорректный ответ.",
        "Peer server executor did not return file content": "Tic-сервер не вернул содержимое файла.",
        "Peer server executor returned invalid file payload": "Tic-сервер вернул некорректный файл.",
    }
    if detail in exact_map:
        return exact_map[detail]
    if detail.startswith("Peer server executor failed to start:"):
        return "Не удалось запустить агент Tic-сервера."
    if detail == "Peer server executor failed":
        return "Tic-сервер не смог выполнить действие."
    return detail


def build_download_response(payload: dict[str, object]) -> Response:
    filename = str(payload["filename"]).replace('"', "_")
    return Response(
        content=payload["content"],
        media_type=str(payload["content_type"]),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def normalize_service_error_detail(detail: str) -> str:
    override_map = {
        "Assign an owner before enabling the interface": "Сначала назначьте владельца интерфейсу.",
        "Server not found": "Сервер не найден.",
        "Server name already exists": "Сервер с таким названием уже существует.",
        "Server connection fields must be filled in": "Заполните имя, адрес и SSH-параметры сервера.",
        "Git repository URL is required for the selected server type": "Сначала укажите Git-репозиторий для выбранного типа сервера в настройках.",
    }
    if detail in override_map:
        return override_map[detail]
    exact_map = {
        "User not found": "Пользователь не найден.",
        "Filter not found": "Фильтр не найден.",
        "Interface not found": "Интерфейс не найден.",
        "Peer not found": "Пир не найден.",
        "Tic server not found": "Выбранный Tic-сервер не найден.",
        "Tak server not found": "Выбранный Tak-сервер не найден.",
        "Selected Tic server can be paired only with its matching Tak server": "Для выбранного Tic-сервера доступен только соответствующий Tak-сервер.",
        "Listen port is already used on this Tic server": "Выбранный listen_port уже занят на этом Tic-сервере.",
        "IPv4 address is already used on this Tic server": "Выбранный IPv4-адрес уже занят на этом Tic-сервере.",
        "Tic server did not return listen_port": "Tic-сервер не вернул свободный listen_port.",
        "Tic server did not return address_v4": "Tic-сервер не вернул свободный address_v4.",
        "Peer limit must be 5, 10, 15 or 20": "Лимит пиров должен быть 5, 10, 15 или 20.",
        "Listen port must be selected before creating the interface": "Сначала выберите или подтвердите listen_port для интерфейса.",
        "IPv4 address must be selected before creating the interface": "Сначала выберите или подтвердите IPv4-адрес для интерфейса.",
        "Preview mode is read-only": "Режим просмотра доступен только для чтения.",
        "Peer limit reached": "Достигнут лимит пиров для этого интерфейса.",
        "No free peer slot available": "Свободных слотов для нового пира больше нет.",
        "Login already exists": "Пользователь с таким логином уже существует.",
        "Some interfaces were not found": "Часть выбранных интерфейсов не найдена.",
        "Interface name already exists": "Интерфейс с таким именем уже существует.",
        "Users can download only their own peers": "Можно скачивать только свои пиры.",
        "Users can download only their own interfaces": "Можно скачивать только свои интерфейсы.",
        "Peer server executor is not configured": "Агент Tic-сервера не настроен.",
        "Peer server executor timed out": "Tic-сервер не ответил вовремя.",
        "Peer server executor returned invalid JSON": "Tic-сервер вернул некорректный ответ.",
        "Peer server executor did not return file content": "Tic-сервер не вернул содержимое файла.",
        "Peer server executor returned invalid file payload": "Tic-сервер вернул некорректный файл.",
    }
    if detail in exact_map:
        return exact_map[detail]
    if detail.startswith("Peer server executor failed to start:"):
        return "Не удалось запустить агент Tic-сервера."
    if detail == "Peer server executor failed":
        return "Tic-сервер не смог выполнить действие."
    return detail


@router.get("/", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ensure_seed_data(db)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    form_data = await request.form()
    form = LoginForm(login=str(form_data.get("login", "")), password=str(form_data.get("password", "")))
    user = authenticate_user(db, form.login, form.password)
    if not user:
        write_audit_log(
            db,
            event_type="auth.login_failed",
            severity="error",
            message=f"Failed login for {form.login}",
            message_ru="Не удалось войти: неверный логин или пароль.",
            details=f"login={form.login}",
        )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    write_audit_log(
        db,
        event_type="auth.login_success",
        severity="info",
        message=f"User {user.login} logged in",
        message_ru=f"Пользователь {user.login} вошёл в панель.",
        actor_user_id=user.id,
    )
    redirect_target = "/admin" if user.role == UserRole.ADMIN else "/dashboard"
    response = RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=create_access_token(user.login),
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_access_token(token)
            subject = payload.get("sub")
            user = db.query(User).filter(User.login == subject).one_or_none() if subject else None
            write_audit_log(
                db,
                event_type="auth.logout",
                severity="info",
                message=f"User {subject or 'unknown'} logged out",
                message_ru=f"Пользователь {subject or 'unknown'} вышел из панели.",
                actor_user_id=user.id if user else None,
            )
        except Exception:
            pass
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    target_user_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    preview_mode = is_preview_mode(request, current_user)
    try:
        target_user = resolve_target_user(db, current_user, target_user_id)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)

    dashboard_data = get_dashboard_data(db, target_user, preview_mode=preview_mode)
    basic_settings = get_basic_settings(db)
    exclusion_filters_enabled = basic_settings.get("exclusion_filters_enabled", "1") == "1"
    block_filters_enabled = basic_settings.get("block_filters_enabled", "1") == "1"
    interface_count = len(dashboard_data.interfaces)
    disabled_exclusion_interface_count = sum(
        1 for interface in dashboard_data.interfaces if not interface.exclusion_filters_enabled
    )
    show_exclusion_filters_tab = exclusion_filters_enabled and not (
        interface_count == 1 and disabled_exclusion_interface_count == 1
    )
    warn_exclusion_filters_tab = (
        show_exclusion_filters_tab
        and interface_count > 1
        and disabled_exclusion_interface_count > 0
    )
    can_manage_resources = current_user.role == UserRole.ADMIN and not preview_mode
    can_manage_user_filters = not preview_mode and (
        current_user.role == UserRole.ADMIN or current_user.id == target_user.id
    )
    pending_interfaces = []
    if current_user.role == UserRole.ADMIN and not preview_mode:
        pending_interfaces = db.execute(
            select(Interface)
            .options(joinedload(Interface.tic_server), joinedload(Interface.user))
            .join(Server, Interface.tic_server_id == Server.id)
            .join(User, Interface.user_id == User.id)
            .where(Interface.is_pending_owner.is_(True))
            .order_by(Interface.name.asc())
        ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "dashboard": dashboard_data,
            "current_user": current_user,
            "preview_mode": preview_mode,
            "is_admin_view": current_user.role == UserRole.ADMIN and not preview_mode,
            "target_user_id": target_user.id,
            "can_manage_resources": can_manage_resources,
            "can_manage_user_filters": can_manage_user_filters,
            "exclusion_filters_enabled": exclusion_filters_enabled,
            "show_exclusion_filters_tab": show_exclusion_filters_tab,
            "warn_exclusion_filters_tab": warn_exclusion_filters_tab,
            "block_filters_enabled": block_filters_enabled,
            "contact_links": {
                "telegram": basic_settings["admin_telegram_url"],
                "vk": basic_settings["admin_vk_url"],
                "email": basic_settings["admin_email_url"],
                "group": basic_settings["admin_group_url"],
            },
            "moscow_now": "MSK",
            "pending_interfaces": pending_interfaces,
            "has_problem_jobs": has_problem_panel_jobs(db) if current_user.role == UserRole.ADMIN and not preview_mode else False,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    tic_server_id: int | None = Query(default=None),
    tak_server_id: int | None = Query(default=None),
    interface_tic_server_id: int | None = Query(default=None),
    filter_scope: str = Query(default="all"),
    settings_view: str = Query(default="basic"),
    tab: str = Query(default="overview"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        require_admin(current_user)
        admin_page = get_admin_page_data(
            db,
            current_user,
            tic_server_id=tic_server_id,
            tak_server_id=tak_server_id,
            filter_scope=filter_scope,
            filter_kind=FilterKind.BLOCK if settings_view == "block_filters" else FilterKind.EXCLUSION,
        )
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "admin_page": admin_page,
            "current_user": current_user,
            "active_tab": tab if tab in {"overview", "settings", "clients"} else "overview",
            "filter_scope": filter_scope if filter_scope in {"all", "global", "user"} else "all",
            "settings_view": settings_view if settings_view in {"basic", "logs", "updates", "backups", "shared_peers", "filters", "block_filters"} else "basic",
            "exclusion_filters_enabled": admin_page.settings.exclusion_filters_enabled,
            "block_filters_enabled": admin_page.settings.block_filters_enabled,
            "filter_kind": "block" if settings_view == "block_filters" else "exclusion",
            "interface_tic_server_id": interface_tic_server_id,
            "backups_page": get_backups_page(db, current_user) if settings_view == "backups" else None,
            "shared_peers_page": get_shared_peer_links_page(db, current_user) if settings_view == "shared_peers" else None,
            "has_problem_jobs": has_problem_panel_jobs(db),
        },
    )


@router.get("/admin/servers", response_class=HTMLResponse)
def admin_servers(
    request: Request,
    bucket: str = Query(default="active"),
    server_type: str = Query(default="all"),
    sort: str = Query(default="load_desc"),
    selected_server_id: int | None = Query(default=None),
    selected_bootstrap_task_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        require_admin(current_user)
        servers_page = get_servers_page_data(
            db,
            current_user,
            bucket=bucket,
            server_type=server_type,
            sort=sort,
            selected_server_id=selected_server_id,
            selected_bootstrap_task_id=selected_bootstrap_task_id,
        )
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)

    return templates.TemplateResponse(
        request,
        "admin_servers.html",
        {
            "servers_page": servers_page,
            "current_user": current_user,
            "has_problem_jobs": has_problem_panel_jobs(db),
        },
    )


@router.get("/admin/logs", response_class=HTMLResponse)
def admin_logs(
    request: Request,
    severity: str = Query(default="all"),
    event_type: str = Query(default="all"),
    user_id: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        selected_user_id = int(user_id) if user_id and user_id.strip() else None
        selected_server_id = int(server_id) if server_id and server_id.strip() else None
        logs_page = get_audit_logs_page(
            db,
            current_user,
            severity=severity,
            event_type=event_type,
            user_id=selected_user_id,
            server_id=selected_server_id,
            sort=sort,
        )
    except ValueError as exc:
        raise_service_http_error(InvalidInputError("Invalid logs filter value"))
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return templates.TemplateResponse(
        request,
        "admin_logs.html",
        {
            "logs_page": logs_page,
            "current_user": current_user,
            "has_problem_jobs": has_problem_panel_jobs(db),
        },
    )


@router.get("/admin/diagnostics", response_class=HTMLResponse)
def admin_diagnostics(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        diagnostics_page = get_panel_diagnostics_page(current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)
    return templates.TemplateResponse(
        request,
        "admin_diagnostics.html",
        {
            "diagnostics_page": diagnostics_page,
            "current_user": current_user,
            "has_problem_jobs": has_problem_panel_jobs(db),
        },
    )


@router.post("/admin/diagnostics/run", response_class=HTMLResponse)
def run_admin_diagnostics(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        diagnostics_page = run_panel_diagnostics(db, current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)
    return templates.TemplateResponse(
        request,
        "admin_diagnostics.html",
        {
            "diagnostics_page": diagnostics_page,
            "current_user": current_user,
            "has_problem_jobs": has_problem_panel_jobs(db),
        },
    )


@router.get("/admin/agent-contract", response_class=HTMLResponse)
def admin_agent_contract(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    try:
        contract_page = get_agent_contract_page(current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)
    return templates.TemplateResponse(
        request,
        "admin_agent_contract.html",
        {
            "contract_page": contract_page,
            "current_user": current_user,
            "has_problem_jobs": False,
        },
    )


@router.get("/api/admin/agent-contract")
def get_admin_agent_contract(
    current_user: User = Depends(get_current_user),
):
    try:
        return get_agent_contract_page(current_user).model_dump()
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.get("/admin/jobs", response_class=HTMLResponse)
def admin_jobs(
    request: Request,
    status_filter: str = Query(default="all", alias="status"),
    type_filter: str = Query(default="all", alias="type"),
    selected_job_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        jobs_page = get_panel_jobs_page(
            db,
            current_user,
            status_filter=status_filter,
            type_filter=type_filter,
            selected_job_id=selected_job_id,
        )
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)
    return templates.TemplateResponse(
        request,
        "admin_jobs.html",
        {
            "jobs_page": jobs_page,
            "current_user": current_user,
            "has_problem_jobs": jobs_page.has_problem_jobs,
        },
    )


@router.post("/api/admin/jobs/expired-peers/run", status_code=status.HTTP_201_CREATED)
def run_expired_peers_cleanup_job_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return run_expired_peers_cleanup_job(db, current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/jobs/{job_id}/cancel")
def cancel_panel_job_endpoint(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return cancel_panel_job(db, current_user, job_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/servers", response_model=ServerBootstrapTaskView, status_code=status.HTTP_201_CREATED)
def create_admin_server(
    payload: ServerCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerBootstrapTaskView:
    try:
        return create_server_bootstrap_task(db, current_user, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.get("/api/admin/server-bootstrap/{task_id}", response_model=ServerBootstrapTaskView)
def read_admin_server_bootstrap_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerBootstrapTaskView:
    try:
        return get_server_bootstrap_task_view(db, current_user, task_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/server-bootstrap/{task_id}/input", response_model=ServerBootstrapTaskView)
def send_admin_server_bootstrap_input(
    task_id: int,
    payload: ServerBootstrapInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerBootstrapTaskView:
    try:
        return submit_server_bootstrap_input(db, current_user, task_id, payload)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/servers/{server_id}/restart-agent", status_code=status.HTTP_204_NO_CONTENT)
def restart_admin_server_agent(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        restart_server_agent(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/servers/{server_id}/refresh")
def refresh_admin_server_status(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        is_active = verify_server_status(db, current_user, server_id)
        return {"is_active": is_active}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/servers/{server_id}/runtime-check", response_model=ServerRuntimeCheckView)
def runtime_check_admin_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerRuntimeCheckView:
    try:
        return verify_server_runtime(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/servers/{server_id}/reboot", status_code=status.HTTP_204_NO_CONTENT)
def reboot_admin_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        reboot_server_host(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/servers/{server_id}/exclude", status_code=status.HTTP_204_NO_CONTENT)
def exclude_admin_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        exclude_server_record(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/servers/{server_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_admin_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        restore_server_record(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/admin/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_server_record(db, current_user, server_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/users/{user_id}/resources", response_model=list[ResourceItemView])
def read_user_resources(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ResourceItemView]:
    try:
        target_user = resolve_target_user(db, current_user, user_id)
        return get_user_resources_view(db, target_user)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/users/{user_id}/resources", response_model=list[ResourceItemView])
def save_user_resources(
    request: Request,
    user_id: int,
    payload: UserResourceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ResourceItemView]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        target_user = resolve_target_user(db, current_user, user_id)
        ensure_can_write_user_resources(current_user, target_user, preview_mode)
        return update_user_resources(db, target_user, payload)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/users/{user_id}/resources", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_resources(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    preview_mode = is_preview_mode(request, current_user)
    try:
        target_user = resolve_target_user(db, current_user, user_id)
        ensure_can_write_user_resources(current_user, target_user, preview_mode)
        delete_user_resources(db, target_user)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/users/{user_id}/filters", response_model=list[FilterView])
def read_filters(
    user_id: int,
    kind: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FilterView]:
    try:
        target_user = resolve_target_user(db, current_user, user_id)
        try:
            filter_kind = FilterKind(kind) if kind else None
        except ValueError as exc:
            raise InvalidInputError("Unknown filter kind") from exc
        return get_filters_view(db, target_user, kind=filter_kind)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/users/{user_id}/filters", response_model=FilterView, status_code=status.HTTP_201_CREATED)
def add_filter(
    request: Request,
    user_id: int,
    payload: FilterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FilterView:
    preview_mode = is_preview_mode(request, current_user)
    try:
        target_user = resolve_target_user(db, current_user, user_id)
        return create_filter(db, current_user, target_user, payload, preview_mode=preview_mode)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/filters", response_model=FilterView, status_code=status.HTTP_201_CREATED)
def add_global_filter(
    payload: FilterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FilterView:
    try:
        return create_global_filter(db, current_user, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/filters/delete", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin_filters(
    payload: AdminFilterDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_filters_bulk(db, current_user, payload)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/api/filters/{filter_id}", response_model=FilterView)
def edit_filter(
    request: Request,
    filter_id: int,
    payload: FilterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FilterView:
    preview_mode = is_preview_mode(request, current_user)
    try:
        resource_filter = get_filter_by_id(db, filter_id)
        ensure_can_edit_filter(current_user, resource_filter, preview_mode)
        return update_filter(db, resource_filter, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_filter(
    request: Request,
    filter_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    preview_mode = is_preview_mode(request, current_user)
    try:
        resource_filter = get_filter_by_id(db, filter_id)
        ensure_can_edit_filter(current_user, resource_filter, preview_mode)
        delete_filter(db, resource_filter)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/api/admin/settings/basic")
def save_basic_settings(
    payload: BasicSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        require_admin(current_user)
        return update_basic_settings(db, payload)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/settings/updates")
def save_update_settings(
    payload: UpdateSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        return update_git_settings(db, current_user, payload)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/settings/logs")
def save_audit_log_settings(
    payload: AuditLogSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    try:
        retention_days = update_audit_log_settings(db, current_user, payload.retention_days)
        return {"retention_days": retention_days}
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/settings/backups")
def save_backup_settings(
    payload: BackupSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_backup_settings(db, current_user, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups", status_code=status.HTTP_201_CREATED)
def create_admin_backup(
    payload: BackupCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return create_backup(db, current_user, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups/scheduled/run-now", status_code=status.HTTP_201_CREATED)
def run_scheduled_admin_backup_now(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        record = run_scheduled_backup_if_due(db, force=True, actor=current_user)
        if record is None:
            raise InvalidInputError("Scheduled backup could not be started")
        return record
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups/latest-full/verify-server-copies", response_model=BackupServerSnapshotVerifyView)
def verify_latest_full_backup_server_copies_api(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupServerSnapshotVerifyView:
    try:
        return verify_latest_full_backup_server_copies(db, current_user)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.get("/api/admin/backups/download-all")
def download_all_admin_backups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        content, filename = build_all_backups_archive(db, current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=content, media_type="application/zip", headers=headers)


@router.post("/api/admin/backups/server-copies/cleanup", response_model=ServerBackupCleanupView)
def cleanup_server_backup_copies_api(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerBackupCleanupView:
    try:
        return cleanup_server_backup_copies(db, current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups/delete-all-except-latest", response_model=BackupBulkDeleteView)
def delete_all_panel_backups_except_latest_api(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupBulkDeleteView:
    try:
        return delete_panel_backups_except_latest(db, current_user)
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.get("/api/admin/backups/{backup_id}/download")
def download_admin_backup(
    backup_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    try:
        path = get_backup_download_path(db, current_user, backup_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.get("/api/admin/backups/{backup_id}/restore-plan", response_model=BackupRestorePlanView)
def read_admin_backup_restore_plan(
    backup_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupRestorePlanView:
    try:
        return build_backup_restore_plan(db, current_user, backup_id)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups/{backup_id}/restore-plan", response_model=BackupRestorePlanView)
def draft_admin_backup_restore_plan(
    backup_id: int,
    payload: BackupRestorePlanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupRestorePlanView:
    try:
        return build_backup_restore_plan(db, current_user, backup_id, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/backups/{backup_id}/restore-users", response_model=BackupRestoreApplyView)
def restore_admin_backup_users(
    backup_id: int,
    payload: BackupRestoreApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupRestoreApplyView:
    try:
        return restore_backup_users(db, current_user, backup_id, payload)
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/admin/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_backup(
    backup_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_backup(db, current_user, backup_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/admin/updates/check", response_model=PanelUpdateCheckView)
def check_panel_update_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelUpdateCheckView:
    try:
        return PanelUpdateCheckView(**check_panel_updates(db, current_user))
    except (PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.get("/api/admin/agent-updates/check", response_model=ServerAgentUpdateListView)
def check_server_agent_update_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerAgentUpdateListView:
    try:
        return ServerAgentUpdateListView(servers=check_server_agent_updates(db, current_user))
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/agent-updates/apply", response_model=ServerAgentUpdateListView)
def apply_server_agent_update_status(
    payload: ServerAgentUpdateApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerAgentUpdateListView:
    try:
        return ServerAgentUpdateListView(servers=apply_server_agent_updates(db, current_user, payload.server_id))
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/logs/cleanup")
def cleanup_old_audit_logs(
    payload: AuditLogCleanupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    try:
        deleted = delete_audit_logs_older_than(db, current_user, payload.keep_days)
        write_audit_log(
            db,
            event_type="audit_logs_cleanup",
            message=f"Deleted audit logs older than {payload.keep_days} days: {deleted}",
            message_ru=f"Удалены старые логи старше {payload.keep_days} дн.: {deleted}",
            severity="info",
            actor_user_id=current_user.id,
            details=f"keep_days={payload.keep_days}; deleted={deleted}",
        )
        return {"deleted": deleted}
    except (EntityNotFoundError, InvalidInputError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/admin/logs", status_code=status.HTTP_204_NO_CONTENT)
def cleanup_all_audit_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_all_audit_logs(db, current_user)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/logs/delete-all")
def cleanup_all_audit_logs_form(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        delete_all_audit_logs(db, current_user)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return RedirectResponse(url="/admin?tab=settings&settings_view=logs", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/admin/interfaces/{interface_id}/toggle")
def toggle_interface(
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        enabled = toggle_interface_state(db, current_user, interface_id)
        return {"is_enabled": enabled}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/interfaces/{interface_id}/peer-limit")
def save_interface_peer_limit(
    interface_id: int,
    payload: InterfacePeerLimitUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    try:
        peer_limit = update_interface_peer_limit(db, current_user, interface_id, payload)
        return {"peer_limit": peer_limit}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/interfaces/{interface_id}/route-mode")
def save_interface_route_mode(
    interface_id: int,
    payload: InterfaceRouteModeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        route_mode = update_interface_route_mode(db, current_user, interface_id, payload)
        return {"route_mode": route_mode.value}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/interfaces/{interface_id}/tak-server")
def save_interface_tak_server(
    interface_id: int,
    payload: InterfaceTakServerUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | int | None]:
    try:
        tak_server_id, route_mode = update_interface_tak_server(db, current_user, interface_id, payload)
        return {"tak_server_id": tak_server_id, "route_mode": route_mode.value}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/interfaces/{interface_id}/exclusion-filters")
def save_interface_exclusion_filters(
    interface_id: int,
    payload: InterfaceExclusionFiltersUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        enabled = update_interface_exclusion_filters(db, current_user, interface_id, payload)
        return {"enabled": enabled}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/interfaces", status_code=status.HTTP_201_CREATED)
def create_interface(
    payload: InterfaceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    try:
        interface = create_interface_record(db, current_user, payload)
        return {"id": interface.id, "name": interface.name}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/interfaces/prepare")
def prepare_interface(
    payload: InterfacePrepareRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    try:
        allocation = prepare_interface_creation(db, current_user, payload)
        return {
            "listen_port": allocation.listen_port,
            "address_v4": allocation.address_v4,
            "route_mode": allocation.route_mode.value,
        }
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/interfaces/{interface_id}/peers", status_code=status.HTTP_201_CREATED)
def create_interface_peer(
    request: Request,
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        peer_id = create_peer_for_interface(db, current_user, interface_id, preview_mode)
        return {"id": peer_id}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/peers/{peer_id}/comment")
def save_peer_comment(
    request: Request,
    peer_id: int,
    payload: PeerCommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        comment = update_peer_comment(db, current_user, peer_id, payload, preview_mode)
        return {"comment": comment}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/peers/{peer_id}/expires")
def save_peer_expiry(
    request: Request,
    peer_id: int,
    payload: PeerExpiryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        expires_at = update_peer_expiry(db, current_user, peer_id, payload, preview_mode)
        return {"expires_at": expires_at.isoformat() if expires_at else None}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/peers/{peer_id}/block-filters")
def save_peer_block_filters(
    request: Request,
    peer_id: int,
    payload: PeerBlockFiltersUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        enabled = update_peer_block_filters(db, current_user, peer_id, payload, preview_mode)
        return {"enabled": enabled}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/users/{user_id}/expires")
def save_user_expires(
    request: Request,
    user_id: int,
    payload: UserExpiresUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        expires_at = update_user_expires_at(db, current_user, user_id, payload, preview_mode)
        return {"expires_at": expires_at.isoformat() if expires_at else None}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/peers/{peer_id}/download-link")
def create_peer_download_link(
    request: Request,
    peer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        if preview_mode:
            raise PermissionDeniedError("Preview mode is read-only")
        url = generate_peer_download_link(db, current_user, peer_id, str(request.base_url))
        return {"url": url}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/admin/peer-download-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_peer_download_link_endpoint(
    link_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        revoke_peer_download_link(db, current_user, link_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/peer-download-links/revoke-all")
def revoke_all_peer_download_links_endpoint(
    lifetime_only: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    try:
        return {"revoked": revoke_peer_download_links(db, current_user, lifetime_only=lifetime_only)}
    except PermissionDeniedError as exc:
        raise_service_http_error(exc)


@router.post("/api/peers/{peer_id}/recreate")
def recreate_peer_endpoint(
    request: Request,
    peer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        peer = recreate_peer(db, current_user, peer_id, preview_mode)
        return {"id": peer.id, "slot": peer.slot}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.post("/api/peers/{peer_id}/toggle")
def toggle_peer_endpoint(
    request: Request,
    peer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    preview_mode = is_preview_mode(request, current_user)
    try:
        is_enabled = toggle_peer_state(db, current_user, peer_id, preview_mode)
        return {"is_enabled": is_enabled}
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.get("/downloads/peer/{token}")
def public_peer_download(
    token: str,
    db: Session = Depends(get_db),
) -> Response:
    try:
        payload = decode_peer_download_token(token)
        peer_id = int(payload["peer_id"])
        token_id = str(payload.get("jti") or "")
        download_payload = download_peer_config_public(db, peer_id, token_id)
        return build_download_response(download_payload)
    except Exception:
        return Response(content="Download link is invalid or expired.", status_code=status.HTTP_404_NOT_FOUND, media_type="text/plain; charset=utf-8")


@router.delete("/api/peers/{peer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_peer_endpoint(
    request: Request,
    peer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    preview_mode = is_preview_mode(request, current_user)
    try:
        delete_peer(db, current_user, peer_id, preview_mode)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/peers/{peer_id}/download")
def download_peer_config_endpoint(
    peer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        payload = download_peer_config(db, current_user, peer_id)
        return build_download_response(payload)
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.get("/api/interfaces/{interface_id}/download-all")
def download_interface_bundle_endpoint(
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        payload = download_interface_bundle(db, current_user, interface_id)
        return build_download_response(payload)
    except (EntityNotFoundError, PermissionDeniedError, ServerOperationUnavailableError) as exc:
        raise_service_http_error(exc)


@router.post("/api/admin/users/{user_id}/assign-interface/{interface_id}", status_code=status.HTTP_204_NO_CONTENT)
def attach_pending_interface(
    user_id: int,
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        assign_interface_to_user(db, current_user, interface_id, user_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/users/{user_id}/detach-interface/{interface_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_user_interface(
    request: Request,
    user_id: int,
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    preview_mode = is_preview_mode(request, current_user)
    try:
        if preview_mode:
            raise PermissionDeniedError("Preview mode is read-only")
        unassign_interface_from_user(db, current_user, interface_id, user_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/admin/interfaces/{interface_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_pending_interface(
    interface_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_pending_interface(db, current_user, interface_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/admin/users", status_code=status.HTTP_201_CREATED)
def create_admin_user(
    payload: AdminUserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    try:
        user = create_user_with_interfaces(db, current_user, payload)
        return {"id": user.id, "login": user.login}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.delete("/api/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_user_account(db, current_user, user_id)
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/api/admin/users/{user_id}/channel")
def save_user_channel(
    user_id: int,
    payload: UserContactLinkUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    try:
        value = update_user_contact_link(db, current_user, user_id, payload)
        return {"value": value}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)


@router.put("/api/admin/users/{user_id}/name")
def save_user_name(
    user_id: int,
    payload: UserDisplayNameUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        value = update_user_display_name(db, current_user, user_id, payload)
        return {"value": value}
    except (EntityNotFoundError, PermissionDeniedError) as exc:
        raise_service_http_error(exc)
from app.security import create_access_token, decode_access_token, decode_peer_download_token
