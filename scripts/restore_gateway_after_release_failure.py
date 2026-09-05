#!/usr/bin/env python3
"""Dedicated post-evidence failure restoration, with cleanup unable to skip it."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deploy_owner_dashboard_api import (
    DurableMutationLease,
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
    parser.add_argument("--lease-owner", required=True)
    parser.add_argument("--release-run-id", type=int)
    args = parser.parse_args()
    raw = args.release_state.read_bytes() if args.release_state.is_file() else None
    if args.release_run_id is not None or raw is None or not raw.startswith(b"{"):
        from cryptography.fernet import Fernet
        from scripts.release_components import EncryptedJournal, SiteBoundTransport, load_native_release_adapter, recover_components
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        adapter = load_native_release_adapter({"project_ref": args.project_ref, "candidate_sha": candidate,
                                               "release_run_id": args.release_run_id, "recovery": True})
        if args.release_run_id is not None:
            raw = adapter.recover_retained(args.release_run_id)
        if not isinstance(raw, bytes):
            raise RuntimeError("authenticated component recovery artifact is unavailable")
        key = os.environ.get("RELEASE_RECOVERY_KEY", "").encode()
        state = json.loads(Fernet(key).decrypt(raw))
        context = state.get("release_context", {})
        if (context.get("project_ref") != args.project_ref or context.get("candidate_sha") != candidate
                or (args.release_run_id is not None and str(context.get("release_run_id")) != str(args.release_run_id))):
            raise RuntimeError("component recovery candidate/project/run binding mismatch")
        sink = EncryptedJournal(args.release_state, key, retain=adapter.retain)
        with DurableMutationLease(args.admin_url, args.lease_owner, "recovery") as lease:
            recover_components(SiteBoundTransport(adapter, adapter.site), state, persist=sink)
            lease.heartbeat()
            lease.resolve()
        return 0
    state = json.loads(raw)
    # Acquiring the same durable session lock makes recovery a safe takeover
    # after a release runner disappears, while an active release cannot be
    # raced between a preflight check and its first remote mutation.
    with DurableMutationLease(args.admin_url, args.lease_owner, "recovery") as lease:
        recover_gateway_from_state(
            state, args.project_ref, args.admin_url, recovery_root=args.recovery_root,
            releaser=(lambda _artifact: None) if args.retain_recovery_artifact else release_gateway_rollback_artifact,
        )
        lease.heartbeat()
        lease.resolve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
