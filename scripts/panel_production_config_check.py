from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class ProductionConfigFailure(RuntimeError):
    pass


def run_case(label: str, env_overrides: dict[str, str], should_pass: bool) -> None:
    env = {
        **os.environ,
        "NELOMAI_CONFIG_PROFILE": "production",
        "DEBUG": "false",
        "SECRET_KEY": "prod-secret-key-with-at-least-32-chars-123456",
        "DATABASE_URL": "postgresql+psycopg://nelomai:secret@db.example.local/nelomai",
        "NELOMAI_GIT_REPO": "https://github.com/altzxd/nelomai-panel.git",
        "PEER_AGENT_COMMAND": ".venv/bin/python scripts/peer_agent_ssh_bridge.py",
        **env_overrides,
    }
    result = subprocess.run(
        [sys.executable, "scripts/config_check.py"],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    passed = result.returncode == 0
    if should_pass and not passed:
        raise ProductionConfigFailure(
            f"{label}: expected pass, got failure:\n{result.stdout}\n{result.stderr}"
        )
    if not should_pass and passed:
        raise ProductionConfigFailure(f"{label}: expected failure, got pass")


def run() -> None:
    run_case("valid production config", {}, should_pass=True)
    run_case("debug forbidden", {"DEBUG": "true"}, should_pass=False)
    run_case(
        "placeholder secret forbidden",
        {"SECRET_KEY": "dev-only-change-me-with-a-long-random-value"},
        should_pass=False,
    )
    run_case("sqlite forbidden", {"DATABASE_URL": "sqlite+pysqlite:///./nelomai-panel.db"}, should_pass=False)
    run_case("empty nelomai git repo forbidden", {"NELOMAI_GIT_REPO": ""}, should_pass=False)
    run_case("empty peer agent command forbidden", {"PEER_AGENT_COMMAND": ""}, should_pass=False)
    print("OK: production config rules check passed")


if __name__ == "__main__":
    try:
        run()
    except ProductionConfigFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
