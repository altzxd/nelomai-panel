from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models import AuditLog
from app.services import serialize_audit_log


class AuditLinkCheckFailure(RuntimeError):
    pass


def build_log(event_type: str) -> AuditLog:
    details = {
        "tic_server_id": 11,
        "tic_server_name": "tic-alpha",
        "tak_server_id": 22,
        "tak_server_name": "tak-beta",
    }
    return AuditLog(
        id=1,
        event_type=event_type,
        severity="warning",
        message=f"test {event_type}",
        message_ru=f"test {event_type}",
        details=json.dumps(details, ensure_ascii=False),
        created_at=datetime.now(UTC),
    )


def assert_diagnostics_link(event_type: str) -> None:
    view = serialize_audit_log(build_log(event_type))
    expected = "/admin/diagnostics?focused_tic_server_id=11&focused_tak_server_id=22#check-tak_tunnels"
    if view.pair_label != "tic-alpha → tak-beta":
        raise AuditLinkCheckFailure(
            f"{event_type}: expected pair_label 'tic-alpha → tak-beta', got {view.pair_label!r}"
        )
    if view.diagnostics_url != expected:
        raise AuditLinkCheckFailure(
            f"{event_type}: expected diagnostics_url {expected!r}, got {view.diagnostics_url!r}"
        )


def run() -> None:
    for event_type in (
        "tak_tunnels.cooldown",
        "tak_tunnels.manual_attention_required",
        "tak_tunnels.manual_repaired",
    ):
        assert_diagnostics_link(event_type)
    print("OK: tak tunnel audit links check passed")


if __name__ == "__main__":
    try:
        run()
    except AuditLinkCheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
