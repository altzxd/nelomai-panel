from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CHECKED_PATHS = [
    ROOT_DIR / "app" / "templates",
    ROOT_DIR / "app" / "static" / "app.js",
]

MOJIBAKE_MARKERS = [
    "\ufffd",  # replacement character
    "\u045f",  # often appears in mojibake for "П"
    "\u0403",
    "\u0453",
    "\u0409",
    "\u0459",
    "\u040a",
    "\u045a",
    "\u040b",
    "\u045b",
    "\u040c",
    "\u045c",
    "\u045e",
    "\u20ac",
    "\u2122",
    "\u0412\u00b7",
]


def iter_checked_files() -> list[Path]:
    files: list[Path] = []
    for path in CHECKED_PATHS:
        if path.is_dir():
            files.extend(sorted(path.glob("*.html")))
        elif path.exists():
            files.append(path)
    return files


def main() -> int:
    failures: list[str] = []
    for path in iter_checked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"{path.relative_to(ROOT_DIR)} is not valid UTF-8: {exc}")
            continue
        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT_DIR)} contains suspicious encoding marker {marker!r}")
    if failures:
        print("FAIL: encoding check found suspicious UI text")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("OK: encoding check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
