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
    parser.add_argument("--candidate-script", required=True, type=Path)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    url = os.environ.get("RELEASE_READONLY_DATABASE_URL", "")
    command = shlex.split(args.safe_command)
    if not url or not command or not args.candidate_script.is_file() or args.candidate_sha not in command:
        raise SystemExit("a protected reader and candidate-bound read-only command are required")
    before = snapshot(url, args.production_project_ref)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit("safe dry-run command failed")
    after = snapshot(url, args.production_project_ref)
    deltas = {name: after["tables"][name]["count"] - before["tables"][name]["count"] for name in before["tables"]}
    if before["source"] != after["source"] or any(value != 0 for value in deltas.values()) or before["tables"] != after["tables"]:
        raise SystemExit("safe dry-run changed protected market evidence")
    for table in before["tables"].values():
        if (set(table) != {"count", "rows_sha256"} or type(table["count"]) is not int
                or not isinstance(table["rows_sha256"], str) or not __import__("re").fullmatch(r"[0-9a-f]{64}", table["rows_sha256"])):
            raise SystemExit("safe dry-run snapshot receipt is malformed")
    command_binding = json.dumps({"argv": command, "candidate_sha": args.candidate_sha,
                                  "candidate_script_sha256": hashlib.sha256(args.candidate_script.read_bytes()).hexdigest()},
                                 sort_keys=True, separators=(",", ":")).encode()
    evidence = {
        "before": before, "after": after, "table_deltas": deltas,
        "safe_command_argv": command,
        "candidate_script_sha256": hashlib.sha256(args.candidate_script.read_bytes()).hexdigest(),
        "safe_command_sha256": hashlib.sha256(command_binding).hexdigest(),
        "safe_command_exit_code": result.returncode,
    }
    args.output.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
