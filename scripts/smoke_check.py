from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.config import settings
from app.main import app
from app.models import AppSetting, BackupRecord, BackupType, Interface, Peer, PeerDownloadLink, ResourceFilter, User, UserRole
from app.security import create_access_token
from app.services import delete_backup, ensure_default_settings, ensure_seed_data, get_backup_settings, get_shared_peer_links_page, run_scheduled_backup_if_due
from app.web import format_handshake


SMOKE_FILTER_PREFIX = "smoke-check-"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class SmokeFailure(RuntimeError):
    pass


def assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:500].replace("\n", " ")
        raise SmokeFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def cleanup_smoke_filters() -> None:
    with SessionLocal() as db:
        filters = db.execute(
            select(ResourceFilter).where(ResourceFilter.name.like(f"{SMOKE_FILTER_PREFIX}%"))
        ).scalars().all()
        for resource_filter in filters:
            db.delete(resource_filter)
        db.commit()


def prepare_data() -> tuple[int, int, int | None, int | None, int | None, int | None, str | None, int]:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        previous_block_value = db.get(AppSetting, "block_filters_enabled")
        original_block_value = previous_block_value.value if previous_block_value else None
        audit_retention_setting = db.get(AppSetting, "audit_log_retention_days")
        try:
            original_audit_retention_days = int(audit_retention_setting.value) if audit_retention_setting else 30
        except (TypeError, ValueError):
            original_audit_retention_days = 30
        original_audit_retention_days = max(1, min(original_audit_retention_days, 365))
        setting = previous_block_value or AppSetting(key="block_filters_enabled", value="1")
        setting.value = "1"
        db.add(setting)
        db.commit()

        admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        user = db.execute(select(User).where(User.role == UserRole.USER).order_by(User.id.asc())).scalars().first()
        if admin is None:
            raise SmokeFailure("No admin user found")
        if user is None:
            raise SmokeFailure("No regular user found")

        peer = db.execute(
            select(Peer)
            .join(Interface)
            .where(Interface.user_id == user.id, Interface.is_pending_owner.is_(False))
            .order_by(Peer.id.asc())
        ).scalars().first()
        other_user = db.execute(
            select(User)
            .where(User.role == UserRole.USER, User.id != user.id)
            .order_by(User.id.asc())
        ).scalars().first()
        foreign_interface = None
        foreign_peer = None
        if other_user is not None:
            foreign_interface = db.execute(
                select(Interface)
                .where(Interface.user_id == other_user.id, Interface.is_pending_owner.is_(False))
                .order_by(Interface.id.asc())
            ).scalars().first()
            if foreign_interface is not None:
                foreign_peer = db.execute(
                    select(Peer)
                    .where(Peer.interface_id == foreign_interface.id)
                    .order_by(Peer.id.asc())
                ).scalars().first()

        return (
            admin.id,
            user.id,
            peer.id if peer else None,
            other_user.id if other_user else None,
            foreign_interface.id if foreign_interface else None,
            foreign_peer.id if foreign_peer else None,
            original_block_value,
            original_audit_retention_days,
        )


def restore_block_setting(original_value: str | None) -> None:
    with SessionLocal() as db:
        setting = db.get(AppSetting, "block_filters_enabled")
        if setting is None:
            return
        if original_value is None:
            db.delete(setting)
        else:
            setting.value = original_value
            db.add(setting)
        db.commit()


def load_user(user_id: int) -> User:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise SmokeFailure(f"User {user_id} disappeared during smoke check")
        db.expunge(user)
        return user


def run() -> None:
    cleanup_smoke_filters()
    (
        admin_id,
        user_id,
        peer_id,
        other_user_id,
        foreign_interface_id,
        foreign_peer_id,
        original_block_value,
        original_audit_retention_days,
    ) = prepare_data()
    admin = load_user(admin_id)
    user = load_user(user_id)
    admin_headers = auth_headers(admin)
    user_headers = auth_headers(user)

    try:
        with TestClient(app) as client:
            assert_status(client.get("/"), 200, "login page")
            assert_status(client.get("/dashboard", follow_redirects=False), 303, "unauthorized dashboard redirect")
            assert_status(client.get("/admin", headers=user_headers), 403, "regular user cannot open admin")
            assert_status(client.get("/admin/servers", headers=user_headers), 403, "regular user cannot open servers page")
            assert_status(client.get("/admin/logs", headers=user_headers), 403, "regular user cannot open logs page")
            assert_status(client.get("/admin/jobs", headers=user_headers), 403, "regular user cannot open jobs page")
            assert_status(client.get("/admin/diagnostics", headers=user_headers), 403, "regular user cannot open diagnostics page")
            assert_status(client.get("/admin/agent-contract", headers=user_headers), 403, "regular user cannot open agent contract page")
            assert_status(client.get("/api/admin/agent-contract", headers=user_headers), 403, "regular user cannot open agent contract api")
            if format_handshake(datetime(2026, 1, 1, 0, 0)) != "01.01.2026 03:00:00":
                raise SmokeFailure("datetime_moscow filter does not treat naive DB datetimes as UTC")
            assert_status(
                client.post("/admin/logs/delete-all", headers=user_headers, follow_redirects=False),
                403,
                "regular user cannot submit delete-all logs form",
            )
            assert_status(
                client.put("/api/admin/settings/logs", json={"retention_days": 30}, headers=user_headers),
                403,
                "regular user cannot edit log settings",
            )
            assert_status(
                client.post("/api/admin/logs/cleanup", json={"keep_days": 30}, headers=user_headers),
                403,
                "regular user cannot clean old logs",
            )
            assert_status(
                client.delete("/api/admin/logs", headers=user_headers),
                403,
                "regular user cannot delete all logs",
            )
            assert_status(
                client.post("/api/admin/jobs/999999/cancel", headers=user_headers),
                403,
                "regular user cannot cancel panel jobs",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/basic",
                    json={
                        "dns_server": "8.8.8.8",
                        "mtu": 1280,
                        "keepalive": 21,
                        "exclusion_filters_enabled": True,
                        "block_filters_enabled": True,
                        "admin_telegram_url": "",
                        "admin_vk_url": "",
                        "admin_email_url": "",
                        "admin_group_url": "",
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot edit basic settings",
            )
            assert_status(
                client.get("/api/admin/updates/check", headers=user_headers),
                403,
                "regular user cannot check panel updates",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/updates",
                    json={"nelomai_git_repo": ""},
                    headers=user_headers,
                ),
                403,
                "regular user cannot edit update settings",
            )
            assert_status(
                client.get("/api/admin/agent-updates/check", headers=user_headers),
                403,
                "regular user cannot check agent updates",
            )
            assert_status(
                client.post("/api/admin/agent-updates/apply", json={"server_id": None}, headers=user_headers),
                403,
                "regular user cannot apply agent updates",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/backups",
                    json={
                        "backups_enabled": True,
                        "backup_frequency": "daily",
                        "backup_time": "03:00",
                        "backup_retention_days": 30,
                        "backup_storage_path": ".tmp/backups",
                        "server_backup_retention_days": 90,
                        "server_backup_size_limit_mb": 5120,
                        "server_backup_monthly_retention_days": 365,
                        "server_backup_monthly_size_limit_mb": 3072,
                        "backup_remote_storage_server_id": None,
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot edit backup settings",
            )
            assert_status(
                client.post("/api/admin/backups", json={"backup_type": "system"}, headers=user_headers),
                403,
                "regular user cannot create backups",
            )
            assert_status(
                client.post(
                    "/api/admin/servers",
                    json={
                        "server_type": "tic",
                        "name": f"{SMOKE_FILTER_PREFIX}server",
                        "host": "127.0.0.1",
                        "ssh_port": 22,
                        "ssh_login": "root",
                        "ssh_password": "secret",
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot create server",
            )
            assert_status(
                client.get("/api/admin/server-bootstrap/999999", headers=user_headers),
                403,
                "regular user cannot read bootstrap task",
            )
            assert_status(
                client.post(
                    "/api/admin/server-bootstrap/999999/input",
                    json={"value": "yes"},
                    headers=user_headers,
                ),
                403,
                "regular user cannot submit bootstrap input",
            )
            for path in (
                "/api/admin/servers/999999/restart-agent",
                "/api/admin/servers/999999/refresh",
                "/api/admin/servers/999999/reboot",
                "/api/admin/servers/999999/exclude",
                "/api/admin/servers/999999/restore",
            ):
                assert_status(
                    client.post(path, headers=user_headers),
                    403,
                    f"regular user cannot call admin server action {path}",
                )
            assert_status(
                client.delete("/api/admin/servers/999999", headers=user_headers),
                403,
                "regular user cannot delete server",
            )
            assert_status(
                client.post(
                    "/api/admin/interfaces/prepare",
                    json={"name": f"{SMOKE_FILTER_PREFIX}iface", "tic_server_id": 1, "tak_server_id": None},
                    headers=user_headers,
                ),
                403,
                "regular user cannot prepare interface",
            )
            assert_status(
                client.post(
                    "/api/admin/interfaces",
                    json={
                        "name": f"{SMOKE_FILTER_PREFIX}iface",
                        "tic_server_id": 1,
                        "tak_server_id": None,
                        "listen_port": 19999,
                        "address_v4": "10.90.0.1/24",
                        "peer_limit": 5,
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot create interface",
            )
            assert_status(
                client.post("/api/admin/filters/delete", json={"ids": [999999]}, headers=user_headers),
                403,
                "regular user cannot bulk delete filters",
            )

            assert_status(client.get("/dashboard", headers=user_headers), 200, "user dashboard")
            assert_status(
                client.get(f"/dashboard?target_user_id={user_id}&preview=1", headers=admin_headers),
                200,
                "admin preview dashboard",
            )

            assert_status(client.get("/admin?tab=overview", headers=admin_headers), 200, "admin overview")
            assert_status(
                client.get("/admin?tab=overview&interface_tic_server_id=1", headers=admin_headers),
                200,
                "admin overview tic filter",
            )
            assert_status(client.get("/admin?tab=clients", headers=admin_headers), 200, "admin clients")
            assert_status(
                client.get("/admin?tab=settings&settings_view=filters", headers=admin_headers),
                200,
                "admin exclusion settings",
            )
            assert_status(
                client.get("/admin?tab=settings&settings_view=block_filters", headers=admin_headers),
                200,
                "admin block settings",
            )
            assert_status(
                client.get("/admin?tab=settings&settings_view=logs", headers=admin_headers),
                200,
                "admin log settings",
            )
            assert_status(
                client.get("/admin?tab=settings&settings_view=updates", headers=admin_headers),
                200,
                "admin update settings",
            )
            assert_status(
                client.get("/admin?tab=settings&settings_view=backups", headers=admin_headers),
                200,
                "admin backup settings",
            )
            assert_status(client.get("/admin/servers", headers=admin_headers), 200, "admin servers")
            assert_status(
                client.get("/admin/servers?bucket=active&server_type=storage&sort=load_asc", headers=admin_headers),
                200,
                "admin servers storage filter",
            )
            assert_status(
                client.get("/admin/servers?bucket=excluded&server_type=tic&sort=load_desc", headers=admin_headers),
                200,
                "admin excluded servers filter",
            )
            assert_status(client.get("/admin/logs", headers=admin_headers), 200, "admin logs")
            assert_status(client.get("/admin/jobs", headers=admin_headers), 200, "admin jobs")
            assert_status(client.get("/admin/diagnostics", headers=admin_headers), 200, "admin diagnostics")
            assert_status(client.post("/admin/diagnostics/run", headers=admin_headers), 200, "run admin diagnostics")
            assert_status(client.get("/admin/agent-contract", headers=admin_headers), 200, "admin agent contract page")
            contract_manifest_response = client.get("/api/admin/agent-contract", headers=admin_headers)
            assert_status(contract_manifest_response, 200, "admin agent contract api")
            contract_manifest = contract_manifest_response.json()
            if contract_manifest.get("contract_version") != "1.0":
                raise SmokeFailure("agent contract manifest has wrong contract_version")
            if not isinstance(contract_manifest.get("actions"), list) or not contract_manifest["actions"]:
                raise SmokeFailure("agent contract manifest does not expose actions")
            assert_status(client.post("/api/admin/jobs/999999/cancel", headers=admin_headers), 404, "admin gets 404 for missing job")
            assert_status(client.post("/api/admin/jobs/cleanup", headers=admin_headers), 200, "admin can clean inactive panel jobs")
            assert_status(client.get("/admin/logs?severity=error&sort=event_type", headers=admin_headers), 200, "admin logs filters")
            assert_status(client.get("/admin/logs?server_id=&user_id=&sort=server", headers=admin_headers), 200, "admin logs empty filters")
            assert_status(
                client.get("/admin/logs?event_type=servers.exclude&sort=server", headers=admin_headers),
                200,
                "admin logs server event filter",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/logs",
                    json={"retention_days": original_audit_retention_days},
                    headers=admin_headers,
                ),
                200,
                "admin edits log retention settings",
            )
            assert_status(
                client.post("/api/admin/logs/cleanup", json={"keep_days": 30}, headers=admin_headers),
                200,
                "admin cleans old logs",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/updates",
                    json={"nelomai_git_repo": ""},
                    headers=admin_headers,
                ),
                200,
                "admin edits update settings",
            )
            assert_status(
                client.get("/api/admin/updates/check", headers=admin_headers),
                200,
                "admin checks panel updates without configured repo",
            )
            assert_status(
                client.get("/api/admin/agent-updates/check", headers=admin_headers),
                200,
                "admin checks agent updates without configured repos",
            )
            assert_status(
                client.post("/api/admin/agent-updates/apply", json={"server_id": None}, headers=admin_headers),
                200,
                "admin applies agent updates without configured repos",
            )
            assert_status(
                client.put(
                    "/api/admin/settings/backups",
                    json={
                        "backups_enabled": True,
                        "backup_frequency": "daily",
                        "backup_time": "03:00",
                        "backup_retention_days": 30,
                        "backup_storage_path": ".tmp/backups",
                        "server_backup_retention_days": 90,
                        "server_backup_size_limit_mb": 5120,
                        "server_backup_monthly_retention_days": 365,
                        "server_backup_monthly_size_limit_mb": 3072,
                        "backup_remote_storage_server_id": None,
                    },
                    headers=admin_headers,
                ),
                200,
                "admin edits backup settings",
            )
            with SessionLocal() as db:
                admin_for_schedule = db.get(User, admin_id)
                if admin_for_schedule is None:
                    raise SmokeFailure("No admin user found for scheduled backup check")
                last_run = db.get(AppSetting, "backup_last_run_at") or AppSetting(key="backup_last_run_at", value="")
                last_run.value = (datetime(2026, 1, 1, 4, 0, tzinfo=UTC)).isoformat()
                db.add(last_run)
                db.commit()
                backup_settings = get_backup_settings(db, admin_for_schedule)
                next_run_hour = backup_settings.backup_next_run_at.astimezone(MOSCOW_TZ).hour if backup_settings.backup_next_run_at else None
                if next_run_hour == 6:
                    raise SmokeFailure("backup next run appears to be shifted by timezone")
                if next_run_hour != 3:
                    raise SmokeFailure("backup next run does not match configured 03:00 local time")
                scheduled_backup = run_scheduled_backup_if_due(db, datetime(2026, 1, 10, 4, 0, tzinfo=UTC))
                if scheduled_backup is None:
                    raise SmokeFailure("scheduled backup was due but did not run")
                scheduled_backup_id = scheduled_backup.id
                scheduled_duplicate = run_scheduled_backup_if_due(db, datetime(2026, 1, 10, 4, 5, tzinfo=UTC))
                if scheduled_duplicate is not None:
                    raise SmokeFailure("scheduled backup created duplicate run")
                if db.get(BackupRecord, scheduled_backup_id) is None:
                    raise SmokeFailure("scheduled backup record disappeared")
                delete_backup(db, admin_for_schedule, scheduled_backup_id)
            scheduled_api_response = client.post("/api/admin/backups/scheduled/run-now", headers=admin_headers)
            assert_status(scheduled_api_response, 201, "admin runs scheduled backup manually")
            scheduled_api_backup_id = scheduled_api_response.json()["id"]
            full_restore_plan_response = client.get(f"/api/admin/backups/{scheduled_api_backup_id}/restore-plan", headers=admin_headers)
            assert_status(full_restore_plan_response, 200, "admin previews full backup restore plan")
            full_restore_plan = full_restore_plan_response.json()
            if full_restore_plan.get("restore_scope") != "user_data_only":
                raise SmokeFailure("full backup restore scope should be limited to user data")
            if full_restore_plan.get("can_restore_system") or full_restore_plan.get("can_restore_server_snapshots"):
                raise SmokeFailure("full backup should not offer system/server restore apply")
            if not full_restore_plan.get("summary", {}).get("has_user_payload"):
                raise SmokeFailure("full backup restore preview does not detect user payload")
            assert_status(
                client.post("/api/admin/backups/latest-full/verify-server-copies", headers=admin_headers),
                200,
                "admin verifies latest full backup server copies",
            )
            assert_status(
                client.delete(f"/api/admin/backups/{scheduled_api_backup_id}", headers=admin_headers),
                204,
                "admin deletes manually triggered scheduled backup",
            )
            backup_response = client.post("/api/admin/backups", json={"backup_type": "system"}, headers=admin_headers)
            assert_status(backup_response, 201, "admin creates system backup")
            backup_payload = backup_response.json()
            backup_id = backup_payload["id"]
            backup_created_at = datetime.fromisoformat(backup_payload["created_at"]).astimezone(MOSCOW_TZ)
            if backup_created_at.strftime("%Y%m%d-%H%M%S") not in backup_payload["filename"]:
                raise SmokeFailure("backup filename does not use Moscow creation time")
            if backup_payload["created_label"] != backup_created_at.strftime("%d.%m.%Y %H:%M:%S"):
                raise SmokeFailure("backup created label does not use Moscow creation time")
            assert_status(
                client.get("/api/admin/backups/download-all", headers=admin_headers),
                200,
                "admin downloads all panel backups",
            )
            assert_status(
                client.get(f"/api/admin/backups/{backup_id}/download", headers=admin_headers),
                200,
                "admin downloads backup",
            )
            assert_status(
                client.get(f"/api/admin/backups/{backup_id}/restore-plan", headers=admin_headers),
                200,
                "admin builds backup restore plan",
            )
            system_restore_plan = client.get(f"/api/admin/backups/{backup_id}/restore-plan", headers=admin_headers).json()
            if system_restore_plan.get("system_summary", {}).get("settings", 0) < 1:
                raise SmokeFailure("system backup restore preview does not expose settings summary")
            if system_restore_plan.get("can_restore_users"):
                raise SmokeFailure("system backup should be preview-only for user restore")
            if system_restore_plan.get("restore_scope") != "preview_only":
                raise SmokeFailure("system backup restore scope should be preview-only")
            assert_status(
                client.post(f"/api/admin/backups/{backup_id}/restore-plan", json={"user_ids": []}, headers=admin_headers),
                200,
                "admin builds selected backup restore draft",
            )
            assert_status(
                client.post(
                    f"/api/admin/backups/{backup_id}/restore-users",
                    json={
                        "user_ids": [999999],
                        "user_login_overrides": {"999999": "restored-login"},
                        "interface_port_overrides": {"999999": 19999},
                        "interface_address_overrides": {"999999": "10.255.0.1"},
                    },
                    headers=admin_headers,
                ),
                403,
                "admin cannot restore users from system backup",
            )
            assert_status(
                client.delete(f"/api/admin/backups/{backup_id}", headers=admin_headers),
                204,
                "admin deletes backup",
            )
            cleanup_dir = ROOT_DIR / ".tmp" / "backups"
            cleanup_dir.mkdir(parents=True, exist_ok=True)
            old_system_path = cleanup_dir / "smoke-old-system.zip"
            old_full_path = cleanup_dir / "smoke-old-full.zip"
            old_system_path.write_bytes(b"old-system-backup")
            old_full_path.write_bytes(b"old-full-backup")
            with SessionLocal() as db:
                admin_for_cleanup = db.get(User, admin_id)
                if admin_for_cleanup is None:
                    raise SmokeFailure("No admin user found for backup cleanup check")
                old_created_at = datetime.now(UTC) - timedelta(days=400)
                protected_created_at = datetime.now(UTC) + timedelta(days=365)
                old_system = BackupRecord(
                    backup_type=BackupType.SYSTEM,
                    status="completed",
                    filename=old_system_path.name,
                    storage_path=str(old_system_path),
                    size_bytes=old_system_path.stat().st_size,
                    created_by_user_id=admin_id,
                    manifest_json="{}",
                    created_at=old_created_at,
                    completed_at=old_created_at,
                )
                old_full = BackupRecord(
                    backup_type=BackupType.FULL,
                    status="completed",
                    filename=old_full_path.name,
                    storage_path=str(old_full_path),
                    size_bytes=old_full_path.stat().st_size,
                    created_by_user_id=admin_id,
                    manifest_json="{}",
                    created_at=protected_created_at,
                    completed_at=protected_created_at,
                )
                db.add_all([old_system, old_full])
                db.commit()
                old_system_id = old_system.id
                old_full_id = old_full.id
            assert_status(
                client.post("/api/admin/backups/server-copies/cleanup", headers=admin_headers),
                200,
                "admin cleans up server backup copies",
            )
            cleanup_response = client.post("/api/admin/backups/delete-all-except-latest", headers=admin_headers)
            assert_status(cleanup_response, 200, "admin deletes panel backups except latest")
            cleanup_payload = cleanup_response.json()
            if cleanup_payload["deleted_count"] < 1:
                raise SmokeFailure("panel backup delete-all did not delete older backup")
            if old_system_path.exists():
                raise SmokeFailure("panel backup delete-all left old backup file on disk")
            if not old_full_path.exists():
                raise SmokeFailure("panel backup delete-all deleted latest backup")
            with SessionLocal() as db:
                admin_for_cleanup = db.get(User, admin_id)
                if db.get(BackupRecord, old_system_id) is not None:
                    raise SmokeFailure("panel backup delete-all left old backup record")
                if db.get(BackupRecord, old_full_id) is None:
                    raise SmokeFailure("panel backup delete-all removed latest backup record")
                if admin_for_cleanup is not None:
                    delete_backup(db, admin_for_cleanup, old_full_id)

            assert_status(client.get(f"/api/users/{user_id}/resources", headers=user_headers), 200, "user resources")
            assert_status(client.get(f"/api/users/{user_id}/filters?kind=block", headers=user_headers), 200, "block filters list")
            assert_status(client.get("/downloads/peer/not-a-real-token"), 404, "invalid public download token")

            if other_user_id is not None:
                assert_status(
                    client.get(f"/dashboard?target_user_id={other_user_id}", headers=user_headers),
                    403,
                    "regular user cannot open another dashboard",
                )
                assert_status(
                    client.get(f"/api/users/{other_user_id}/resources", headers=user_headers),
                    403,
                    "regular user cannot read another resources",
                )
                assert_status(
                    client.get(f"/api/users/{other_user_id}/filters?kind=block", headers=user_headers),
                    403,
                    "regular user cannot read another filters",
                )
                assert_status(
                    client.post(f"/api/admin/users/{other_user_id}/assign-interface/999999", headers=user_headers),
                    403,
                    "regular user cannot assign interfaces through admin API",
                )
                assert_status(
                    client.post(
                        f"/api/admin/users/{other_user_id}/detach-interface/999999",
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot detach interfaces through admin API",
                )
                assert_status(
                    client.put(
                        f"/api/admin/users/{other_user_id}/expires",
                        json={"expires_at": None},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit user expiry",
                )
                assert_status(
                    client.put(
                        f"/api/admin/users/{other_user_id}/channel",
                        json={"value": "https://example.com"},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit user channel",
                )
                assert_status(
                    client.put(
                        f"/api/admin/users/{other_user_id}/name",
                        json={"value": "Forbidden"},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit user display name",
                )
                assert_status(
                    client.delete(f"/api/admin/users/{other_user_id}", headers=user_headers),
                    403,
                    "regular user cannot delete users",
                )

            if foreign_interface_id is not None:
                assert_status(
                    client.post(f"/api/interfaces/{foreign_interface_id}/peers", headers=user_headers),
                    403,
                    "regular user cannot create peer in foreign interface",
                )
                assert_status(
                    client.get(f"/api/interfaces/{foreign_interface_id}/download-all", headers=user_headers),
                    403,
                    "regular user cannot download foreign interface bundle",
                )
                assert_status(
                    client.post(f"/api/admin/interfaces/{foreign_interface_id}/toggle", headers=user_headers),
                    403,
                    "regular user cannot toggle interface through admin API",
                )
                assert_status(
                    client.put(
                        f"/api/admin/interfaces/{foreign_interface_id}/exclusion-filters",
                        json={"enabled": False},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit interface exclusion filters",
                )

            if foreign_peer_id is not None:
                assert_status(
                    client.put(
                        f"/api/peers/{foreign_peer_id}/comment",
                        json={"comment": "forbidden"},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit foreign peer comment",
                )
                assert_status(
                    client.post(f"/api/peers/{foreign_peer_id}/toggle", headers=user_headers),
                    403,
                    "regular user cannot toggle foreign peer",
                )
                assert_status(
                    client.post(f"/api/peers/{foreign_peer_id}/recreate", headers=user_headers),
                    403,
                    "regular user cannot recreate foreign peer",
                )
                assert_status(
                    client.delete(f"/api/peers/{foreign_peer_id}", headers=user_headers),
                    403,
                    "regular user cannot delete foreign peer",
                )
                assert_status(
                    client.get(f"/api/peers/{foreign_peer_id}/download", headers=user_headers),
                    403,
                    "regular user cannot download foreign peer config",
                )
                assert_status(
                    client.put(
                        f"/api/admin/peers/{foreign_peer_id}/block-filters",
                        json={"enabled": False},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit peer block filters",
                )
                assert_status(
                    client.put(
                        f"/api/peers/{foreign_peer_id}/expires",
                        json={"expires_at": None},
                        headers=user_headers,
                    ),
                    403,
                    "regular user cannot edit peer expiry",
                )

            assert_status(
                client.post(
                    "/api/admin/filters",
                    json={
                        "name": f"{SMOKE_FILTER_PREFIX}forbidden-admin-filter",
                        "kind": "exclusion",
                        "filter_type": "ip",
                        "scope": "global",
                        "value": "8.8.8.8",
                        "is_active": True,
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot create admin global filter",
            )
            assert_status(
                client.post(
                    "/api/admin/users",
                    json={
                        "login": f"{SMOKE_FILTER_PREFIX}forbidden-user",
                        "password": "secret123",
                        "interface_ids": [],
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot create admin user",
            )
            assert_status(
                client.post(
                    f"/api/users/{user_id}/filters",
                    json={
                        "name": f"{SMOKE_FILTER_PREFIX}forbidden-global",
                        "kind": "exclusion",
                        "filter_type": "ip",
                        "scope": "global",
                        "value": "8.8.4.4",
                        "is_active": True,
                    },
                    headers=user_headers,
                ),
                403,
                "regular user cannot create global filter through user endpoint",
            )

            global_create_response = client.post(
                "/api/admin/filters",
                json={
                    "name": f"{SMOKE_FILTER_PREFIX}global",
                    "kind": "exclusion",
                    "filter_type": "ip",
                    "scope": "global",
                    "value": "8.8.8.8",
                    "is_active": True,
                },
                headers=admin_headers,
            )
            assert_status(global_create_response, 201, "admin creates global filter for permission checks")
            global_filter_id = global_create_response.json()["id"]
            assert_status(
                client.patch(f"/api/filters/{global_filter_id}", json={"value": "8.8.4.4"}, headers=user_headers),
                403,
                "regular user cannot edit global filter",
            )
            assert_status(
                client.delete(f"/api/filters/{global_filter_id}", headers=user_headers),
                403,
                "regular user cannot delete global filter",
            )
            assert_status(
                client.delete(f"/api/filters/{global_filter_id}", headers=admin_headers),
                204,
                "admin deletes permission-check global filter",
            )

            invalid_ip_payload = {
                "name": f"{SMOKE_FILTER_PREFIX}invalid-ip",
                "kind": "block",
                "peer_id": peer_id,
                "filter_type": "ip",
                "scope": "user",
                "value": "999.1.1.1",
                "is_active": True,
            }
            if peer_id is not None:
                assert_status(
                    client.post(f"/api/users/{user_id}/filters", json=invalid_ip_payload, headers=user_headers),
                    400,
                    "invalid IP validation",
                )

            no_peer_payload = {
                "name": f"{SMOKE_FILTER_PREFIX}no-peer",
                "kind": "block",
                "filter_type": "ip",
                "scope": "user",
                "value": "1.1.1.1",
                "is_active": True,
            }
            assert_status(
                client.post(f"/api/users/{user_id}/filters", json=no_peer_payload, headers=user_headers),
                400,
                "block filter requires peer",
            )

            if peer_id is not None:
                preview_response = client.post(
                    f"/api/users/{user_id}/filters?preview=1",
                    json={**invalid_ip_payload, "value": "1.1.1.1", "name": f"{SMOKE_FILTER_PREFIX}preview"},
                    headers=admin_headers,
                )
                assert_status(preview_response, 403, "preview mode blocks writes")
                assert_status(
                    client.put(
                        f"/api/users/{user_id}/resources?preview=1",
                        json={
                            "yandex_disk_url": "https://example.com",
                            "amnezia_vpn_finland": "secret",
                            "outline_japan": "secret",
                        },
                        headers=admin_headers,
                    ),
                    403,
                    "preview mode blocks resource writes",
                )
                assert_status(
                    client.delete(f"/api/users/{user_id}/resources?preview=1", headers=admin_headers),
                    403,
                    "preview mode blocks resource delete",
                )
                assert_status(
                    client.put(
                        f"/api/peers/{peer_id}/comment?preview=1",
                        json={"comment": "preview forbidden"},
                        headers=admin_headers,
                    ),
                    403,
                    "preview mode blocks peer comment writes",
                )
                assert_status(
                    client.post(f"/api/peers/{peer_id}/toggle?preview=1", headers=admin_headers),
                    403,
                    "preview mode blocks peer toggle",
                )
                assert_status(
                    client.post(f"/api/peers/{peer_id}/recreate?preview=1", headers=admin_headers),
                    403,
                    "preview mode blocks peer recreate",
                )
                assert_status(
                    client.delete(f"/api/peers/{peer_id}?preview=1", headers=admin_headers),
                    403,
                    "preview mode blocks peer delete",
                )
                assert_status(
                    client.put(
                        f"/api/peers/{peer_id}/expires?preview=1",
                        json={"expires_at": None},
                        headers=admin_headers,
                    ),
                    403,
                    "preview mode blocks peer expiry",
                )
                assert_status(
                    client.put(
                        f"/api/admin/peers/{peer_id}/block-filters?preview=1",
                        json={"enabled": False},
                        headers=admin_headers,
                    ),
                    403,
                    "preview mode blocks peer block filters",
                )
                assert_status(
                    client.post(f"/api/peers/{peer_id}/download-link?preview=1", headers=admin_headers),
                    403,
                    "preview mode blocks public download link generation",
                )
                previous_agent_command = settings.peer_agent_command
                settings.peer_agent_command = f'"{sys.executable}" "{ROOT_DIR / "scripts" / "fake_peer_agent.py"}"'
                try:
                    link_response = client.post(f"/api/peers/{peer_id}/download-link", headers=admin_headers)
                    assert_status(link_response, 200, "admin creates public peer download link")
                    public_url = link_response.json().get("url")
                    if not isinstance(public_url, str) or "/downloads/peer/" not in public_url:
                        raise SmokeFailure("public peer download link response has no download URL")
                    public_path = "/" + public_url.split("/", 3)[3]
                    assert_status(client.get(public_path), 200, "public peer download link works without auth")
                    with SessionLocal() as db:
                        link = db.execute(
                            select(PeerDownloadLink)
                            .where(PeerDownloadLink.peer_id == peer_id, PeerDownloadLink.revoked_at.is_(None))
                            .order_by(PeerDownloadLink.created_at.desc(), PeerDownloadLink.id.desc())
                        ).scalars().first()
                        if link is None:
                            raise SmokeFailure("public peer download link was not persisted")
                        link_id = link.id
                        if link.expires_at is not None:
                            raise SmokeFailure("peer without lifetime should create lifetime download link")
                    assert_status(
                        client.get("/admin?tab=settings&settings_view=shared_peers", headers=admin_headers),
                        200,
                        "admin shared peers settings",
                    )
                    assert_status(
                        client.delete(f"/api/admin/peer-download-links/{link_id}", headers=admin_headers),
                        204,
                        "admin revokes public peer download link",
                    )
                    assert_status(client.get(public_path), 404, "revoked public peer download link is blocked")
                    with SessionLocal() as db:
                        admin_for_links = db.get(User, admin_id)
                        fresh_page = get_shared_peer_links_page(db, admin_for_links)
                        if not any(item.id == link_id for item in fresh_page.links):
                            raise SmokeFailure("freshly revoked peer link disappeared before one hour")
                        old_link = db.get(PeerDownloadLink, link_id)
                        if old_link is None or old_link.revoked_at is None:
                            raise SmokeFailure("revoked peer link disappeared from database")
                        old_link.revoked_at = datetime.now(UTC) - timedelta(hours=2)
                        db.add(old_link)
                        db.commit()
                        hidden_page = get_shared_peer_links_page(db, admin_for_links)
                        if any(item.id == link_id for item in hidden_page.links):
                            raise SmokeFailure("peer link revoked more than one hour ago is still visible")
                    lifetime_link_response = client.post(f"/api/peers/{peer_id}/download-link", headers=admin_headers)
                    assert_status(lifetime_link_response, 200, "admin creates lifetime peer link for bulk revoke")
                    assert_status(
                        client.post("/api/admin/peer-download-links/revoke-all?lifetime_only=true", headers=admin_headers),
                        200,
                        "admin revokes lifetime peer links",
                    )
                    lifetime_public_path = "/" + lifetime_link_response.json()["url"].split("/", 3)[3]
                    assert_status(client.get(lifetime_public_path), 404, "bulk-revoked lifetime peer link is blocked")
                finally:
                    settings.peer_agent_command = previous_agent_command

                create_response = client.post(
                    f"/api/users/{user_id}/filters",
                    json={**invalid_ip_payload, "value": "1.1.1.1", "name": f"{SMOKE_FILTER_PREFIX}peer-block"},
                    headers=user_headers,
                )
                assert_status(create_response, 201, "create peer block filter")
                filter_id = create_response.json()["id"]
                if create_response.json().get("peer_id") != peer_id:
                    raise SmokeFailure("created block filter is not tied to requested peer")

                assert_status(
                    client.patch(
                        f"/api/filters/{filter_id}",
                        json={"value": "1.1.1.2"},
                        headers=user_headers,
                    ),
                    200,
                    "update peer block filter",
                )
                assert_status(
                    client.delete(f"/api/filters/{filter_id}", headers=user_headers),
                    204,
                    "delete peer block filter",
                )

        print("OK: smoke check passed")
        if peer_id is None:
            print("WARN: peer-level block filter CRUD skipped because no regular-user peer exists")
    finally:
        cleanup_smoke_filters()
        restore_block_setting(original_block_value)


if __name__ == "__main__":
    try:
        run()
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
