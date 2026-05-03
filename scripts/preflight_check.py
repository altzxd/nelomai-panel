from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    allow_warnings: bool = False


CHECKS = [
    Check("syntax", [sys.executable, "-m", "compileall", "app", "scripts", "migrations"]),
    Check("routes", [sys.executable, "scripts/route_inventory.py"]),
    Check("encoding", [sys.executable, "scripts/encoding_check.py"]),
    Check("smoke", [sys.executable, "scripts/smoke_check.py"]),
    Check("agent contract", [sys.executable, "scripts/agent_contract_check.py"]),
    Check("agent contract docs", [sys.executable, "scripts/agent_contract_doc_check.py"]),
    Check("node safe-init", [sys.executable, "scripts/node_agent_safe_init_check.py"]),
    Check("node full-profile", [sys.executable, "scripts/node_agent_full_profile_check.py"]),
    Check("node panel profiles", [sys.executable, "scripts/node_agent_panel_bootstrap_profiles_check.py"]),
    Check("node panel e2e", [sys.executable, "scripts/node_agent_panel_e2e_check.py"]),
    Check("node ssh prompts", [sys.executable, "scripts/node_agent_panel_ssh_prompt_check.py"]),
    Check("node ssh exec", [sys.executable, "scripts/node_agent_panel_ssh_exec_check.py"]),
    Check("node live validation", [sys.executable, "scripts/node_agent_live_validation_check.py"]),
    Check("amnezia tool adapter", [sys.executable, "scripts/amnezia_official_tool_check.py"]),
    Check("tak tunnel audit links", [sys.executable, "scripts/tak_tunnel_audit_links_check.py"]),
    Check("tak tunnel clear backoff", [sys.executable, "scripts/tak_tunnel_clear_backoff_check.py"]),
    Check("tak tunnel clear-backoff route", [sys.executable, "scripts/tak_tunnel_clear_backoff_route_check.py"]),
    Check("tak tunnel focused diagnostics", [sys.executable, "scripts/tak_tunnel_focused_diagnostics_check.py"]),
    Check("tak tunnel focused action forms", [sys.executable, "scripts/tak_tunnel_focused_actions_form_check.py"]),
    Check("tak tunnel overview", [sys.executable, "scripts/tak_tunnel_overview_check.py"]),
    Check("tak tunnel logs backlink", [sys.executable, "scripts/tak_tunnel_logs_backlink_check.py"]),
    Check("tak tunnel repair route", [sys.executable, "scripts/tak_tunnel_repair_route_check.py"]),
    Check("tak tunnel rotate route", [sys.executable, "scripts/tak_tunnel_rotate_route_check.py"]),
    Check("panel server inventory", [sys.executable, "scripts/panel_server_inventory_check.py"]),
    Check("panel server release", [sys.executable, "scripts/panel_server_release_check.py"]),
    Check("panel server install script", [sys.executable, "scripts/panel_server_install_script_check.py"]),
    Check("panel beta runbook", [sys.executable, "scripts/panel_beta_runbook_check.py"]),
    Check("panel beta diagnostics", [sys.executable, "scripts/panel_beta_diagnostics_check.py"]),
    Check("panel beta servers summary", [sys.executable, "scripts/panel_beta_servers_summary_check.py"]),
    Check("panel beta overview", [sys.executable, "scripts/panel_beta_overview_check.py"]),
    Check("panel beta settings", [sys.executable, "scripts/panel_beta_settings_check.py"]),
    Check("panel versions summary", [sys.executable, "scripts/panel_versions_summary_check.py"]),
    Check("panel updates page", [sys.executable, "scripts/panel_updates_page_check.py"]),
    Check("panel server location", [sys.executable, "scripts/panel_server_location_check.py"]),
    Check("panel registration links", [sys.executable, "scripts/panel_registration_links_check.py"]),
    Check("first admin bootstrap", [sys.executable, "scripts/first_admin_bootstrap_check.py"]),
    Check("panel clients without interfaces", [sys.executable, "scripts/panel_clients_without_interfaces_check.py"]),
    Check("panel access and tunnels views", [sys.executable, "scripts/panel_access_and_tunnels_views_check.py"]),
    Check("security access", [sys.executable, "scripts/panel_security_access_check.py"]),
    Check("clean start", [sys.executable, "scripts/clean_start_check.py"]),
    Check("production config", [sys.executable, "scripts/panel_production_config_check.py"]),
    Check("panel runbook", [sys.executable, "scripts/panel_release_runbook_check.py"]),
    Check("release hygiene", [sys.executable, "scripts/release_hygiene_check.py"]),
    Check("release summary", [sys.executable, "scripts/release_summary_check.py"]),
    Check("remaining release gaps", [sys.executable, "scripts/release_remaining_gaps_check.py"]),
    Check("data flow", [sys.executable, "scripts/data_flow_check.py"]),
    Check("integrity", [sys.executable, "scripts/integrity_check.py"]),
    Check("migrations", [sys.executable, "scripts/migration_check.py"]),
    Check("config", [sys.executable, "scripts/config_check.py"], allow_warnings=True),
]


class PreflightFailure(RuntimeError):
    pass


def run_command(check: Check) -> tuple[str, str]:
    completed = subprocess.run(
        check.command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    output = "\n".join(part for part in [stdout, stderr] if part)
    if completed.returncode != 0:
        raise PreflightFailure(output or f"{check.name} exited with {completed.returncode}")
    return stdout, stderr


def run_startup_check() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/")
    if response.status_code != 200:
        raise PreflightFailure(f"startup: expected 200 from /, got {response.status_code}")


def print_step(status: str, name: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"{status}: {name}{suffix}")


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    for check in CHECKS:
        try:
            stdout, stderr = run_command(check)
        except PreflightFailure as exc:
            print_step("FAIL", check.name)
            failures.append(f"{check.name}: {exc}")
            continue

        output = "\n".join(part for part in [stdout, stderr] if part)
        warning_lines = [line for line in output.splitlines() if line.startswith("WARN:")]
        if warning_lines and check.allow_warnings:
            print_step("WARN", check.name, f"{len(warning_lines)} warning(s)")
            warnings.extend(f"{check.name}: {line}" for line in warning_lines)
        else:
            print_step("OK", check.name)

    try:
        run_startup_check()
        print_step("OK", "startup")
    except PreflightFailure as exc:
        print_step("FAIL", "startup")
        failures.append(str(exc))

    print("\nPreflight Summary")
    print("=================")
    print(f"OK checks: {len(CHECKS) + 1 - len(failures)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Failures: {len(failures)}")

    if warnings:
        print("\nWarnings")
        for item in warnings:
            print(f"- {item}")

    if failures:
        print("\nFailures")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nOK: preflight check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
