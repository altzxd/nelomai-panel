from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


SCRIPT_PATH = ROOT_DIR / "scripts" / "install_panel_server.sh"

REQUIRED_TOKENS = [
    "postgresql",
    "python3.11",
    "openssh-client",
    "sshpass",
    "DATABASE_URL",
    "PANEL_PUBLIC_BASE_URL",
    "PANEL_TLS_EMAIL",
    "peer_agent_ssh_bridge.py",
    ".env.example",
    "alembic upgrade head",
    "create_database",
    "write_env_file",
    "write_systemd_unit",
    "write_nginx_site",
    "certbot --nginx",
    "systemctl enable --now",
    "journalctl -u",
]


class InstallScriptFailure(RuntimeError):
    pass


def run() -> None:
    if not SCRIPT_PATH.exists():
        raise InstallScriptFailure(f"missing script: {SCRIPT_PATH.relative_to(ROOT_DIR)}")
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if token not in content]
    if missing:
        raise InstallScriptFailure(
            "panel install script misses required tokens: " + ", ".join(missing)
        )
    print("OK: panel server install script check passed")


if __name__ == "__main__":
    try:
        run()
    except InstallScriptFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
