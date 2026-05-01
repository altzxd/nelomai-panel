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


class NodePanelE2EFailure(RuntimeError):
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
        raise NodePanelE2EFailure(f"{label}: expected {expected}, got {response.status_code}. {detail}")


def _load_admin() -> User:
    with SessionLocal() as db:
        ensure_seed_data(db)
        ensure_default_settings(db)
        user = db.execute(select(User).where(User.role == UserRole.ADMIN).order_by(User.id.asc())).scalars().first()
        if user is None:
            raise NodePanelE2EFailure("No admin user found")
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

        orphan_servers = db.execute(select(Server).where(Server.name.like(f"{prefix}%"))).scalars().all()
        for server in orphan_servers:
            db.delete(server)

        orphan_tasks = db.execute(
            select(ServerBootstrapTask).where(ServerBootstrapTask.id.in_(task_ids))
        ).scalars().all()
        for task in orphan_tasks:
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


def _find_payload(payloads: list[dict[str, Any]], action: str) -> dict[str, Any]:
    for payload in reversed(payloads):
        if payload.get("action") == action:
            return payload
    raise NodePanelE2EFailure(f"Node agent log does not contain action {action!r}")


def main() -> None:
    node_bin = _node_binary()
    if not node_bin:
        print("SKIP: node is not installed; panel-to-node e2e check was not run")
        return

    agent_entry = ROOT_DIR / "agents" / "node-tic-agent" / "src" / "index.js"
    if not agent_entry.exists():
        raise NodePanelE2EFailure(f"Missing node agent entry: {agent_entry}")

    admin = _load_admin()
    prefix = f"node-e2e-{uuid.uuid4().hex[:8]}"
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
    previous_bootstrap_input_required = os.environ.get("NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED")
    previous_latest_version = os.environ.get("NELOMAI_AGENT_LATEST_VERSION")
    previous_repo_url = _set_nelomai_repo("https://github.com/example/nelomai.git")

    _cleanup_records(prefix)
    try:
        settings.peer_agent_command = f'"{node_bin}" ".\\agents\\node-tic-agent\\src\\index.js"'
        os.environ["NELOMAI_AGENT_COMPONENT"] = "tic-agent"
        os.environ["NELOMAI_AGENT_STATE_FILE"] = str(state_file)
        os.environ["NELOMAI_AGENT_LOG"] = str(log_file)
        os.environ["NELOMAI_AGENT_STUB_MODE"] = ""
        os.environ["NELOMAI_AGENT_BOOTSTRAP_MODE"] = "dry-run"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_TRANSPORT"] = "noop"
        os.environ["NELOMAI_AGENT_BOOTSTRAP_INPUT_REQUIRED"] = "1"
        os.environ["NELOMAI_AGENT_LATEST_VERSION"] = "0.1.1"

        with TestClient(app) as client:
            headers = _auth_headers(admin)
            create_response = client.post(
                "/api/admin/servers",
                json={
                    "server_type": "tic",
                    "name": server_name,
                    "host": "127.0.0.61",
                    "ssh_port": 22,
                    "ssh_login": "root",
                    "ssh_password": "secret",
                },
                headers=headers,
            )
            _assert_status(create_response, 201, "bootstrap create")
            created_task = create_response.json()
            if created_task.get("status") != "input_required":
                raise NodePanelE2EFailure("Bootstrap create must return input_required for panel e2e check")
            if created_task.get("input_key") != "install_confirm" or created_task.get("input_kind") != "confirm":
                raise NodePanelE2EFailure("Bootstrap create returned unexpected input key/kind")
            task_id = int(created_task["id"])
            snapshot = created_task.get("bootstrap_snapshot") or {}
            if int(snapshot.get("command_count") or 0) <= 0:
                raise NodePanelE2EFailure("Bootstrap create did not return command_count in snapshot")

            status_response = client.get(f"/api/admin/server-bootstrap/{task_id}", headers=headers)
            _assert_status(status_response, 200, "bootstrap status")
            status_payload = status_response.json()
            if status_payload.get("status") != "input_required":
                raise NodePanelE2EFailure("Bootstrap status must remain input_required before admin input")

            input_response = client.post(
                f"/api/admin/server-bootstrap/{task_id}/input",
                json={"value": "yes"},
                headers=headers,
            )
            _assert_status(input_response, 200, "bootstrap input")
            completed_task = input_response.json()
            if completed_task.get("status") != "completed":
                raise NodePanelE2EFailure("Bootstrap input did not complete Node-agent bootstrap flow")
            if not completed_task.get("server_id"):
                raise NodePanelE2EFailure("Completed bootstrap did not create server record in panel")
            completed_snapshot = completed_task.get("bootstrap_snapshot") or {}
            if int(completed_snapshot.get("executed_step_count") or 0) <= 0:
                raise NodePanelE2EFailure("Completed bootstrap snapshot does not contain executed steps")
            if completed_snapshot.get("planned") is not True:
                raise NodePanelE2EFailure("Dry-run bootstrap should remain planned=true in snapshot")

        payloads = _load_payloads(log_file)
        for action in ("bootstrap_server", "bootstrap_server_input"):
            payload = _find_payload(payloads, action)
            if payload.get("component") != "server-agent":
                raise NodePanelE2EFailure(f"Action {action!r} was sent with wrong component")
            if payload.get("server", {}).get("name") != server_name:
                raise NodePanelE2EFailure(f"Action {action!r} was sent with wrong server payload")

        print("OK: panel-to-node bootstrap e2e check passed")
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
    except NodePanelE2EFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
