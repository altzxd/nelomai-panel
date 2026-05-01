from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


GITIGNORE_PATH = ROOT_DIR / ".gitignore"
REQUIRED_GITIGNORE_PATTERNS = {
    ".env",
    ".env.*",
    "!.env.example",
    ".tmp/",
    "__pycache__/",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "nelomai_panel.egg-info/",
}
FORBIDDEN_ROOT_FILES = {
    ".env",
}
FORBIDDEN_SECRET_FILENAMES = {
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}


class ReleaseHygieneFailure(RuntimeError):
    pass


def read_gitignore_patterns() -> set[str]:
    if not GITIGNORE_PATH.exists():
        raise ReleaseHygieneFailure(".gitignore is missing")
    lines = set()
    for raw_line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.add(line)
    return lines


def check_gitignore() -> None:
    patterns = read_gitignore_patterns()
    missing = sorted(REQUIRED_GITIGNORE_PATTERNS - patterns)
    if missing:
        raise ReleaseHygieneFailure(".gitignore misses required patterns: " + ", ".join(missing))


def check_forbidden_root_files() -> None:
    present = sorted(name for name in FORBIDDEN_ROOT_FILES if (ROOT_DIR / name).exists())
    if present:
        raise ReleaseHygieneFailure("forbidden root files present: " + ", ".join(present))


def check_forbidden_secret_files() -> None:
    found: list[str] = []
    for path in ROOT_DIR.rglob("*"):
        if not path.is_file():
            continue
        if ".tmp" in path.parts:
            continue
        if path.name in FORBIDDEN_SECRET_FILENAMES:
            found.append(str(path.relative_to(ROOT_DIR)))
    if found:
        raise ReleaseHygieneFailure("forbidden secret-like files present: " + ", ".join(sorted(found)))


def run() -> None:
    check_gitignore()
    check_forbidden_root_files()
    check_forbidden_secret_files()
    print("OK: release hygiene check passed")


if __name__ == "__main__":
    try:
        run()
    except ReleaseHygieneFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
