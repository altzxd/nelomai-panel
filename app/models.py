from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class RouteMode(StrEnum):
    STANDALONE = "standalone"
    VIA_TAK = "via_tak"


class FilterType(StrEnum):
    IP = "ip"
    LINK = "link"


class FilterScope(StrEnum):
    GLOBAL = "global"
    USER = "user"


class FilterKind(StrEnum):
    EXCLUSION = "exclusion"
    BLOCK = "block"


class ServerType(StrEnum):
    TIC = "tic"
    TAK = "tak"
    STORAGE = "storage"


class BackupType(StrEnum):
    USERS = "users"
    SYSTEM = "system"
    FULL = "full"


class PanelJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STUCK = "stuck"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interfaces: Mapped[list["Interface"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    resources: Mapped["UserResource"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    filters: Mapped[list["ResourceFilter"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    contact_link_record: Mapped["UserContactLink | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="actor_user",
        foreign_keys="AuditLog.actor_user_id",
    )
    backup_records: Mapped[list["BackupRecord"]] = relationship(back_populates="created_by_user")


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    server_type: Mapped[ServerType] = mapped_column(Enum(ServerType))
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tic_interfaces: Mapped[list["Interface"]] = relationship(
        back_populates="tic_server",
        foreign_keys="Interface.tic_server_id",
    )
    tak_interfaces: Mapped[list["Interface"]] = relationship(
        back_populates="tak_server",
        foreign_keys="Interface.tak_server_id",
    )
    bootstrap_tasks: Mapped[list["ServerBootstrapTask"]] = relationship(back_populates="server")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="server")


class ServerBootstrapTask(Base):
    __tablename__ = "server_bootstrap_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"), nullable=True)
    agent_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    panel_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    server_name: Mapped[str] = mapped_column(String(120))
    server_type: Mapped[ServerType] = mapped_column(Enum(ServerType))
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_login: Mapped[str] = mapped_column(String(120))
    ssh_password: Mapped[str] = mapped_column(String(255))
    repository_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_command_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bootstrap_packages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_safe_init_packages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_full_only_packages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bootstrap_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_execution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    server: Mapped["Server | None"] = relationship(back_populates="bootstrap_tasks")


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_interface_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tic_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"))
    tak_server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"), nullable=True)
    route_mode: Mapped[RouteMode] = mapped_column(Enum(RouteMode), default=RouteMode.STANDALONE)
    tak_tunnel_fallback_active: Mapped[bool] = mapped_column(Boolean, default=False)
    tak_tunnel_last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    listen_port: Mapped[int] = mapped_column(Integer)
    address_v4: Mapped[str] = mapped_column(String(64))
    address_v6: Mapped[str | None] = mapped_column(String(64), nullable=True)
    peer_limit: Mapped[int] = mapped_column(Integer, default=5)
    exclusion_filters_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_pending_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="interfaces")
    tic_server: Mapped["Server"] = relationship(back_populates="tic_interfaces", foreign_keys=[tic_server_id])
    tak_server: Mapped["Server | None"] = relationship(back_populates="tak_interfaces", foreign_keys=[tak_server_id])
    peers: Mapped[list["Peer"]] = relationship(back_populates="interface", cascade="all, delete-orphan")


class Peer(Base):
    __tablename__ = "peers"
    __table_args__ = (
        UniqueConstraint("interface_id", "slot", name="uq_interface_peer_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(ForeignKey("interfaces.id", ondelete="CASCADE"))
    slot: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    block_filters_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handshake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    traffic_7d_mb: Mapped[int] = mapped_column(Integer, default=0)
    traffic_30d_mb: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interface: Mapped["Interface"] = relationship(back_populates="peers")
    filters: Mapped[list["ResourceFilter"]] = relationship(back_populates="peer", cascade="all, delete-orphan")
    download_links: Mapped[list["PeerDownloadLink"]] = relationship(back_populates="peer", cascade="all, delete-orphan")


class PeerDownloadLink(Base):
    __tablename__ = "peer_download_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    peer: Mapped["Peer"] = relationship(back_populates="download_links")
    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by_user_id])


class UserResource(Base):
    __tablename__ = "user_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    yandex_disk_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    amnezia_vpn_finland: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline_japan: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="resources")


class ResourceFilter(Base):
    __tablename__ = "resource_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    peer_id: Mapped[int | None] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[FilterKind] = mapped_column(Enum(FilterKind), default=FilterKind.EXCLUSION)
    filter_type: Mapped[FilterType] = mapped_column(Enum(FilterType))
    scope: Mapped[FilterScope] = mapped_column(Enum(FilterScope))
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User | None"] = relationship(back_populates="filters")
    peer: Mapped["Peer | None"] = relationship(back_populates="filters")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserContactLink(Base):
    __tablename__ = "user_contact_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="contact_link_record")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    message: Mapped[str] = mapped_column(Text)
    message_ru: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    actor_user: Mapped["User | None"] = relationship(
        back_populates="audit_logs",
        foreign_keys=[actor_user_id],
    )
    target_user: Mapped["User | None"] = relationship(foreign_keys=[target_user_id])
    server: Mapped["Server | None"] = relationship(back_populates="audit_logs")


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_type: Mapped[BackupType] = mapped_column(Enum(BackupType))
    status: Mapped[str] = mapped_column(String(32), default="running")
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    contains_secrets: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user: Mapped["User | None"] = relationship(back_populates="backup_records")


class PanelJob(Base):
    __tablename__ = "panel_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[PanelJobStatus] = mapped_column(Enum(PanelJobStatus), default=PanelJobStatus.QUEUED, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[str] = mapped_column(String(255), default="В очереди")
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by_user_id])
