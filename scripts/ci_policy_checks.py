#!/usr/bin/env python3
"""Repository-local CI policy checks that never echo matching credentials."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
FORBIDDEN_NAMES = {".env", ".env.local", "secrets.local.json"}
FORBIDDEN_PATHS = {Path(".codex/config.toml"), Path("supabase/.env.local")}
PLACEHOLDERS = ("replace-me", "example", "test-only", "test_only", "placeholder")
SECRET_MARKERS = (
    "-----BEGIN " + "PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
)
SECRET_PATTERNS = (
    re.compile(r"sb_secret_[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
)
ASSIGNMENT_NAMES = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "R2_SECRET_ACCESS_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
)


class PolicyFailure(RuntimeError):
    """A tracked file violates the release policy."""


def _looks_like_placeholder(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in PLACEHOLDERS)


def scan_text(path: Path, text: str) -> None:
    if path in FORBIDDEN_PATHS or path.name in FORBIDDEN_NAMES or (
        path.name.startswith(".env.") and not path.name.endswith(".example")
    ):
        raise PolicyFailure(f"tracked local-secret file: {path.as_posix()}")
    for number, line in enumerate(text.splitlines(), start=1):
        if _looks_like_placeholder(line):
            continue
        if any(marker in line for marker in SECRET_MARKERS) or any(
            pattern.search(line) for pattern in SECRET_PATTERNS
        ):
            raise PolicyFailure(f"credential-shaped material: {path.as_posix()}:{number}")
        for name in ASSIGNMENT_NAMES:
            assignment = re.search(
                rf"\b{re.escape(name)}\s*=\s*(['\"])([^'\"]+)\1", line
            )
            if assignment and not _looks_like_placeholder(assignment.group(2)):
                raise PolicyFailure(f"literal secret assignment: {path.as_posix()}:{number}")


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def scan_tracked_files() -> int:
    count = 0
    for path in _tracked_files():
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PolicyFailure(f"tracked file exceeds scan bound: {path.relative_to(ROOT)}")
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scan_text(relative, text)
        count += 1
    return count


def lint_sql_files(paths: list[Path]) -> int:
    if not paths:
        raise PolicyFailure("no SQL files were selected")
    for path in paths:
        try:
            parse_sql(path.read_text(encoding="utf-8"))
        except Exception as error:
            raise PolicyFailure(f"SQL parse failed: {path.as_posix()}") from error
    return len(paths)


def repository_sql_files() -> list[Path]:
    return sorted({
        *(ROOT / "sql").rglob("*.sql"),
        *(ROOT / "supabase/migrations").glob("*.sql"),
    })


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan-secrets", "sql-lint"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        count = scan_tracked_files() if args.command == "scan-secrets" else lint_sql_files(
            repository_sql_files()
        )
        print(f"{args.command}: passed ({count} files)")
        return 0
    except (OSError, PolicyFailure, subprocess.SubprocessError):
        print(f"{args.command}: rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
