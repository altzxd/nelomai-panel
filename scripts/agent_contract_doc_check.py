from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services import ACTION_CAPABILITIES


def main() -> int:
    doc_path = ROOT_DIR / "docs" / "agent_contract.md"
    document = doc_path.read_text(encoding="utf-8")
    missing_actions = [action for action in sorted(ACTION_CAPABILITIES) if f"`{action}`" not in document]
    capabilities = sorted({capability for values in ACTION_CAPABILITIES.values() for capability in values})
    missing_capabilities = [capability for capability in capabilities if capability not in document]
    if missing_actions or missing_capabilities:
        if missing_actions:
            print("Missing documented actions:")
            for action in missing_actions:
                print(f"- {action}")
        if missing_capabilities:
            print("Missing documented capabilities:")
            for capability in missing_capabilities:
                print(f"- {capability}")
        return 1
    print("OK: agent contract documentation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
