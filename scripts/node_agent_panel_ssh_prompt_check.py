from __future__ import annotations

import json
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


class NodePanelSshPromptFailure(RuntimeError):
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
        raise NodePanelSshPromptFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise NodePanelSshPromptFailure("No admin user found")
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
            servers = db.execute(select(Server).where(Server.id.in_(server_ids))).scalars().all()
            for server in servers:
                db.delete(server)
        if job_ids:
            jobs = db.execute(select(PanelJob).where(PanelJob.id.in_(job_ids))).scalars().all()
            for job in jobs:
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


def _load_payloads(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _find_payloads(payloads: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [payload for payload in payloads if payload.get("action") == action]


def _expect_input(task: dict[str, Any], *, key: str, kind: str, label: str) -> None:
    if task.get("status") != "input_required":
        raise NodePanelSshPromptFailure(f"{label}: expected input_required, got {task.get('status')!r}")
    if task.get("input_key") != key or task.get("input_kind") != kind:
        raise NodePanelSshPromptFailure(
            f"{label}: expected input ({key}, {kind}), got ({task.get('input_key')}, {task.get('input_kind')})"
        )


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; panel-to-node ssh prompt check was not run")
        return

    agent_entry = ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js"
    if not agent_entry.exists():
        raise NodePanelSshPromptFailure(f"Missing node agent entry: {agent_entry}")

    admin = _load_admin()
    prefix = f"node-ssh-{uuid.uuid4().hex[:8]}"
    server_name = f"{prefix}-server"

    temp_root = Path(tempfile.gettempdir()) / "node-panel-e2e"
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
    previous_bootstrap_command_profile = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE")
    previous_bootstrap_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_bootstrap_require_confirm = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM")
    previous_host_key_confirm = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM")
    previous_allow_ssh = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH")
    previous_connect_timeout = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT")
    previous_latest_version = os.environ.get("NELOMAI_AGENT_LATEST_VERSION")
    previous_repo_url = _set_nelomai_repo("https://github.com/example/nelomai.git")

    _cleanup_records(prefix)
    try:
        settings.peer_agent_command = f'"{node_bin}" ".\\agents\\node-tic-agent\\src\\index.js"'
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"
        os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
        os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
        os.environ["NELOMAI_AGENT_STUB_MODE"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = "apply"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_TRANSPORT"] = "ssh"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_COMMAND_PROFILE"] = "safe-init"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_REQUIRE_COMMAND_CONFIRM"] = "1"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_REQUIRE_HOST_KEY_CONFIRM"] = "1"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_SSH_CONNECT_TIMEOUT"] = "1"
        os.environ["NELOMAI_AGENT_LATEST_VERSION"] = "0.1.1"

        with TestClient(app) as client:
            headers = _auth_headers(admin)
            create_response = client.post(
                "/api/admin/servers",
                json={
                    "server_type": "tic",
                    "name": server_name,
                    "host": "127.0.0.62",
                    "ssh_port": 22,
                    "ssh_login": "root",
                    "ssh_password": "secret",
                },
                headers=headers,
            )
            _assert_status(create_response, 201, "ssh bootstrap create")
            task = create_response.json()
            _expect_input(task, key="ssh_host_key_confirm", kind="confirm", label="ssh bootstrap create")
            task_id = int(task["id"])

            response = client.post(
                f"/api/admin/server-bootstrap/{task_id}/input",
                json={"value": "yes"},
                headers=headers,
            )
            _assert_status(response, 200, "ssh host key confirm")
            task = response.json()
            _expect_input(task, key="bootstrap_step_1_confirm", kind="confirm", label="ssh step confirm prompt")

            response = client.post(
                f"/api/admin/server-bootstrap/{task_id}/input",
                json={"value": "yes"},
                headers=headers,
            )
            _assert_status(response, 200, "ssh step confirm input")
            task = response.json()
            if task.get("status") != "failed":
                raise NodePanelSshPromptFailure("SSH bootstrap should fail on ALLOW_SSH gate in controlled check")
            last_error = str(task.get("last_error") or "")
            if "ALLOW_SSH" not in last_error and "blocked until NELOMAI_AGENT_BOOTSTRAP_ALLOW_SSH=1 is set" not in last_error:
                raise NodePanelSshPromptFailure("SSH bootstrap failure did not reach controlled ALLOW_SSH gate")

        payloads = _load_payloads(log_file)
        for payload in _find_payloads(payloads, "bootstrap_server_input"):
            if payload.get("component") != "server-agent":
                raise NodePanelSshPromptFailure("bootstrap_server_input payload must target server-agent")
        input_values = [payload.get("input", {}).get("value") for payload in _find_payloads(payloads, "bootstrap_server_input")]
        if input_values != ["yes", "yes"]:
            raise NodePanelSshPromptFailure(f"Unexpected SSH prompt input sequence: {input_values!r}")

        print("OK: panel-to-node ssh prompt check passed")
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
        if previous_latest_version is None:
            os.environ.pop("NELOMAI_AGENT_LATEST_VERSION", None)
        else:
            os.environ["NELOMAI_AGENT_LATEST_VERSION"] = previous_latest_version
        _restore_nelomai_repo(previous_repo_url)
        _cleanup_records(prefix)


if __name__ == "__main__":
    try:
        main()
    except NodePanelSshPromptFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
