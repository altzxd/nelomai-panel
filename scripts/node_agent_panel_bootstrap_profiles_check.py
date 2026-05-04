from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import AppSetting, PanelJob, Server, ServerBootstrapTask, User, UserRole
from app.security import create_access_token
from app.services import ensure_default_settings, ensure_seed_data


class NodePanelBootstrapProfilesFailure(RuntimeError):
    pass


def _node_binary() -> str | None:
    explicit = os.environ.get("NELOMAI_NODE_BIN", "").strip()
    if explicit:
        return explicit
    return shutil.which("node")


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:800].replace("\n", " ")
        raise NodePanelBootstrapProfilesFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise NodePanelBootstrapProfilesFailure("No admin user found")
        db.expunge(user)
        return user


def _cleanup_records(prefix: str) -> None:
    with SessionLocal() as db:
        tasks = db.execute(select(ServerBootstrapTask).where(ServerBootstrapTask.server_name.like(f"{prefix}%"))).scalars().all()
        task_ids = [task.id for task in tasks]
        job_ids = [task.panel_job_id for task in tasks if task.panel_job_id]
        server_ids = [task.server_id for task in tasks if task.server_id]
        for task in tasks:
            db.delete(task)
        if server_ids:
            for server in db.execute(select(Server).where(Server.id.in_(server_ids))).scalars().all():
                db.delete(server)
        if job_ids:
            for job in db.execute(select(PanelJob).where(PanelJob.id.in_(job_ids))).scalars().all():
                db.delete(job)
        for server in db.execute(select(Server).where(Server.name.like(f"{prefix}%"))).scalars().all():
            db.delete(server)
        for task in db.execute(select(ServerBootstrapTask).where(ServerBootstrapTask.id.in_(task_ids))).scalars().all():
            db.delete(task)
        db.commit()


def _set_nelomai_repo(value: str) -> str | None:
    with SessionLocal() as db:
        ensure_default_settings(db)
        setting = db.get(AppSetting, "nelomai_git_repo")
        previous = setting.value if setting is not None else None
        if setting is None:
            setting = AppSetting(key="nelomai_git_repo", value=value)
        else:
            setting.value = value
        db.add(setting)
        db.commit()
        return previous


def _restore_nelomai_repo(previous: str | None) -> None:
    with SessionLocal() as db:
        ensure_default_settings(db)
        setting = db.get(AppSetting, "nelomai_git_repo")
        if setting is None:
            if previous is None:
                return
            setting = AppSetting(key="nelomai_git_repo", value=previous)
            db.add(setting)
        else:
            setting.value = previous or ""
            db.add(setting)
        db.commit()


def _assert_profile_view(task: dict[str, Any], expected_profile: str) -> None:
    if task.get("bootstrap_command_profile") != expected_profile:
        raise NodePanelBootstrapProfilesFailure(
            f"Expected bootstrap_command_profile={expected_profile!r}, got {task.get('bootstrap_command_profile')!r}"
        )
    packages = task.get("bootstrap_packages")
    safe_init_packages = task.get("bootstrap_safe_init_packages")
    full_only_packages = task.get("bootstrap_full_only_packages")
    if not isinstance(packages, list) or not packages:
        raise NodePanelBootstrapProfilesFailure("bootstrap_packages is missing")
    if not isinstance(safe_init_packages, list) or not safe_init_packages:
        raise NodePanelBootstrapProfilesFailure("bootstrap_safe_init_packages is missing")
    if "wireguard" not in safe_init_packages or "build-essential" not in safe_init_packages or "python3" not in safe_init_packages or "ufw" not in safe_init_packages:
        raise NodePanelBootstrapProfilesFailure("bootstrap_safe_init_packages is missing required runtime dependencies")
    if "nodejs" in safe_init_packages:
        raise NodePanelBootstrapProfilesFailure("nodejs must remain a command step, not a safe-init package baseline entry")
    if expected_profile == "safe-init":
        if full_only_packages != []:
            raise NodePanelBootstrapProfilesFailure(f"Unexpected safe-init full-only delta: {full_only_packages!r}")
    if expected_profile == "full":
        if full_only_packages != []:
            raise NodePanelBootstrapProfilesFailure(f"Unexpected full-profile delta: {full_only_packages!r}")
        snapshot = task.get("bootstrap_snapshot") or {}
        if int(snapshot.get("command_count") or 0) <= 0:
            raise NodePanelBootstrapProfilesFailure("bootstrap_snapshot.command_count is missing")


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; panel bootstrap profiles check was not run")
        return

    admin = _load_admin()
    prefix = f"node-profiles-{uuid.uuid4().hex[:8]}"
    temp_root = Path(tempfile.gettempdir()) / "node-panel-e2e"
    temp_root.mkdir(parents=True, exist_ok=True)

    previous_command = settings.peer_agent_command
    previous_component = os.environ.get("NELOMAI_AGENT_COMPONENT")
    previous_state_file = os.environ.get("NELOMAI_AGENT_STATE_FILE")
    previous_log_file = os.environ.get("NELOMAI_AGENT_LOG")
    previous_stub_mode = os.environ.get("NELOMAI_AGENT_STUB_MODE")
    previous_bootstrap_mode = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_MODE")
    previous_bootstrap_transport = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_TRANSPORT")
    previous_bootstrap_profile = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE")
    previous_bootstrap_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_latest_version = os.environ.get("NELOMAI_AGENT_LATEST_VERSION")
    previous_repo_url = _set_nelomai_repo("https://github.com/example/nelomai.git")

    _cleanup_records(prefix)
    try:
        settings.peer_agent_command = f'"{node_bin}" ".\\agents\\node-tic-agent\\src\\index.js"'
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"
        os.environ["NELOMAI_AGENT_STUB_MODE"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = "dry-run"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_TRANSPORT"] = "noop"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = "1"
        os.environ["NELOMAI_AGENT_LATEST_VERSION"] = "0.1.1"

        with TestClient(app) as client:
            headers = _auth_headers(admin)
            for profile in ("safe-init", "full"):
                state_file = temp_root / f"{prefix}-{profile}-state.json"
                log_file = temp_root / f"{prefix}-{profile}-log.jsonl"
                if state_file.exists():
                    state_file.unlink()
                if log_file.exists():
                    log_file.unlink()
                os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
                os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
                os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = profile
                server_name = f"{prefix}-{profile}"

                create_response = client.post(
                    "/api/admin/servers",
                    json={
                        "server_type": "tic",
                        "tic_region": "europe",
                        "name": server_name,
                        "host": "127.0.0.62",
                        "ssh_port": 22,
                        "ssh_login": "root",
                        "ssh_password": "secret",
                    },
                    headers=headers,
                )
                _assert_status(create_response, 201, f"{profile} bootstrap create")
                created_task = create_response.json()
                _assert_profile_view(created_task, profile)
                task_id = int(created_task["id"])

                status_response = client.get(f"/api/admin/server-bootstrap/{task_id}", headers=headers)
                _assert_status(status_response, 200, f"{profile} bootstrap status")
                status_task = status_response.json()
                _assert_profile_view(status_task, profile)

        print("OK: panel bootstrap profiles check passed")
    finally:
        settings.peer_agent_command = previous_command
        if previous_component is None:
            os.environ.pop("NELOMAI_AGENT_COMPONENT", None)
        else:
            os.environ["NELOMAI_AGENT_COMPONENT"] = previous_component
        if previous_state_file is None:
            os.environ.pop("NELOMAI_AGENT_STATE_FILE", None)
        else:
            os.environ["NELOMAI_AGENT_STATE_FILE"] = previous_state_file
        if previous_log_file is None:
            os.environ.pop("NELOMAI_AGENT_LOG", None)
        else:
            os.environ["NELOMAI_AGENT_LOG"] = previous_log_file
        if previous_stub_mode is None:
            os.environ.pop("NELOMAI_AGENT_STUB_MODE", None)
        else:
            os.environ["NELOMAI_AGENT_STUB_MODE"] = previous_stub_mode
        if previous_bootstrap_mode is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_MODE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = previous_bootstrap_mode
        if previous_bootstrap_transport is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_TRANSPORT", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_TRANSPORT"] = previous_bootstrap_transport
        if previous_bootstrap_profile is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = previous_bootstrap_profile
        if previous_bootstrap_input_required is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = previous_bootstrap_input_required
        if previous_latest_version is None:
            os.environ.pop("NELOMAI_AGENT_LATEST_VERSION", None)
        else:
            os.environ["NELOMAI_AGENT_LATEST_VERSION"] = previous_latest_version
        _restore_nelomai_repo(previous_repo_url)
        _cleanup_records(prefix)


if __name__ == "__main__":
    try:
        main()
    except NodePanelBootstrapProfilesFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
