from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
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


class LiveSafeInitFailure(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LiveSafeInitFailure(f"Missing required env {name}")
    return value


def _node_binary() -> str:
    explicit = os.environ.get("NELOMAI_NODE_BIN", "").strip()
    if explicit:
        return explicit
    found = shutil.which("node")
    if not found:
        raise LiveSafeInitFailure("node is not installed")
    return found


def _auth_headers(user: User) -> dict[str, str]:
    return {"Cookie": f"access_token={create_access_token(user.login)}"}


def _assert_status(response: Any, expected: int, label: str) -> None:
    if response.status_code != expected:
        detail = response.text[:1000].replace("\n", " ")
        raise LiveSafeInitFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise LiveSafeInitFailure("No admin user found")
        db.expunge(user)
        return user


def _cleanup_records(prefix: str) -> None:
    with SessionLocal() as db:
        tasks = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.server_name.like(f"{prefix}%"))
        ).scalars().all()
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


def _print_task_snapshot(task: dict[str, Any]) -> None:
    snapshot = task.get("bootstrap_snapshot") or {}
    print(
        json.dumps(
            {
                "status": task.get("status"),
                "current_stage": task.get("current_stage"),
                "input_key": task.get("input_key"),
                "last_error": task.get("last_error"),
                "snapshot": snapshot,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    node_bin = _node_binary()
    host = _required_env("NELOMAI_LIVE_HOST")
    ssh_login = _required_env("NELOMAI_LIVE_SSH_LOGIN")
    ssh_password = _required_env("NELOMAI_LIVE_SSH_PASSWORD")
    git_repo = _required_env("NELOMAI_LIVE_GIT_REPO")
    host_key = _required_env("NELOMAI_LIVE_SSH_HOST_KEY")
    ssh_port = int(os.environ.get("NELOMAI_LIVE_SSH_PORT", "22"))
    timeout_seconds = int(os.environ.get("NELOMAI_LIVE_POLL_TIMEOUT", "1800"))
    poll_interval = int(os.environ.get("NELOMAI_LIVE_POLL_INTERVAL", "5"))

    admin = _load_admin()
    prefix = f"live-safe-init-{uuid.uuid4().hex[:8]}"
    server_name = f"{prefix}-server"

    temp_root = Path(tempfile.gettempdir()) / "node-panel-live"
    temp_root.mkdir(parents=True, exist_ok=True)
    state_file = temp_root / f"{prefix}-state.json"
    log_file = temp_root / f"{prefix}-payloads.jsonl"
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()

    previous_command = settings.peer_agent_command
    previous_component = os.environ.get("NELOMAI_AGENT_COMPONENT")
    previous_state_file = os.environ.get("NELOMAI_AGENT_STATE_FILE")
    previous_log_file = os.environ.get("NELOMAI_AGENT_LOG")
    previous_stub_mode = os.environ.get("NELOMAI_AGENT_STUB_MODE")
    previous_bootstrap_mode = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_MODE")
    previous_bootstrap_transport = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_TRANSPORT")
    previous_bootstrap_auth_mode = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE")
    previous_bootstrap_command_profile = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE")
    previous_bootstrap_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_bootstrap_require_confirm = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM")
    previous_host_key_confirm = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM")
    previous_allow_ssh = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH")
    previous_connect_timeout = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT")
    previous_strict_host_key = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING")
    previous_pinned_host_key = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY")
    previous_latest_version = os.environ.get("NELOMAI_AGENT_LATEST_VERSION")
    previous_repo_url = _set_nelomai_repo(git_repo)

    _cleanup_records(prefix)
    try:
        settings.peer_agent_command = f'"{node_bin}" ".\\agents\\node-tic-agent\\src\\index.js"'
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"
        os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
        os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
        os.environ["NELOMAI_AGENT_STUB_MODE"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = "apply"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_TRANSPORT"] = "ssh"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE"] = "password"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = "safe-init"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH"] = "1"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT"] = "15"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING"] = "no"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY"] = host_key
        os.environ["NELOMAI_AGENT_LATEST_VERSION"] = "0.1.1"

        with TestClient(app) as client:
            headers = _auth_headers(admin)
            response = client.post(
                "/api/admin/servers",
                json={
                    "server_type": "tic",
                    "name": server_name,
                    "host": host,
                    "ssh_port": ssh_port,
                    "ssh_login": ssh_login,
                    "ssh_password": ssh_password,
                },
                headers=headers,
            )
            _assert_status(response, 201, "live safe-init create")
            task = response.json()
            _print_task_snapshot(task)
            task_id = int(task["id"])

            if task.get("status") == "input_required" and task.get("input_key") == "ssh_password":
                response = client.post(
                    f"/api/admin/server-bootstrap/{task_id}/input",
                    json={"value": ssh_password},
                    headers=headers,
                )
                _assert_status(response, 200, "live ssh password submit")
                task = response.json()
                _print_task_snapshot(task)

            deadline = time.time() + timeout_seconds
            while task.get("status") in {"running", "bootstrapping", "input_required"}:
                if task.get("status") == "input_required":
                    raise LiveSafeInitFailure(
                        f"Unexpected additional input required: key={task.get('input_key')} kind={task.get('input_kind')}"
                    )
                if time.time() >= deadline:
                    raise LiveSafeInitFailure("Timed out while waiting for live safe-init to finish")
                time.sleep(poll_interval)
                response = client.get(f"/api/admin/server-bootstrap/{task_id}", headers=headers)
                _assert_status(response, 200, "live safe-init poll")
                task = response.json()
                _print_task_snapshot(task)

            if task.get("status") != "completed":
                raise LiveSafeInitFailure(
                    f"Live safe-init did not complete: status={task.get('status')} error={task.get('last_error')}"
                )

        print("OK: live safe-init completed")
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
        if previous_bootstrap_auth_mode is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_AUTH_MODE"] = previous_bootstrap_auth_mode
        if previous_bootstrap_command_profile is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = previous_bootstrap_command_profile
        if previous_bootstrap_input_required is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = previous_bootstrap_input_required
        if previous_bootstrap_require_confirm is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM"] = previous_bootstrap_require_confirm
        if previous_host_key_confirm is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM"] = previous_host_key_confirm
        if previous_allow_ssh is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH"] = previous_allow_ssh
        if previous_connect_timeout is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT"] = previous_connect_timeout
        if previous_strict_host_key is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_STRICT_HOST_KEY_CHECKING"] = previous_strict_host_key
        if previous_pinned_host_key is None:
            os.environ.pop("NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY", None)
        else:
            os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_HOST_KEY"] = previous_pinned_host_key
        if previous_latest_version is None:
            os.environ.pop("NELOMAI_AGENT_LATEST_VERSION", None)
        else:
            os.environ["NELOMAI_AGENT_LATEST_VERSION"] = previous_latest_version
        _restore_nelomai_repo(previous_repo_url)
        _cleanup_records(prefix)


if __name__ == "__main__":
    try:
        main()
    except LiveSafeInitFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
