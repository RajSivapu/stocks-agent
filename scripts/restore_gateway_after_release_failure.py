#!/usr/bin/env python3
"""Dedicated post-evidence failure restoration, with cleanup unable to skip it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.deploy_owner_dashboard_api import (
    release_gateway_rollback_artifact,
    recover_gateway_from_state,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--receipt", type=Path)  # optional: state survives a killed deploy subprocess
    parser.add_argument("--release-state", required=True, type=Path)
    parser.add_argument("--recovery-root", type=Path)
    parser.add_argument("--retain-recovery-artifact", action="store_true")
    args = parser.parse_args()
    state = json.loads(args.release_state.read_text())
    recover_gateway_from_state(
        state, args.project_ref, args.admin_url, recovery_root=args.recovery_root,
        releaser=(lambda _artifact: None) if args.retain_recovery_artifact else release_gateway_rollback_artifact,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
