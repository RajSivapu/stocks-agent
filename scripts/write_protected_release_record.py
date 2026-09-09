#!/usr/bin/env python3
"""Build the canonical candidate-bound record from protected step receipts."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from lib.release_baseline import expected_snapshot_tables


AUTH_CANARY_INVENTORY = {
    "status": "verified",
    "identity_count": 2,
    "privileged_owner_count": 1,
    "denied_canary_count": 1,
}


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


def validate_release_identity(
    receipt: dict[str, object], candidate_sha: str, reviewed_sha: str
) -> tuple[str, str]:
    """Bind the protected receipt to one candidate and one reviewed PR head."""
    if (
        re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None
        or re.fullmatch(r"[0-9a-f]{40}", reviewed_sha) is None
        or receipt.get("candidate_sha") != candidate_sha
    ):
        raise RuntimeError("release identity is incomplete or mismatched")
    return candidate_sha, reviewed_sha


def protected_canary_evidence(receipt: dict[str, object]) -> dict[str, object]:
    """Require and retain the exact owner, anonymous, and denied-canary proof."""
    canary = receipt.get("canary")
    if (
        not isinstance(canary, dict)
        or canary.get("status") != "verified"
        or canary.get("source_reconciliation") != "verified"
        or canary.get("source_database_role") != "stock_agent_dashboard_runtime"
        or canary.get("financial_write_routes") != 0
        or canary.get("brokerage_authority") != "none"
        or canary.get("friend_invitations") != "disabled"
        or canary.get("owner_route_count") != 10
        or canary.get("unauthenticated_status") != 401
        or canary.get("non_owner_status") != 403
        or canary.get("owner_session") != "revoked"
        or canary.get("non_owner_session") != "revoked"
        or canary.get("auth_inventory_preflight") != AUTH_CANARY_INVENTORY
        or canary.get("auth_inventory_readback") != AUTH_CANARY_INVENTORY
    ):
        raise RuntimeError("protected release canary receipt is incomplete")
    return {
        "canaries": {"owner": 200, "anonymous": 401, "non_owner": 403},
        "auth_canary": {
            "owner_session": "revoked",
            "non_owner_session": "revoked",
            "inventory_preflight": copy.deepcopy(AUTH_CANARY_INVENTORY),
            "inventory_readback": copy.deepcopy(AUTH_CANARY_INVENTORY),
        },
    }


def release_evidence_classes(receipt: dict[str, object]) -> dict[str, object]:
    """Classify immediate release proof without promoting it to scheduled proof."""
    candidate_sha = receipt.get("candidate_sha")
    if (
        not isinstance(candidate_sha, str)
        or len(candidate_sha) != 40
        or any(character not in "0123456789abcdef" for character in candidate_sha)
    ):
        raise RuntimeError("protected release canary receipt is incomplete")
    protected_canary_evidence(receipt)
    return {
        "protected_backend": {"status": "verified", "candidate_sha": candidate_sha},
        "owner_site": {
            "status": "pending",
            "required_evidence": "current_authenticated_native_connector_observation",
        },
        "operational_scheduled": {
            "status": "pending",
            "required_evidence": "normal_post_release_scheduled_receipt",
        },
        "discovery_capability": {
            "status": "pending",
            "checkpoint": "V1-C3",
            "required_evidence": "normal_post_release_scheduled_capability_receipt",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path); parser.add_argument("--dry-run-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path); parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-ci-workflow-run-id", required=True)
    parser.add_argument("--ci-workflow-run-id", required=True); parser.add_argument("--release-workflow-run-id", required=True)
    parser.add_argument("--authorization-kind", choices=("github_review", "owner_comment"), required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--release-workflow-run-attempt", required=True)
    parser.add_argument("--pull-request-number", required=True); parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--backend-evidence-artifact-id", required=True)
    parser.add_argument("--backend-evidence-artifact-name", required=True)
    parser.add_argument("--backend-evidence-artifact-digest", required=True)
    parser.add_argument("--project-ref", required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text()); dry = json.loads(args.dry_run_evidence.read_text())
    validate_release_identity(receipt, args.candidate_sha, args.reviewed_sha)
    before = dry.get("before")
    after = dry.get("after")
    omissions = before.get("pre_migration_omissions") if isinstance(before, dict) else None
    expected_snapshot_table_names = expected_snapshot_tables(omissions)
    if (receipt.get("deployment_outcome") != "succeeded" or not isinstance(dry.get("table_deltas"), dict)
            or not isinstance(before, dict) or not isinstance(after, dict)
            or omissions != after.get("pre_migration_omissions")
            or expected_snapshot_table_names is None
            or not isinstance(before.get("tables"), dict) or not isinstance(after.get("tables"), dict)
            or set(before["tables"]) != set(expected_snapshot_table_names)
            or set(after["tables"]) != set(expected_snapshot_table_names)
            or set(dry["table_deltas"]) != set(expected_snapshot_table_names)
            or [row.get("component") for row in receipt.get("component_readbacks", [])] != [
                "market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio"]):
        raise SystemExit("protected receipts are incomplete")
    if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository) is None
            or re.fullmatch(r"backend-component-evidence-[1-9][0-9]*-[1-9][0-9]*", args.backend_evidence_artifact_name) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", args.backend_evidence_artifact_digest) is None):
        raise SystemExit("protected backend artifact identity is malformed")
    artifact_id = integer(args.backend_evidence_artifact_id)
    run_id = integer(args.release_workflow_run_id)
    run_attempt = integer(args.release_workflow_run_attempt)
    if args.backend_evidence_artifact_name != f"backend-component-evidence-{run_id}-{run_attempt}":
        raise SystemExit("protected backend artifact identity is mismatched")
    canary_evidence = protected_canary_evidence(receipt)
    component_readbacks = copy.deepcopy(receipt["component_readbacks"])
    for row in component_readbacks:
        row["artifact_id"] = artifact_id
        prior = row.get("prior")
        if isinstance(prior, dict) and prior.get("exists") is True:
            prior["artifact_id"] = artifact_id
    static = receipt.get("static_assets")
    if (not isinstance(static, dict) or static.get("candidate_sha") != args.candidate_sha
            or not isinstance(static.get("files"), dict) or not static["files"]):
        raise SystemExit("candidate static build receipt is incomplete")
    record = {
        "candidate_sha": args.candidate_sha, "reviewed_sha": args.reviewed_sha,
        "repository": args.repository, "project_ref": args.project_ref,
        "workflow_run_id": integer(args.ci_workflow_run_id), "release_workflow_run_id": run_id,
        "release_workflow_run_attempt": run_attempt,
        "pull_request_number": integer(args.pull_request_number), "deployment_id": integer(args.deployment_id),
        "release_authorization": {"kind": args.authorization_kind,
            "id": integer(args.authorization_id),
            "pr_ci_workflow_run_id": integer(args.pr_ci_workflow_run_id)},
        "migrations": receipt["migrations"], "migration_application": receipt["migration_application"], "functions": receipt["functions"],
        "static_assets": copy.deepcopy(static),
        "dry_run": False, "dry_run_evidence": dry,
        "canaries": canary_evidence["canaries"],
        "auth_canary": canary_evidence["auth_canary"],
        "deployment_outcome": "succeeded",
        "component_readbacks": component_readbacks,
        "backend_evidence_artifact": {"artifact_id": artifact_id,
            "name": args.backend_evidence_artifact_name,
            "digest": args.backend_evidence_artifact_digest,
            "manifest_sha256": receipt["backend_evidence"]["manifest_sha256"],
            "recovery_metadata_sha256": receipt["backend_evidence"]["recovery_metadata_sha256"]},
        "recovery_journal": receipt["recovery_journal"],
        "evidence_classes": release_evidence_classes(receipt),
    }
    args.output.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
