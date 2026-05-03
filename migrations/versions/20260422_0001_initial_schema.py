"""Initial panel schema.

Revision ID: 20260422_0001
Revises:
Create Date: 2026-04-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260422_0001"
down_revision = None
branch_labels = None
depends_on = None


user_role = sa.Enum("ADMIN", "USER", name="userrole")
route_mode = sa.Enum("STANDALONE", "VIA_TAK", name="routemode")
filter_type = sa.Enum("IP", "LINK", name="filtertype")
filter_scope = sa.Enum("GLOBAL", "USER", name="filterscope")
filter_kind = sa.Enum("EXCLUSION", "BLOCK", name="filterkind")
server_type = sa.Enum("TIC", "TAK", "STORAGE", name="servertype")
tic_region = sa.Enum("EUROPE", "EAST", name="ticregion")
user_region = sa.Enum("EUROPE", "EAST", "UNKNOWN", name="userregion")
backup_type = sa.Enum("USERS", "SYSTEM", "FULL", name="backuptype")
panel_job_status = sa.Enum("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "STUCK", name="paneljobstatus")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("region", user_region, nullable=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_users_login"), "users", ["login"], unique=True)

    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("server_type", server_type, nullable=False),
        sa.Column("tic_region", tic_region, nullable=True),
        sa.Column("tak_country", sa.String(length=120), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_login", sa.String(length=120), nullable=True),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("is_excluded", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "server_bootstrap_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_task_id", sa.Integer(), nullable=True),
        sa.Column("panel_job_id", sa.Integer(), nullable=True),
        sa.Column("server_name", sa.String(length=120), nullable=False),
        sa.Column("server_type", server_type, nullable=False),
        sa.Column("tic_region", tic_region, nullable=True),
        sa.Column("tak_country", sa.String(length=120), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_login", sa.String(length=120), nullable=False),
        sa.Column("ssh_password", sa.String(length=255), nullable=False),
        sa.Column("repository_url", sa.Text(), nullable=True),
        sa.Column("bootstrap_command_profile", sa.String(length=32), nullable=True),
        sa.Column("bootstrap_packages_json", sa.Text(), nullable=True),
        sa.Column("bootstrap_safe_init_packages_json", sa.Text(), nullable=True),
        sa.Column("bootstrap_full_only_packages_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_prompt", sa.Text(), nullable=True),
        sa.Column("input_key", sa.String(length=120), nullable=True),
        sa.Column("input_kind", sa.String(length=32), nullable=True),
        sa.Column("bootstrap_snapshot_json", sa.Text(), nullable=True),
        sa.Column("bootstrap_execution_json", sa.Text(), nullable=True),
        sa.Column("logs_json", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "interfaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_interface_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tic_server_id", sa.Integer(), sa.ForeignKey("servers.id"), nullable=False),
        sa.Column("tak_server_id", sa.Integer(), sa.ForeignKey("servers.id"), nullable=True),
        sa.Column("route_mode", route_mode, nullable=False),
        sa.Column("tak_tunnel_fallback_active", sa.Boolean(), nullable=False),
        sa.Column("tak_tunnel_last_status", sa.String(length=32), nullable=True),
        sa.Column("listen_port", sa.Integer(), nullable=False),
        sa.Column("address_v4", sa.String(length=64), nullable=False),
        sa.Column("address_v6", sa.String(length=64), nullable=True),
        sa.Column("peer_limit", sa.Integer(), nullable=False),
        sa.Column("exclusion_filters_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_pending_owner", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "peers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("interface_id", sa.Integer(), sa.ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(length=255), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("block_filters_enabled", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handshake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_rx_bytes", sa.Integer(), nullable=True),
        sa.Column("live_tx_bytes", sa.Integer(), nullable=True),
        sa.Column("traffic_7d_mb", sa.Integer(), nullable=False),
        sa.Column("traffic_30d_mb", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interface_id", "slot", name="uq_interface_peer_slot"),
    )

    op.create_table(
        "user_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("yandex_disk_url", sa.Text(), nullable=True),
        sa.Column("amnezia_vpn_finland", sa.Text(), nullable=True),
        sa.Column("outline_japan", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "user_contact_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "resource_filters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("peer_id", sa.Integer(), sa.ForeignKey("peers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", filter_kind, nullable=False),
        sa.Column("filter_type", filter_type, nullable=False),
        sa.Column("scope", filter_scope, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "registration_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("used_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_id"),
    )
    op.create_index(op.f("ix_registration_links_token_id"), "registration_links", ["token_id"], unique=True)
    op.create_index(op.f("ix_registration_links_created_at"), "registration_links", ["created_at"], unique=False)

    op.create_table(
        "peer_download_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("peer_id", sa.Integer(), sa.ForeignKey("peers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_id"),
    )
    op.create_index(op.f("ix_peer_download_links_token_id"), "peer_download_links", ["token_id"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("message_ru", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_severity"), "audit_logs", ["severity"], unique=False)

    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("backup_type", backup_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("contains_secrets", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "panel_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", panel_job_status, nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_stage", sa.String(length=255), nullable=False, server_default="В очереди"),
        sa.Column("logs_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_panel_jobs_created_at"), "panel_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_panel_jobs_job_type"), "panel_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_panel_jobs_status"), "panel_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_panel_jobs_status"), table_name="panel_jobs")
    op.drop_index(op.f("ix_panel_jobs_job_type"), table_name="panel_jobs")
    op.drop_index(op.f("ix_panel_jobs_created_at"), table_name="panel_jobs")
    op.drop_table("panel_jobs")
    op.drop_table("backup_records")
    op.drop_index(op.f("ix_audit_logs_severity"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_event_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_registration_links_created_at"), table_name="registration_links")
    op.drop_index(op.f("ix_registration_links_token_id"), table_name="registration_links")
    op.drop_table("registration_links")
    op.drop_index(op.f("ix_peer_download_links_token_id"), table_name="peer_download_links")
    op.drop_table("peer_download_links")
    op.drop_table("resource_filters")
    op.drop_table("user_contact_links")
    op.drop_table("user_resources")
    op.drop_table("peers")
    op.drop_table("interfaces")
    op.drop_table("server_bootstrap_tasks")
    op.drop_table("app_settings")
    op.drop_table("servers")
    op.drop_index(op.f("ix_users_login"), table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        filter_kind.drop(bind, checkfirst=True)
        filter_scope.drop(bind, checkfirst=True)
        filter_type.drop(bind, checkfirst=True)
        route_mode.drop(bind, checkfirst=True)
        server_type.drop(bind, checkfirst=True)
        tic_region.drop(bind, checkfirst=True)
        user_region.drop(bind, checkfirst=True)
        backup_type.drop(bind, checkfirst=True)
        panel_job_status.drop(bind, checkfirst=True)
        user_role.drop(bind, checkfirst=True)
