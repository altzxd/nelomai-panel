from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import joinedload

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal, engine
from app.config import settings
from app.models import (
    AuditLog,
    FilterKind,
    FilterScope,
    FilterType,
    Interface,
    PanelJob,
    PanelJobStatus,
    Peer,
    ResourceFilter,
    RouteMode,
    Server,
    ServerBootstrapTask,
    ServerType,
    User,
    UserContactLink,
    UserResource,
    UserRole,
)
from app.security import get_password_hash
from app.runtime_schema import apply_legacy_runtime_schema_updates
from app.services import (
    PermissionDeniedError,
    assign_interface_to_user,
    cancel_panel_job,
    create_panel_job,
    delete_user_account,
    delete_server_record,
    ensure_default_settings,
    ensure_seed_data,
    exclude_server_record,
    get_servers_page_data,
    has_problem_panel_jobs,
    mark_stuck_panel_jobs,
    purge_expired_peers,
    restore_server_record,
    update_interface_peer_limit,
)
from app.schemas import InterfacePeerLimitUpdate


class DataFlowFailure(RuntimeError):
    pass


PREFIX = f"flow-check-{uuid4().hex[:10]}"


def cleanup(prefix: str) -> None:
    with SessionLocal() as db:
        users = db.execute(select(User).where(User.login.like(f"{prefix}%"))).scalars().all()
        interfaces = db.execute(select(Interface).where(Interface.name.like(f"{prefix}%"))).scalars().all()
        servers = db.execute(select(Server).where(Server.name.like(f"{prefix}%"))).scalars().all()
        bootstrap_tasks = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.server_name.like(f"{prefix}%"))
        ).scalars().all()
        jobs = db.execute(select(PanelJob).where(PanelJob.job_type.like(f"{prefix}%"))).scalars().all()
        linked_job_ids = {task.panel_job_id for task in bootstrap_tasks if task.panel_job_id is not None}
        if linked_job_ids:
            jobs.extend(db.execute(select(PanelJob).where(PanelJob.id.in_(linked_job_ids))).scalars().all())
        for user in users:
            db.delete(user)
        for interface in interfaces:
            db.delete(interface)
        db.flush()
        for task in bootstrap_tasks:
            db.delete(task)
        for server in servers:
            db.delete(server)
        for job in set(jobs):
            db.delete(job)
        db.commit()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise DataFlowFailure(message)


def create_admin_and_tic(db, label: str):
    ensure_seed_data(db)
    ensure_default_settings(db)
    admin = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
    if admin is None:
        raise DataFlowFailure("No admin user found")

    tic = Server(
        name=f"{PREFIX}-{label}-tic",
        server_type=ServerType.TIC,
        host="127.0.0.1",
        ssh_port=22,
        ssh_login="root",
        ssh_password="secret",
        is_excluded=False,
        is_active=True,
    )
    excluded_tic = Server(
        name=f"{PREFIX}-{label}-excluded-tic",
        server_type=ServerType.TIC,
        host="127.0.0.2",
        ssh_port=22,
        ssh_login="root",
        ssh_password="secret",
        is_excluded=True,
        is_active=False,
    )
    db.add_all([tic, excluded_tic])
    db.flush()
    return admin, tic, excluded_tic


def check_peer_limit_removes_highest_slots() -> None:
    with SessionLocal() as db:
        admin, tic, _ = create_admin_and_tic(db, "limit")
        interface = Interface(
            name=f"{PREFIX}-limit",
            user_id=admin.id,
            tic_server_id=tic.id,
            tak_server_id=None,
            route_mode=RouteMode.STANDALONE,
            listen_port=19001,
            address_v4="10.250.1.1/24",
            peer_limit=10,
            is_pending_owner=True,
        )
        db.add(interface)
        db.flush()
        for slot in [1, 4, 5, 6, 7, 8, 9, 10]:
            db.add(Peer(interface_id=interface.id, slot=slot, is_enabled=False))
        db.commit()
        interface_id = interface.id
        admin_id = admin.id

    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        update_interface_peer_limit(db, admin, interface_id, InterfacePeerLimitUpdate(peer_limit=5))

    with SessionLocal() as db:
        slots = db.execute(select(Peer.slot).where(Peer.interface_id == interface_id).order_by(Peer.slot.asc())).scalars().all()
        interface = db.get(Interface, interface_id)
        assert_true(interface.peer_limit == 5, "peer_limit was not updated to 5")
        assert_true(slots == [1, 4, 5, 6, 7], f"peer limit reduction kept wrong slots: {slots}")


def check_excluded_pending_interface_cannot_be_assigned() -> None:
    with SessionLocal() as db:
        admin, _, excluded_tic = create_admin_and_tic(db, "assign")
        user = User(
            login=f"{PREFIX}-assign-user",
            password_hash=get_password_hash("secret"),
            display_name="-",
            role=UserRole.USER,
        )
        db.add(user)
        db.flush()
        db.add(UserResource(user_id=user.id))
        db.add(UserContactLink(user_id=user.id))
        interface = Interface(
            name=f"{PREFIX}-excluded-interface",
            user_id=admin.id,
            tic_server_id=excluded_tic.id,
            tak_server_id=None,
            route_mode=RouteMode.STANDALONE,
            listen_port=19002,
            address_v4="10.250.2.1/24",
            peer_limit=5,
            is_pending_owner=True,
        )
        db.add(interface)
        db.flush()
        db.add(Peer(interface_id=interface.id, slot=1, is_enabled=False))
        db.commit()
        admin_id = admin.id
        user_id = user.id
        interface_id = interface.id

    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        try:
            assign_interface_to_user(db, admin, interface_id, user_id)
        except PermissionDeniedError:
            return
        raise DataFlowFailure("excluded pending interface was assigned to user")


def check_user_delete_cascades_owned_data() -> None:
    with SessionLocal() as db:
        admin, tic, _ = create_admin_and_tic(db, "delete")
        user = User(
            login=f"{PREFIX}-delete-user",
            password_hash=get_password_hash("secret"),
            display_name="-",
            role=UserRole.USER,
        )
        db.add(user)
        db.flush()
        db.add(UserResource(user_id=user.id))
        db.add(UserContactLink(user_id=user.id, value="https://example.com"))
        interface = Interface(
            name=f"{PREFIX}-delete-interface",
            user_id=user.id,
            tic_server_id=tic.id,
            tak_server_id=None,
            route_mode=RouteMode.STANDALONE,
            listen_port=19003,
            address_v4="10.250.3.1/24",
            peer_limit=5,
            is_pending_owner=False,
        )
        db.add(interface)
        db.flush()
        peer = Peer(interface_id=interface.id, slot=1, is_enabled=True)
        db.add(peer)
        db.flush()
        db.add(
            ResourceFilter(
                user_id=user.id,
                peer_id=peer.id,
                name=f"{PREFIX}-block",
                kind=FilterKind.BLOCK,
                filter_type=FilterType.IP,
                scope=FilterScope.USER,
                value="1.1.1.1",
                is_active=True,
            )
        )
        db.commit()
        admin_id = admin.id
        user_id = user.id
        interface_id = interface.id
        peer_id = peer.id

    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        delete_user_account(db, admin, user_id)

    with SessionLocal() as db:
        assert_true(db.get(User, user_id) is None, "deleted user still exists")
        assert_true(db.get(Interface, interface_id) is None, "deleted user's interface still exists")
        assert_true(db.get(Peer, peer_id) is None, "deleted user's peer still exists")
        leftover_filters = db.execute(select(ResourceFilter).where(ResourceFilter.name.like(f"{PREFIX}%"))).scalars().all()
        assert_true(not leftover_filters, f"deleted user left filters behind: {[item.id for item in leftover_filters]}")


def check_storage_server_stays_out_of_interface_flow() -> None:
    with SessionLocal() as db:
        admin, tic, _ = create_admin_and_tic(db, "storage")
        storage = Server(
            name=f"{PREFIX}-storage",
            server_type=ServerType.STORAGE,
            host="127.0.0.50",
            ssh_port=22,
            ssh_login="root",
            ssh_password="secret",
            is_excluded=False,
            is_active=True,
        )
        db.add(storage)
        db.flush()
        interface = Interface(
            name=f"{PREFIX}-storage-interface",
            user_id=admin.id,
            tic_server_id=tic.id,
            tak_server_id=None,
            route_mode=RouteMode.STANDALONE,
            listen_port=19004,
            address_v4="10.250.4.1/24",
            peer_limit=5,
            is_pending_owner=True,
        )
        db.add(interface)
        db.commit()
        admin_id = admin.id
        storage_id = storage.id
        interface_id = interface.id

    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        storage_page = get_servers_page_data(db, admin, server_type="storage")
        assert_true([item.id for item in storage_page.servers] == [storage_id], "storage filter includes non-storage servers")
        storage_item = storage_page.servers[0]
        assert_true(storage_item.traffic_mbps is None, "storage server unexpectedly exposes traffic metrics")
        assert_true(storage_item.interface_count == 0, "storage server is counted as owning interfaces")
        assert_true(storage_item.endpoint_count == 0, "storage server is counted as an interface endpoint")
        assert_true(db.get(Interface, interface_id).tic_server_id != storage_id, "interface was attached to storage server")


def check_server_exclude_restore_delete_flow() -> None:
    with SessionLocal() as db:
        admin, tic, _ = create_admin_and_tic(db, "server-life")
        interface = Interface(
            name=f"{PREFIX}-server-life-interface",
            user_id=admin.id,
            tic_server_id=tic.id,
            tak_server_id=None,
            route_mode=RouteMode.STANDALONE,
            listen_port=19005,
            address_v4="10.250.5.1/24",
            peer_limit=5,
            is_pending_owner=False,
        )
        db.add(interface)
        db.flush()
        peer = Peer(interface_id=interface.id, slot=1, is_enabled=True)
        db.add(peer)
        db.commit()
        admin_id = admin.id
        server_id = tic.id
        peer_id = peer.id

    with SessionLocal() as db:
        admin = db.get(User, admin_id)
        try:
            delete_server_record(db, admin, server_id)
        except PermissionDeniedError:
            pass
        else:
            raise DataFlowFailure("active server was deleted without being excluded")
        exclude_server_record(db, admin, server_id)

    with SessionLocal() as db:
        server = db.get(Server, server_id)
        peer = db.get(Peer, peer_id)
        assert_true(server is not None and server.is_excluded, "server was not marked as excluded")
        assert_true(peer is not None and not peer.is_enabled, "excluding server did not disable its peers")
        admin = db.get(User, admin_id)
        restore_server_record(db, admin, server_id)

    with SessionLocal() as db:
        server = db.get(Server, server_id)
        assert_true(server is not None and not server.is_excluded, "server restore did not clear excluded flag")
        server_logs = db.execute(
            select(AuditLog.event_type)
            .where(AuditLog.server_id == server_id)
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        ).scalars().all()
        assert_true("servers.exclude" in server_logs, "server exclude action did not write audit log")
        assert_true("servers.restore" in server_logs, "server restore action did not write audit log")
        admin = db.get(User, admin_id)
        exclude_server_record(db, admin, server_id)
        delete_server_record(db, admin, server_id)

    with SessionLocal() as db:
        assert_true(db.get(Server, server_id) is None, "excluded server was not deleted")
        delete_log = db.execute(
            select(AuditLog)
            .where(AuditLog.event_type == "servers.delete", AuditLog.details.like(f"%deleted_server_id={server_id}%"))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars().first()
        assert_true(delete_log is not None, "server delete action did not write audit log")


def check_expired_peer_cleanup_uses_agent() -> None:
    previous_agent_command = settings.peer_agent_command
    fake_agent = ROOT_DIR / "scripts" / "fake_peer_agent.py"
    settings.peer_agent_command = f'"{sys.executable}" "{fake_agent}"'
    try:
        with SessionLocal() as db:
            admin, tic, _ = create_admin_and_tic(db, "expired-agent")
            interface = Interface(
                name=f"{PREFIX}-expired-agent-interface",
                user_id=admin.id,
                tic_server_id=tic.id,
                tak_server_id=None,
                route_mode=RouteMode.STANDALONE,
                listen_port=19006,
                address_v4="10.250.6.1/24",
                peer_limit=5,
                is_pending_owner=False,
            )
            db.add(interface)
            db.flush()
            peer = Peer(
                interface_id=interface.id,
                slot=1,
                is_enabled=True,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            db.add(peer)
            db.commit()
            peer_id = peer.id

        with SessionLocal() as db:
            deleted_count = purge_expired_peers(db, peer_ids={peer_id})
            assert_true(deleted_count >= 1, "expired peer was not deleted after agent success")

        with SessionLocal() as db:
            assert_true(db.get(Peer, peer_id) is None, "expired peer still exists after agent-backed cleanup")
            log = db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "peers.expire_delete")
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            ).scalars().first()
            assert_true(log is not None, "expired peer cleanup did not write success audit log")
            agent_log = db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "agent.command")
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            ).scalars().first()
            assert_true(agent_log is not None, "successful agent command did not write agent.command audit log")
    finally:
        settings.peer_agent_command = previous_agent_command


def check_expired_peer_cleanup_keeps_peer_on_agent_failure() -> None:
    previous_agent_command = settings.peer_agent_command
    settings.peer_agent_command = None
    try:
        with SessionLocal() as db:
            admin, tic, _ = create_admin_and_tic(db, "expired-fail")
            interface = Interface(
                name=f"{PREFIX}-expired-fail-interface",
                user_id=admin.id,
                tic_server_id=tic.id,
                tak_server_id=None,
                route_mode=RouteMode.STANDALONE,
                listen_port=19007,
                address_v4="10.250.7.1/24",
                peer_limit=5,
                is_pending_owner=False,
            )
            db.add(interface)
            db.flush()
            peer = Peer(
                interface_id=interface.id,
                slot=1,
                is_enabled=True,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            db.add(peer)
            db.commit()
            peer_id = peer.id

        with SessionLocal() as db:
            deleted_count = purge_expired_peers(db, peer_ids={peer_id})
            assert_true(deleted_count == 0, "expired peer was deleted even though agent was unavailable")

        with SessionLocal() as db:
            assert_true(db.get(Peer, peer_id) is not None, "expired peer disappeared after failed agent cleanup")
            log = db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "peers.expire_delete_failed")
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            ).scalars().first()
            assert_true(log is not None, "expired peer cleanup failure did not write audit log")
            agent_error_log = db.execute(
                select(AuditLog)
                .where(AuditLog.event_type == "agent.command_failed")
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            ).scalars().first()
            assert_true(agent_error_log is not None, "failed agent command did not write agent.command_failed audit log")
    finally:
        settings.peer_agent_command = previous_agent_command


def check_panel_jobs_lifecycle() -> None:
    with SessionLocal() as db:
        admin, _, _ = create_admin_and_tic(db, "panel-jobs")
        queued_job = create_panel_job(db, admin, f"{PREFIX}-queued-job")
        queued_job_id = queued_job.id
        cancelled = cancel_panel_job(db, admin, queued_job_id)
        assert_true(cancelled.status == PanelJobStatus.CANCELLED, "queued panel job was not cancelled")

        bootstrap_job = create_panel_job(db, admin, "server_bootstrap")
        bootstrap_job.status = PanelJobStatus.RUNNING
        bootstrap_job.started_at = datetime.now(UTC)
        bootstrap_task = ServerBootstrapTask(
            panel_job_id=bootstrap_job.id,
            server_name=f"{PREFIX}bootstrap-cancel",
            server_type=ServerType.TIC,
            host="127.0.0.1",
            ssh_port=22,
            ssh_login="root",
            ssh_password="secret",
            status="input_required",
            logs_json="[]",
            input_prompt="confirm",
            input_key="install_confirm",
            input_kind="confirm",
        )
        db.add_all([bootstrap_job, bootstrap_task])
        db.commit()
        cancel_panel_job(db, admin, bootstrap_job.id)
        db.refresh(bootstrap_task)
        assert_true(bootstrap_task.status == "cancelled", "cancelled server bootstrap job did not cancel bootstrap task")
        assert_true(bootstrap_task.input_prompt is None, "cancelled server bootstrap task still asks for input")

        stuck_job = create_panel_job(db, admin, f"{PREFIX}-stuck-job")
        stuck_job.status = PanelJobStatus.RUNNING
        stuck_job.started_at = datetime.now(UTC) - timedelta(minutes=20)
        stuck_job.updated_at = datetime.now(UTC) - timedelta(minutes=20)
        db.add(stuck_job)
        db.commit()
        marked = mark_stuck_panel_jobs(db)
        db.refresh(stuck_job)
        assert_true(marked >= 1, "stuck panel job was not detected")
        assert_true(stuck_job.status == PanelJobStatus.STUCK, "running panel job was not marked stuck")
        assert_true(has_problem_panel_jobs(db), "problem panel jobs indicator is false")


def run() -> None:
    apply_legacy_runtime_schema_updates(engine)
    cleanup("flow-check-")
    try:
        check_peer_limit_removes_highest_slots()
        check_excluded_pending_interface_cannot_be_assigned()
        check_user_delete_cascades_owned_data()
        check_storage_server_stays_out_of_interface_flow()
        check_server_exclude_restore_delete_flow()
        check_expired_peer_cleanup_uses_agent()
        check_expired_peer_cleanup_keeps_peer_on_agent_failure()
        check_panel_jobs_lifecycle()
    finally:
        cleanup("flow-check-")
    print("OK: data flow check passed")


if __name__ == "__main__":
    try:
        run()
    except DataFlowFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        cleanup("flow-check-")
        raise SystemExit(1) from exc
