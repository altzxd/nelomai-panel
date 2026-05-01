from datetime import datetime

from pydantic import BaseModel, Field

from app.models import BackupType, FilterKind, FilterScope, FilterType, PanelJobStatus, RouteMode, UserRole


class LoginForm(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class PeerView(BaseModel):
    id: int
    slot: int
    comment: str | None
    is_enabled: bool
    block_filters_enabled: bool = True
    expires_at: datetime | None
    handshake_at: datetime | None
    traffic_7d_mb: int
    traffic_30d_mb: int


class PeerCommentUpdate(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class PeerExpiryUpdate(BaseModel):
    expires_at: datetime | None = None


class PeerBlockFiltersUpdate(BaseModel):
    enabled: bool


class SharedPeerLinkView(BaseModel):
    id: int
    peer_id: int
    interface_name: str
    peer_slot: int
    user_id: int
    user_login: str
    user_display_name: str
    peer_expires_at: datetime | None = None
    link_expires_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    is_revoked: bool
    is_expired: bool
    is_lifetime: bool


class SharedPeerLinksPageView(BaseModel):
    links: list[SharedPeerLinkView]
    active_count: int
    lifetime_count: int


class UserExpiresUpdate(BaseModel):
    expires_at: datetime | None = None


class InterfaceTakOptionView(BaseModel):
    id: int
    name: str


class InterfaceView(BaseModel):
    id: int
    agent_interface_id: str | None = None
    name: str
    tic_server_name: str
    route_mode: RouteMode
    tak_server_id: int | None = None
    tak_server_name: str | None = None
    available_tak_options: list[InterfaceTakOptionView] = []
    peer_limit: int
    expires_at: datetime | None
    is_enabled: bool
    is_invalid: bool = False
    exclusion_filters_enabled: bool = True
    peers: list[PeerView]


class ResourceItemView(BaseModel):
    key: str
    label: str
    value: str | None


class UserResourceUpdate(BaseModel):
    yandex_disk_url: str | None = None
    amnezia_vpn_finland: str | None = None
    outline_japan: str | None = None


class FilterView(BaseModel):
    id: int
    name: str
    kind: FilterKind = FilterKind.EXCLUSION
    peer_id: int | None = None
    peer_label: str | None = None
    filter_type: FilterType
    scope: FilterScope
    value: str
    description: str | None
    is_active: bool
    owner_users: list[dict[str, str | int]] = []
    delete_ids: list[int] = []


class FilterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: FilterKind = FilterKind.EXCLUSION
    peer_id: int | None = None
    filter_type: FilterType
    scope: FilterScope
    value: str = Field(min_length=1)
    description: str | None = None
    is_active: bool = True


class FilterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filter_type: FilterType | None = None
    value: str | None = Field(default=None, min_length=1)
    description: str | None = None
    is_active: bool | None = None


class ServerOptionView(BaseModel):
    id: int
    name: str


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    server_type: str = Field(pattern="^(tic|tak|storage)$")
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_login: str = Field(min_length=1, max_length=120)
    ssh_password: str = Field(min_length=1, max_length=255)


class ServerBootstrapPendingInputView(BaseModel):
    key: str | None = None
    kind: str | None = None
    prompt: str | None = None
    step_index: int | None = None


class ServerBootstrapSnapshotView(BaseModel):
    mode: str | None = None
    transport: str | None = None
    applied: bool = False
    planned: bool = True
    command_count: int = 0
    executed_step_count: int = 0
    current_step_index: int | None = None
    current_step_status: str | None = None
    resume_from_step: int = 1
    waiting_for_input: bool = False
    pending_input: ServerBootstrapPendingInputView | None = None


class ServerBootstrapStepView(BaseModel):
    index: int
    status: str
    status_label: str
    command: str
    note: str | None = None
    stdout: str | None = None
    stderr: str | None = None


class ServerBootstrapTaskView(BaseModel):
    id: int
    status: str
    logs: list[str]
    bootstrap_command_profile: str | None = None
    bootstrap_packages: list[str] = []
    bootstrap_safe_init_packages: list[str] = []
    bootstrap_full_only_packages: list[str] = []
    input_prompt: str | None = None
    input_key: str | None = None
    input_kind: str | None = None
    bootstrap_snapshot: ServerBootstrapSnapshotView | None = None
    bootstrap_steps: list[ServerBootstrapStepView] = []
    bootstrap_last_step_error: str | None = None
    server_id: int | None = None
    last_error: str | None = None


class ServerBootstrapInput(BaseModel):
    value: str | None = None


class ServerBootstrapListItemView(BaseModel):
    id: int
    name: str
    host: str
    server_type: str
    ssh_port: int
    status: str
    status_label: str
    logs: list[str] = []
    last_error: str | None = None
    panel_job_id: int | None = None
    panel_job_status: str | None = None
    panel_job_stage: str | None = None
    panel_job_progress: int | None = None
    bootstrap_command_profile: str | None = None
    bootstrap_packages: list[str] = []
    bootstrap_safe_init_packages: list[str] = []
    bootstrap_full_only_packages: list[str] = []
    bootstrap_snapshot: ServerBootstrapSnapshotView | None = None
    bootstrap_pending_command: str | None = None
    bootstrap_steps: list[ServerBootstrapStepView] = []
    bootstrap_last_step_error: str | None = None


class AuditLogView(BaseModel):
    id: int
    event_type: str
    event_type_label: str
    severity: str
    message: str
    message_ru: str
    actor_user_id: int | None = None
    actor_login: str | None = None
    target_user_id: int | None = None
    target_login: str | None = None
    server_id: int | None = None
    server_name: str | None = None
    details: str | None = None
    details_ru: str | None = None
    created_at: datetime


class AuditLogsPageView(BaseModel):
    logs: list[AuditLogView]
    event_types: list[str]
    event_type_labels: dict[str, str]
    users: list[ServerOptionView]
    servers: list[ServerOptionView]
    selected_severity: str
    selected_event_type: str
    selected_user_id: int | None = None
    selected_server_id: int | None = None
    selected_sort: str


class DiagnosticsCheckView(BaseModel):
    key: str
    title: str
    status: str
    message: str
    details: list[str] = []
    source_label: str | None = None
    source_url: str | None = None


class DiagnosticsRecommendationView(BaseModel):
    key: str
    title: str
    message: str
    severity: str = "info"
    action_label: str | None = None
    action_url: str | None = None


class DiagnosticsPageView(BaseModel):
    has_report: bool
    overall_status: str
    summary: str
    problem_nodes: list[str] = []
    checks: list[DiagnosticsCheckView] = []
    recommendations: list[DiagnosticsRecommendationView] = []
    recent_incidents: list[AuditLogView] = []
    run_history: list[AuditLogView] = []


class AgentContractActionView(BaseModel):
    action: str
    component: str
    component_label: str
    capabilities: list[str]


class AgentContractPageView(BaseModel):
    contract_version: str
    supported_contracts: list[str]
    panel_version: str
    components: list[dict[str, str]]
    actions: list[AgentContractActionView]
    raw_markdown: str
    doc_path: str


class PanelJobView(BaseModel):
    id: int
    job_type: str
    job_type_label: str
    status: PanelJobStatus
    status_label: str
    progress_percent: int
    current_stage: str
    created_by_login: str | None = None
    logs: list[str] = []
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
    can_cancel: bool
    source_label: str | None = None
    source_url: str | None = None
    bootstrap_command_profile: str | None = None
    bootstrap_packages: list[str] = []
    bootstrap_safe_init_packages: list[str] = []
    bootstrap_full_only_packages: list[str] = []
    bootstrap_snapshot: ServerBootstrapSnapshotView | None = None
    bootstrap_pending_command: str | None = None
    bootstrap_steps: list[ServerBootstrapStepView] = []
    bootstrap_last_step_error: str | None = None


class PanelJobsPageView(BaseModel):
    jobs: list[PanelJobView]
    selected_status: str
    selected_type: str
    has_problem_jobs: bool
    has_active_jobs: bool
    selected_job_id: int | None = None


class ServerCardView(BaseModel):
    key: str
    name: str
    host: str
    available: bool
    status: str = "unknown"
    last_seen_at: datetime | None = None
    metrics_note: str = ""
    cpu_percent: float
    ram_percent: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    traffic_mbps: float | None
    selected_id: int | None = None
    options: list[ServerOptionView] = []


class ServerListItemView(BaseModel):
    id: int
    name: str
    host: str
    server_type: str
    available: bool
    status: str
    last_seen_at: datetime | None = None
    metrics_note: str = ""
    ssh_port: int
    cpu_percent: float
    ram_percent: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    traffic_mbps: float | None
    interface_count: int
    endpoint_count: int
    peer_count: int
    is_excluded: bool = False
    owner_interface_names: list[str] = []
    endpoint_interface_names: list[str] = []


class ServerDetailView(BaseModel):
    id: int
    name: str
    host: str
    server_type: str
    status: str
    last_seen_at: datetime | None = None
    metrics_note: str = ""
    ssh_port: int
    ssh_login: str | None
    cpu_percent: float
    ram_percent: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    traffic_mbps: float | None
    interface_count: int
    endpoint_count: int
    peer_count: int
    is_excluded: bool = False
    owner_interface_names: list[str] = []
    endpoint_interface_names: list[str] = []


class ServerRuntimeCheckItemView(BaseModel):
    key: str
    label: str
    status: str
    message: str


class ServerRuntimeCheckView(BaseModel):
    server_id: int
    ready: bool
    mode: str
    runtime_root: str | None = None
    wireguard_root: str | None = None
    peers_root: str | None = None
    checks: list[ServerRuntimeCheckItemView] = []


class InterfaceSummaryView(BaseModel):
    id: int
    agent_interface_id: str | None = None
    name: str
    owner_id: int | None
    owner_name: str
    owner_login: str | None = None
    tic_server_id: int
    tic_server_name: str
    route_mode: RouteMode
    traffic_30d_gb: float
    active_peers: int
    peer_limit: int
    is_enabled: bool
    is_invalid: bool = False


class BasicSettingsView(BaseModel):
    current_version: str = "0.0.0"
    nelomai_git_repo: str = ""
    dns_server: str
    mtu: int
    keepalive: int
    exclusion_filters_enabled: bool = True
    block_filters_enabled: bool = True
    admin_telegram_url: str
    admin_vk_url: str
    admin_email_url: str
    admin_group_url: str
    audit_log_retention_days: int = 30


class BasicSettingsUpdate(BaseModel):
    dns_server: str = Field(min_length=1, max_length=120)
    mtu: int = Field(ge=576, le=9000)
    keepalive: int = Field(ge=0, le=600)
    exclusion_filters_enabled: bool = True
    block_filters_enabled: bool = True
    admin_telegram_url: str = ""
    admin_vk_url: str = ""
    admin_email_url: str = ""
    admin_group_url: str = ""


class UpdateSettingsUpdate(BaseModel):
    nelomai_git_repo: str = ""


class AuditLogSettingsUpdate(BaseModel):
    retention_days: int = Field(ge=1, le=365)


class AuditLogCleanupRequest(BaseModel):
    keep_days: int = Field(ge=1, le=30)


class PanelUpdateCheckView(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool
    repo_url: str
    release_url: str | None = None
    message: str


class ServerAgentUpdateView(BaseModel):
    server_id: int
    name: str
    server_type: str
    repository_url: str
    status: str
    agent_version: str | None = None
    contract_version: str | None = None
    capabilities: list[str] = []
    is_legacy: bool = False
    current_version: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    message: str


class ServerAgentUpdateListView(BaseModel):
    servers: list[ServerAgentUpdateView]


class ServerAgentUpdateApplyRequest(BaseModel):
    server_id: int | None = None


class BackupSettingsUpdate(BaseModel):
    backups_enabled: bool = True
    backup_frequency: str = Field(pattern="^(daily|every_3_days|weekly)$")
    backup_time: str = Field(pattern="^([01]\\d|2[0-3]):[0-5]\\d$")
    backup_retention_days: int = Field(ge=1, le=365)
    backup_storage_path: str = Field(min_length=1, max_length=500)
    server_backup_retention_days: int = Field(default=90, ge=1, le=365)
    server_backup_size_limit_mb: int = Field(default=5120, ge=100, le=102400)
    server_backup_monthly_retention_days: int = Field(default=365, ge=30, le=1825)
    server_backup_monthly_size_limit_mb: int = Field(default=3072, ge=100, le=102400)
    backup_remote_storage_server_id: int | None = None


class BackupCreateRequest(BaseModel):
    backup_type: BackupType


class BackupRestorePlanRequest(BaseModel):
    user_ids: list[int] = Field(default_factory=list)


class BackupRestoreApplyRequest(BaseModel):
    user_ids: list[int] = Field(min_length=1)
    user_login_overrides: dict[int, str] = Field(default_factory=dict)
    interface_port_overrides: dict[int, int] = Field(default_factory=dict)
    interface_address_overrides: dict[int, str] = Field(default_factory=dict)


class BackupRecordView(BaseModel):
    id: int
    backup_type: BackupType
    status: str
    filename: str
    size_bytes: int
    size_label: str
    contains_secrets: bool
    created_by_login: str | None = None
    created_at: datetime
    created_at_local: datetime
    created_label: str
    completed_at: datetime | None = None
    error_message: str | None = None


class BackupCleanupView(BaseModel):
    deleted_count: int
    freed_bytes: int
    freed_size_label: str
    retention_days: int
    protected_full_backup_id: int | None = None


class BackupBulkDeleteView(BaseModel):
    deleted_count: int
    freed_bytes: int
    freed_size_label: str
    protected_backup_id: int | None = None


class ServerBackupCleanupItemView(BaseModel):
    server_id: int
    server_name: str
    server_type: str
    status: str
    message: str
    deleted_count: int | None = None


class ServerBackupCleanupView(BaseModel):
    status: str
    items: list[ServerBackupCleanupItemView]


class BackupRestoreConflictView(BaseModel):
    conflict_type: str
    severity: str
    message: str
    current_owner: str | None = None
    backup_user_id: int | None = None
    backup_interface_id: int | None = None


class BackupRestoreUserPlanView(BaseModel):
    backup_user_id: int
    login: str
    display_name: str
    selected: bool = True
    status: str
    existing_user_id: int | None = None
    interface_count: int
    peer_count: int
    conflicts: list[BackupRestoreConflictView] = []


class BackupRestorePlanView(BaseModel):
    backup_id: int
    backup_type: BackupType
    filename: str
    backup_version: str
    contains_secrets: bool
    can_restore_users: bool
    can_restore_system: bool = False
    can_restore_server_snapshots: bool = False
    restore_scope: str = "preview_only"
    summary: dict[str, int | str | bool]
    system_summary: dict[str, int | str | bool] = {}
    archive_files: list[str] = []
    users: list[BackupRestoreUserPlanView] = []
    server_snapshots: list[dict[str, str | int | bool | None]] = []
    warnings: list[str] = []


class BackupRestoreApplyView(BaseModel):
    status: str
    restored_users: int
    restored_interfaces: int
    restored_peers: int
    restored_filters: int
    plan: BackupRestorePlanView


class BackupServerSnapshotVerifyItemView(BaseModel):
    server_id: int
    server_name: str
    server_type: str
    snapshot_filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    status: str
    message: str


class BackupServerSnapshotVerifyView(BaseModel):
    backup_id: int
    filename: str
    created_at: datetime
    status: str
    items: list[BackupServerSnapshotVerifyItemView]


class BackupSettingsView(BaseModel):
    backups_enabled: bool
    backup_frequency: str
    backup_time: str
    backup_retention_days: int
    backup_storage_path: str
    backup_last_run_at: datetime | None = None
    backup_next_run_at: datetime | None = None
    server_backup_retention_days: int = 90
    server_backup_size_limit_mb: int = 5120
    server_backup_monthly_retention_days: int = 365
    server_backup_monthly_size_limit_mb: int = 3072
    backup_remote_storage_server_id: int | None = None


class BackupsPageView(BaseModel):
    settings: BackupSettingsView
    backups: list[BackupRecordView]
    storage_server_options: list[ServerOptionView] = []


class ClientInterfaceOptionView(BaseModel):
    id: int
    name: str
    owner_name: str


class ClientView(BaseModel):
    id: int
    login: str
    display_name: str
    role: UserRole
    interface_count: int
    communication_channel: str | None
    can_delete: bool


class AdminUserCreate(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)
    interface_ids: list[int] = Field(default_factory=list, max_length=5)
    display_name: str | None = None
    communication_channel: str | None = None


class UserContactLinkUpdate(BaseModel):
    value: str | None = None


class UserDisplayNameUpdate(BaseModel):
    value: str | None = None


class AdminFilterDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1)


class InterfaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tic_server_id: int
    tak_server_id: int | None = None
    listen_port: int | None = Field(default=None, ge=10001, le=65535)
    address_v4: str | None = None
    peer_limit: int = Field(default=5)


class InterfacePrepareRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tic_server_id: int
    tak_server_id: int | None = None


class InterfaceAllocationView(BaseModel):
    listen_port: int
    address_v4: str
    route_mode: RouteMode


class InterfacePeerLimitUpdate(BaseModel):
    peer_limit: int


class InterfaceRouteModeUpdate(BaseModel):
    route_mode: RouteMode


class InterfaceTakServerUpdate(BaseModel):
    tak_server_id: int | None = None


class InterfaceExclusionFiltersUpdate(BaseModel):
    enabled: bool


class AdminPageView(BaseModel):
    panel_server: ServerCardView
    tic_server: ServerCardView
    tak_server: ServerCardView
    interfaces: list[InterfaceSummaryView]
    settings: BasicSettingsView
    filters: list[FilterView]
    clients: list[ClientView]
    client_interface_options: list[ClientInterfaceOptionView]
    available_tic_servers: list[ServerOptionView]
    available_tak_servers: list[ServerOptionView]


class ServersPageView(BaseModel):
    servers: list[ServerListItemView]
    excluded_servers: list[ServerListItemView] = []
    pending_bootstrap_tasks: list[ServerBootstrapListItemView] = []
    selected_bucket: str = "active"
    selected_type: str
    selected_sort: str
    selected_server: ServerDetailView | None = None
    selected_bootstrap_task_id: int | None = None


class UserDashboardView(BaseModel):
    id: int
    login: str
    display_name: str
    role: UserRole
    preview_mode: bool
    interfaces: list[InterfaceView]
    resources: list[ResourceItemView]
    filters: list[FilterView]
