#!/usr/bin/env python3
"""Dedicated post-evidence failure restoration, with cleanup unable to skip it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.deploy_owner_dashboard_api import (
    restore_gateway_after_release_failure,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    artifact = receipt.get("gateway_rollback_artifact")
    if not isinstance(artifact, dict):
        raise SystemExit("retained gateway rollback artifact is unavailable")
    gateway = {
        "repo_root": artifact.get("repo_root"),
        "commit_sha": artifact.get("git_sha"),
        "source_sha256": artifact.get("source_sha256"),
    }
    restore_gateway_after_release_failure(args.project_ref, args.admin_url, gateway)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
