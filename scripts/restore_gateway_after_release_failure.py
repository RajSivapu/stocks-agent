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
    parser.add_argument("--receipt", type=Path)  # optional: state survives a killed deploy subprocess
    parser.add_argument("--release-state", required=True, type=Path)
    args = parser.parse_args()
    state = json.loads(args.release_state.read_text())
    artifact = state.get("artifact")
    if not state.get("changed"):
        return 0
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
