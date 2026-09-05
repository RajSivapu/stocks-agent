#!/usr/bin/env python3
"""Capture protected DB snapshots around the actual safe dry-run command."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from scripts.protected_evidence import PostgresReadOnlySource


def snapshot(url: str, project_ref: str) -> dict:
    # Separate read-only transactions are essential: a repeatable-read reader
    # cannot prove an after state from its own snapshot.
    with PostgresReadOnlySource(url, project_ref) as source:
        return source.dry_run_snapshot()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--safe-command", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    url = os.environ.get("RELEASE_READONLY_DATABASE_URL", "")
    command = shlex.split(args.safe_command)
    if not url or not command or "--dry-run" not in command:
        raise SystemExit("a protected reader and explicit safe --dry-run command are required")
    before = snapshot(url, args.production_project_ref)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit("safe dry-run command failed")
    after = snapshot(url, args.production_project_ref)
    deltas = {name: after["tables"][name]["count"] - before["tables"][name]["count"] for name in before["tables"]}
    if any(value != 0 for value in deltas.values()) or before["tables"] != after["tables"]:
        raise SystemExit("safe dry-run changed protected market evidence")
    evidence = {
        "before": before, "after": after, "table_deltas": deltas,
        "safe_command_sha256": hashlib.sha256(args.safe_command.encode()).hexdigest(),
        "safe_command_exit_code": result.returncode,
    }
    args.output.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
