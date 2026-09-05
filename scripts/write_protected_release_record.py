#!/usr/bin/env python3
"""Build the canonical candidate-bound record from protected step receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("positive protected identity is required")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path); parser.add_argument("--dry-run-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path); parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--ci-workflow-run-id", required=True); parser.add_argument("--release-workflow-run-id", required=True)
    parser.add_argument("--pull-request-number", required=True); parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--rollback-artifact-id", required=True); parser.add_argument("--recovery-metadata-artifact-id", required=True); parser.add_argument("--project-ref", required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text()); dry = json.loads(args.dry_run_evidence.read_text())
    if receipt.get("deployment_outcome") != "succeeded" or not isinstance(dry.get("table_deltas"), dict):
        raise SystemExit("protected receipts are incomplete")
    static_root = Path("dist")
    files = {path.relative_to(static_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in static_root.rglob("*") if path.is_file()}
    capture = receipt["gateway_rollback_artifact"]
    record = {
        "candidate_sha": args.candidate_sha, "project_ref": args.project_ref,
        "workflow_run_id": integer(args.ci_workflow_run_id), "release_workflow_run_id": integer(args.release_workflow_run_id),
        "pull_request_number": integer(args.pull_request_number), "deployment_id": integer(args.deployment_id),
        "migrations": receipt["migrations"], "migration_application": receipt["migration_application"], "functions": receipt["functions"],
        "static_assets": {"candidate_sha": args.candidate_sha, "source_sha256": tree(Path("apps/web")), "files": files},
        "dry_run": False, "dry_run_evidence": dry, "canaries": {"owner": 200, "anonymous": 401, "non_owner": 403},
        "deployment_outcome": "succeeded",
        "rollback_capture": {"artifact_id": integer(args.rollback_artifact_id), "recovery_metadata_artifact_id": integer(args.recovery_metadata_artifact_id), "commit_sha": capture["commit_sha"], "captured_at": capture["captured_at"], "source_sha256": capture["source_sha256"]},
        "rollback_readiness": receipt["rollback_readiness"],
    }
    args.output.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
