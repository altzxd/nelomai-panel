from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_panel_version() -> str:
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "0.0.0"
    return str(data.get("project", {}).get("version", "0.0.0"))
