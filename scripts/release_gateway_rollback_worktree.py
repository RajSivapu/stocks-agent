#!/usr/bin/env python3
"""Dispose of a retained rollback checkout only after release evidence succeeds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.deploy_owner_dashboard_api import release_gateway_rollback_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    artifact = receipt.get("gateway_rollback_artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("repo_root"), str):
        raise SystemExit("retained gateway rollback artifact is unavailable")
    release_gateway_rollback_artifact({
        "repo_root": artifact["repo_root"],
        "commit_sha": artifact.get("git_sha", ""),
        "source_sha256": artifact.get("source_sha256", ""),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
