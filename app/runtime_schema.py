from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def apply_legacy_runtime_schema_updates(engine: Engine) -> list[str]:
    """Apply compatibility patches for databases created before Alembic.

    Fresh installs should be created by migrations. These runtime updates stay
    intentionally small and idempotent so older local/dev databases can still
    start while we move the project toward migration-first setup.
    """
    applied: list[str] = []
    inspector = inspect(engine)
    datetime_type = "DATETIME" if engine.dialect.name == "sqlite" else "TIMESTAMP WITH TIME ZONE"
    primary_key = "INTEGER PRIMARY KEY" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "region" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN region VARCHAR(16)"))
        applied.append("users.region")

    peer_columns = {column["name"] for column in inspector.get_columns("peers")}
    if "expires_at" not in peer_columns:
        ddl = "ALTER TABLE peers ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE"
        if engine.dialect.name == "sqlite":
            ddl = "ALTER TABLE peers ADD COLUMN expires_at DATETIME"
        with engine.begin() as connection:
            connection.execute(text(ddl))
        applied.append("peers.expires_at")
    if "block_filters_enabled" not in peer_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE peers ADD COLUMN block_filters_enabled BOOLEAN DEFAULT 1"))
            connection.execute(text("UPDATE peers SET block_filters_enabled = 1 WHERE block_filters_enabled IS NULL"))
        applied.append("peers.block_filters_enabled")
    if "live_rx_bytes" not in peer_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE peers ADD COLUMN live_rx_bytes INTEGER"))
        applied.append("peers.live_rx_bytes")
    if "live_tx_bytes" not in peer_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE peers ADD COLUMN live_tx_bytes INTEGER"))
        applied.append("peers.live_tx_bytes")

    interface_columns = {column["name"] for column in inspect(engine).get_columns("interfaces")}
    if "agent_interface_id" not in interface_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE interfaces ADD COLUMN agent_interface_id VARCHAR(120)"))
        applied.append("interfaces.agent_interface_id")
    if "is_pending_owner" not in interface_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE interfaces ADD COLUMN is_pending_owner BOOLEAN DEFAULT 0"))
            connection.execute(
                text("UPDATE interfaces SET is_pending_owner = 1 WHERE user_id IN (SELECT id FROM users WHERE role = 'ADMIN')")
            )
        applied.append("interfaces.is_pending_owner")
    if "exclusion_filters_enabled" not in interface_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE interfaces ADD COLUMN exclusion_filters_enabled BOOLEAN DEFAULT 1"))
            connection.execute(text("UPDATE interfaces SET exclusion_filters_enabled = 1 WHERE exclusion_filters_enabled IS NULL"))
        applied.append("interfaces.exclusion_filters_enabled")
    if "tak_tunnel_fallback_active" not in interface_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE interfaces ADD COLUMN tak_tunnel_fallback_active BOOLEAN DEFAULT 0"))
            connection.execute(
                text("UPDATE interfaces SET tak_tunnel_fallback_active = 0 WHERE tak_tunnel_fallback_active IS NULL")
            )
        applied.append("interfaces.tak_tunnel_fallback_active")
    if "tak_tunnel_last_status" not in interface_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE interfaces ADD COLUMN tak_tunnel_last_status VARCHAR(32)"))
        applied.append("interfaces.tak_tunnel_last_status")

    server_columns = {column["name"] for column in inspect(engine).get_columns("servers")}
    with engine.begin() as connection:
        if "tic_region" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN tic_region VARCHAR(16)"))
            applied.append("servers.tic_region")
        if "tak_country" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN tak_country VARCHAR(120)"))
            applied.append("servers.tak_country")
        if "ssh_port" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN ssh_port INTEGER DEFAULT 22"))
            applied.append("servers.ssh_port")
        if "ssh_login" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN ssh_login VARCHAR(120)"))
            applied.append("servers.ssh_login")
        if "ssh_password" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN ssh_password VARCHAR(255)"))
            applied.append("servers.ssh_password")
        if "is_excluded" not in server_columns:
            connection.execute(text("ALTER TABLE servers ADD COLUMN is_excluded BOOLEAN DEFAULT 0"))
            applied.append("servers.is_excluded")
        if "last_seen_at" not in server_columns:
            ddl = "ALTER TABLE servers ADD COLUMN last_seen_at TIMESTAMP WITH TIME ZONE"
            if engine.dialect.name == "sqlite":
                ddl = "ALTER TABLE servers ADD COLUMN last_seen_at DATETIME"
            connection.execute(text(ddl))
            applied.append("servers.last_seen_at")

    bootstrap_tables = set(inspect(engine).get_table_names())
    if "server_bootstrap_tasks" in bootstrap_tables:
        bootstrap_columns = {column["name"] for column in inspect(engine).get_columns("server_bootstrap_tasks")}
        if "tic_region" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN tic_region VARCHAR(16)"))
            applied.append("server_bootstrap_tasks.tic_region")
        if "tak_country" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN tak_country VARCHAR(120)"))
            applied.append("server_bootstrap_tasks.tak_country")
        if "agent_task_id" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN agent_task_id INTEGER"))
            applied.append("server_bootstrap_tasks.agent_task_id")
        if "bootstrap_command_profile" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_command_profile VARCHAR(32)"))
            applied.append("server_bootstrap_tasks.bootstrap_command_profile")
        if "bootstrap_packages_json" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_packages_json TEXT"))
            applied.append("server_bootstrap_tasks.bootstrap_packages_json")
        if "bootstrap_safe_init_packages_json" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_safe_init_packages_json TEXT"))
            applied.append("server_bootstrap_tasks.bootstrap_safe_init_packages_json")
        if "bootstrap_full_only_packages_json" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_full_only_packages_json TEXT"))
            applied.append("server_bootstrap_tasks.bootstrap_full_only_packages_json")
        if "panel_job_id" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN panel_job_id INTEGER"))
            applied.append("server_bootstrap_tasks.panel_job_id")
        if "bootstrap_snapshot_json" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_snapshot_json TEXT"))
            applied.append("server_bootstrap_tasks.bootstrap_snapshot_json")
        if "bootstrap_execution_json" not in bootstrap_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE server_bootstrap_tasks ADD COLUMN bootstrap_execution_json TEXT"))
            applied.append("server_bootstrap_tasks.bootstrap_execution_json")

    filter_columns = {column["name"] for column in inspect(engine).get_columns("resource_filters")}
    if "peer_id" not in filter_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE resource_filters ADD COLUMN peer_id INTEGER"))
        applied.append("resource_filters.peer_id")
    if "kind" not in filter_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE resource_filters ADD COLUMN kind VARCHAR(16) DEFAULT 'EXCLUSION'"))
            connection.execute(text("UPDATE resource_filters SET kind = 'EXCLUSION' WHERE kind IS NULL"))
        applied.append("resource_filters.kind")
    else:
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("UPDATE resource_filters SET kind = 'EXCLUSION' WHERE kind::text = 'exclusion'"))
                connection.execute(text("UPDATE resource_filters SET kind = 'BLOCK' WHERE kind::text = 'block'"))
            else:
                connection.execute(text("UPDATE resource_filters SET kind = 'EXCLUSION' WHERE kind = 'exclusion'"))
                connection.execute(text("UPDATE resource_filters SET kind = 'BLOCK' WHERE kind = 'block'"))

    table_names = set(inspect(engine).get_table_names())
    if "registration_links" not in table_names:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE registration_links ("
                    f"id {primary_key}, "
                    "token_id VARCHAR(64) NOT NULL UNIQUE, "
                    "comment VARCHAR(255), "
                    "created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                    "used_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                    f"revoked_at {datetime_type}, "
                    f"used_at {datetime_type}, "
                    f"created_at {datetime_type} NOT NULL"
                    ")"
                )
            )
            connection.execute(text("CREATE UNIQUE INDEX ix_registration_links_token_id ON registration_links (token_id)"))
            connection.execute(text("CREATE INDEX ix_registration_links_created_at ON registration_links (created_at)"))
        applied.append("registration_links")
    else:
        registration_link_columns = {column["name"] for column in inspect(engine).get_columns("registration_links")}
        if "comment" not in registration_link_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE registration_links ADD COLUMN comment VARCHAR(255)"))
            applied.append("registration_links.comment")

    if "peer_download_links" not in table_names:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE peer_download_links ("
                    f"id {primary_key}, "
                    "token_id VARCHAR(64) NOT NULL UNIQUE, "
                    "peer_id INTEGER NOT NULL REFERENCES peers(id) ON DELETE CASCADE, "
                    "created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                    f"expires_at {datetime_type}, "
                    f"revoked_at {datetime_type}, "
                    f"created_at {datetime_type} NOT NULL"
                    ")"
                )
            )
            connection.execute(text("CREATE UNIQUE INDEX ix_peer_download_links_token_id ON peer_download_links (token_id)"))
        applied.append("peer_download_links")

    if "backup_records" not in table_names:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE backup_records ("
                    f"id {primary_key}, "
                    "backup_type VARCHAR(16) NOT NULL, "
                    "status VARCHAR(32) NOT NULL, "
                    "filename VARCHAR(255) NOT NULL, "
                    "storage_path TEXT NOT NULL, "
                    "size_bytes INTEGER NOT NULL DEFAULT 0, "
                    "contains_secrets BOOLEAN NOT NULL DEFAULT 1, "
                    "created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                    "manifest_json TEXT NOT NULL DEFAULT '{}', "
                    "error_message TEXT, "
                    f"created_at {datetime_type} NOT NULL, "
                    f"completed_at {datetime_type}"
                    ")"
                )
            )
        applied.append("backup_records")

    table_names = set(inspect(engine).get_table_names())
    if "panel_jobs" not in table_names:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE panel_jobs ("
                    f"id {primary_key}, "
                    "job_type VARCHAR(80) NOT NULL, "
                    "status VARCHAR(32) NOT NULL, "
                    "created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                    "progress_percent INTEGER NOT NULL DEFAULT 0, "
                    "current_stage VARCHAR(255) NOT NULL DEFAULT 'В очереди', "
                    "logs_json TEXT NOT NULL DEFAULT '[]', "
                    "error_message TEXT, "
                    f"created_at {datetime_type} NOT NULL, "
                    f"started_at {datetime_type}, "
                    f"completed_at {datetime_type}, "
                    f"updated_at {datetime_type} NOT NULL"
                    ")"
                )
            )
            connection.execute(text("CREATE INDEX ix_panel_jobs_created_at ON panel_jobs (created_at)"))
            connection.execute(text("CREATE INDEX ix_panel_jobs_job_type ON panel_jobs (job_type)"))
            connection.execute(text("CREATE INDEX ix_panel_jobs_status ON panel_jobs (status)"))
        applied.append("panel_jobs")

    if "panel_jobs" in set(inspect(engine).get_table_names()):
        panel_job_columns = {column["name"] for column in inspect(engine).get_columns("panel_jobs")}
        with engine.begin() as connection:
            if "progress_percent" not in panel_job_columns:
                connection.execute(text("ALTER TABLE panel_jobs ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0"))
                applied.append("panel_jobs.progress_percent")
            if "current_stage" not in panel_job_columns:
                connection.execute(text("ALTER TABLE panel_jobs ADD COLUMN current_stage VARCHAR(255) NOT NULL DEFAULT 'В очереди'"))
                applied.append("panel_jobs.current_stage")

    return applied
