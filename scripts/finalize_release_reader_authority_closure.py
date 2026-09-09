#!/usr/bin/env python3
"""Resolve the closure lease only after its primary artifact is immutable."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Final

import psycopg

if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.deploy_owner_dashboard_api import (  # noqa: E402
    DurableMutationLease,
    validate_release_admin_session_url,
)
from scripts.managed_isolated_restore import canonical_json  # noqa: E402
from scripts.release_reader_authority_closure import (  # noqa: E402
    CLOSURE_LEASE_OWNER,
    FORMAT as CLOSURE_FORMAT,
    MAX_RECEIPT_BYTES as MAX_CLOSURE_RECEIPT_BYTES,
    apply_closure_transaction,
    closure_migration_manifest,
    verify_reader_state,
)
from scripts.protected_evidence import PostgresReadOnlySource  # noqa: E402
from scripts.verify_owner_dashboard_deployment import (  # noqa: E402
    validate_evidence_database_url,
)


FORMAT: Final = "stocks-release-reader-authority-closure-finalization-v1"
MAX_RECEIPT_BYTES = 8_192
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def validate_closure_receipt(
    receipt: object, args: argparse.Namespace,
) -> dict[str, object]:
    """Validate the exact primary receipt that the workflow already uploaded."""
    _require(
        isinstance(receipt, Mapping)
        and len(canonical_json(receipt).encode()) <= MAX_CLOSURE_RECEIPT_BYTES
        and set(receipt) == {
            "format", "repository", "main_sha", "reviewed_sha",
            "production_binding_sha256", "workflow", "authorization",
            "diagnostic", "pre_reader", "transaction", "post_reader",
            "lease", "started_at", "completed_at", "status",
            "receipt_sha256",
        },
        "primary closure receipt is malformed",
    )
    workflow = receipt.get("workflow")
    authorization = receipt.get("authorization")
    diagnostic = receipt.get("diagnostic")
    pre_reader = receipt.get("pre_reader")
    transaction = receipt.get("transaction")
    post_reader = receipt.get("post_reader")
    lease = receipt.get("lease")
    closure = closure_migration_manifest()
    core = dict(receipt)
    supplied_hash = core.pop("receipt_sha256", None)
    expected_hash = hashlib.sha256(canonical_json(core).encode()).hexdigest()
    expected_binding = hashlib.sha256(
        (CLOSURE_FORMAT + "\0" + args.project_ref).encode()
    ).hexdigest()
    _require(
        receipt.get("format") == CLOSURE_FORMAT
        and receipt.get("repository") == args.repository
        and receipt.get("main_sha") == args.main_sha
        and _SHA40.fullmatch(str(receipt.get("reviewed_sha", ""))) is not None
        and receipt.get("production_binding_sha256") == expected_binding
        and receipt.get("status") == "verified"
        and isinstance(receipt.get("started_at"), str)
        and isinstance(receipt.get("completed_at"), str)
        and isinstance(workflow, Mapping)
        and workflow == {
            "run_id": int(args.workflow_run_id),
            "run_attempt": int(args.workflow_run_attempt),
        }
        and isinstance(authorization, Mapping)
        and set(authorization) == {
            "kind", "id", "pull_request_number", "pr_ci_workflow_run_id",
            "ci_workflow_run_id",
        }
        and authorization.get("kind") in {"github_review", "owner_comment"}
        and all(
            _positive(authorization.get(key))
            for key in (
                "id", "pull_request_number", "pr_ci_workflow_run_id",
                "ci_workflow_run_id",
            )
        )
        and isinstance(diagnostic, Mapping)
        and set(diagnostic) == {
            "receipt_sha256", "issue_count", "main_sha", "workflow_run_id",
            "workflow_run_attempt", "artifact_id", "artifact_name",
            "artifact_digest",
        }
        and _SHA256.fullmatch(str(diagnostic.get("receipt_sha256", "")))
            is not None
        and diagnostic.get("issue_count") == 16
        and _SHA40.fullmatch(str(diagnostic.get("main_sha", ""))) is not None
        and all(
            _positive(diagnostic.get(key))
            for key in ("workflow_run_id", "workflow_run_attempt", "artifact_id")
        )
        and diagnostic.get("artifact_name")
            == "production-release-reader-diagnostic-"
               f"{diagnostic.get('workflow_run_id')}-"
               f"{diagnostic.get('workflow_run_attempt')}"
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(diagnostic.get("artifact_digest", "")),
        ) is not None
        and isinstance(pre_reader, Mapping)
        and pre_reader.get("state") in {"legacy_open", "strict_closed"}
        and _SHA256.fullmatch(str(pre_reader.get("authority_sha256", "")))
            is not None
        and isinstance(transaction, Mapping)
        and set(transaction) == {
            "state", "closure", "pre_extension_inventory",
            "pre_extension_inventory_sha256", "post_extension_function_count",
            "post_extension_relation_count", "post_extension_authority_sha256",
            "transaction_state",
        }
        and transaction.get("state") in {"applied", "already_closed"}
        and transaction.get("closure") == closure
        and transaction.get("post_extension_function_count") == 0
        and transaction.get("post_extension_relation_count") == 0
        and transaction.get("transaction_state") == "committed"
        and _SHA256.fullmatch(
            str(transaction.get("pre_extension_inventory_sha256", ""))
        ) is not None
        and _SHA256.fullmatch(
            str(transaction.get("post_extension_authority_sha256", ""))
        ) is not None
        and (
            (
                transaction.get("state") == "applied"
                and pre_reader.get("state") == "legacy_open"
                and diagnostic.get("main_sha") == receipt.get("main_sha")
            )
            or (
                transaction.get("state") == "already_closed"
                and pre_reader.get("state") == "strict_closed"
            )
        )
        and isinstance(post_reader, Mapping)
        and post_reader.get("state") == "strict_closed"
        and type(post_reader.get("read_table_count")) is int
        and post_reader["read_table_count"] > 0
        and _SHA256.fullmatch(str(post_reader.get("authority_sha256", "")))
            is not None
        and lease == {
            "owner": args.lease_owner,
            "kind": "recovery",
            "state": "awaiting_artifact_finalization",
        }
        and isinstance(supplied_hash, str)
        and _SHA256.fullmatch(supplied_hash) is not None
        and supplied_hash == expected_hash,
        "primary closure receipt is not exact",
    )
    return {
        "receipt_sha256": supplied_hash,
        "transaction_state": transaction["state"],
    }


def _load_receipt(path: Path) -> object:
    if (
        path.name != "release-reader-authority-closure.json"
        or not path.is_file() or path.is_symlink()
        or path.stat().st_size > MAX_CLOSURE_RECEIPT_BYTES
    ):
        raise RuntimeError("primary closure receipt path is unsafe")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("primary closure receipt is malformed") from error


def _validate_args(args: argparse.Namespace) -> None:
    expected_artifact = (
        f"release-reader-authority-closure-{args.workflow_run_id}-"
        f"{args.workflow_run_attempt}"
    )
    if (
        _PROJECT_REF.fullmatch(args.project_ref) is None
        or _SHA40.fullmatch(args.main_sha) is None
        or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository
        )
        or any(
            _POSITIVE_INTEGER.fullmatch(str(value)) is None
            for value in (
                args.workflow_run_id, args.workflow_run_attempt,
                args.closure_artifact_id,
            )
        )
        or args.closure_artifact_name != expected_artifact
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", args.closure_artifact_digest
        ) is None
        or args.lease_owner != CLOSURE_LEASE_OWNER
    ):
        raise ValueError("closure finalization metadata is malformed")


def _write_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(receipt) + "\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def finalize_closure(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    *,
    connector: Any = psycopg.connect,
    source_factory: Any = PostgresReadOnlySource,
    lease_factory: Any = DurableMutationLease,
) -> dict[str, object]:
    """Verify durable evidence plus exact closed state, then resolve the lease."""
    _validate_args(args)
    admin_url = environment.get("SUPAVISOR_SESSION_URL", "")
    reader_url = environment.get("RELEASE_READONLY_DATABASE_URL", "")
    if not admin_url or not reader_url:
        raise RuntimeError("protected closure database credentials are required")
    validate_release_admin_session_url(args.project_ref, admin_url)
    validate_evidence_database_url(
        reader_url,
        f"https://{args.project_ref}.supabase.co/functions/v1/owner-dashboard-api",
    )
    primary = validate_closure_receipt(
        _load_receipt(args.closure_receipt), args,
    )
    with lease_factory(admin_url, args.lease_owner, "recovery") as lease:
        first_readback = verify_reader_state(
            reader_url, args.project_ref, source_factory=source_factory,
        )
        _require(
            first_readback.get("state") == "strict_closed",
            "closure finalization reader is not strictly closed",
        )
        lease.heartbeat()
        with connector(
            admin_url, sslmode="verify-full", connect_timeout=15,
        ) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                transaction = apply_closure_transaction(cursor, verify_only=True)
        transaction["transaction_state"] = "committed"
        _require(
            transaction.get("state") == "already_closed",
            "closure finalization attempted a mutation",
        )
        final_readback = verify_reader_state(
            reader_url, args.project_ref, source_factory=source_factory,
        )
        _require(
            final_readback.get("state") == "strict_closed",
            "closure finalization fresh reader is not strictly closed",
        )
        lease.heartbeat()
        lease.resolve()
        receipt: dict[str, object] = {
            "format": FORMAT,
            "repository": args.repository,
            "main_sha": args.main_sha,
            "production_binding_sha256": hashlib.sha256(
                (FORMAT + "\0" + args.project_ref).encode()
            ).hexdigest(),
            "workflow": {
                "run_id": int(args.workflow_run_id),
                "run_attempt": int(args.workflow_run_attempt),
            },
            "primary_receipt": primary,
            "primary_artifact": {
                "id": int(args.closure_artifact_id),
                "name": args.closure_artifact_name,
                "digest": args.closure_artifact_digest,
            },
            "first_readback": first_readback,
            "transaction": transaction,
            "final_readback": final_readback,
            "lease": {
                "owner": args.lease_owner,
                "kind": "recovery",
                "state": "resolved",
            },
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "verified",
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            canonical_json(receipt).encode()
        ).hexdigest()
        _require(
            len(canonical_json(receipt).encode()) <= MAX_RECEIPT_BYTES,
            "closure finalization receipt exceeds bound",
        )
        _write_receipt(args.output, receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--closure-artifact-id", required=True)
    parser.add_argument("--closure-artifact-name", required=True)
    parser.add_argument("--closure-artifact-digest", required=True)
    parser.add_argument("--closure-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lease-owner", required=True)
    args = parser.parse_args()
    args.closure_receipt = args.closure_receipt.resolve()
    args.output = args.output.resolve()
    if (
        args.output.name
            != "release-reader-authority-closure-finalization.json"
        or not args.output.parent.is_dir()
        or args.output.exists()
        or args.output.parent.is_symlink()
    ):
        raise RuntimeError("closure finalization output path is unsafe")
    receipt = finalize_closure(args, os.environ)
    print(canonical_json({
        "receipt_sha256": receipt["receipt_sha256"],
        "status": receipt["status"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
