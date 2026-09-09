#!/usr/bin/env python3
"""Verify the production release reader before any protected mutation."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
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

from scripts.managed_isolated_restore import canonical_json  # noqa: E402
from scripts.protected_evidence import PostgresReadOnlySource  # noqa: E402
from scripts.verify_owner_dashboard_deployment import (  # noqa: E402
    validate_evidence_database_url,
)


FORMAT: Final = "stocks-release-reader-diagnostic-v1"
MAX_RECEIPT_BYTES = 8_192
_MAIN_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")
_AUTHORITY_ERROR_CODES: Final = {
    "pre-migration release reader baseline mismatch": "baseline_mismatch",
    "release reader role has unsafe role authority": "role_authority_mismatch",
    "release reader membership is not exact": "membership_mismatch",
    "release reader database authority is unsafe": "database_authority_mismatch",
    "release reader schema authority is unsafe": "schema_authority_mismatch",
    "release reader relation authority is unsafe": "relation_authority_mismatch",
    "release reader column authority is unsafe": "column_authority_mismatch",
    "release reader extension relation columns are unsafe": "extension_columns_mismatch",
    "release reader sequence authority is unsafe": "sequence_authority_mismatch",
    "release reader large-object authority is unsafe": "large_object_authority_mismatch",
    "release reader function authority is unsafe": "function_authority_mismatch",
    "release reader may not own database objects": "ownership_mismatch",
}


def _finalize(receipt: dict[str, object]) -> dict[str, object]:
    receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode()
    ).hexdigest()
    if len(canonical_json(receipt).encode()) > MAX_RECEIPT_BYTES:
        raise RuntimeError("release reader diagnostic receipt exceeds bound")
    return receipt


def _error_code(error: Exception) -> str:
    if isinstance(error, psycopg.errors.QueryCanceled):
        return "query_timeout"
    if isinstance(error, psycopg.OperationalError):
        return "connection_error"
    if isinstance(error, psycopg.Error):
        return "database_error"
    if isinstance(error, RuntimeError):
        return _AUTHORITY_ERROR_CODES.get(str(error), "preflight_mismatch")
    return "diagnostic_error"


def diagnose_release_reader(
    environment: Mapping[str, str],
    project_ref: str,
    main_sha: str,
    *,
    source_factory: Callable[..., Any] = PostgresReadOnlySource,
) -> dict[str, object]:
    if not _PROJECT_REF.fullmatch(project_ref) or not _MAIN_SHA.fullmatch(main_sha):
        raise ValueError("diagnostic binding is malformed")
    receipt: dict[str, object] = {
        "format": FORMAT,
        "main_sha": main_sha,
        "production_binding_sha256": hashlib.sha256(
            (FORMAT + "\0" + project_ref).encode()
        ).hexdigest(),
        "credential": {"status": "not_run"},
        "preflight": {"status": "not_run"},
    }
    database_url = environment.get("RELEASE_READONLY_DATABASE_URL")
    if not isinstance(database_url, str) or not database_url:
        receipt["credential"] = {
            "status": "failed", "error_code": "credential_missing",
        }
        return _finalize(receipt)
    try:
        validate_evidence_database_url(
            database_url,
            f"https://{project_ref}.supabase.co/functions/v1/owner-dashboard-api",
        )
    except ValueError:
        receipt["credential"] = {
            "status": "failed", "error_code": "credential_url_invalid",
        }
        return _finalize(receipt)
    receipt["credential"] = {"status": "valid"}

    try:
        with source_factory(
            database_url, project_ref, pre_migration_baseline=True,
        ) as source:
            identity = source.identity()
            authority = source.authority_receipt()
            scope = source.pre_migration_scope()
            if (
                identity.get("read_only") is not True
                or identity.get("isolated_guard") is not False
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(identity.get("connection_id", ""))
                )
                or authority.get("status") != "verified"
                or type(authority.get("read_table_count")) is not int
                or authority["read_table_count"] < 1
            ):
                raise RuntimeError("release reader diagnostic identity mismatch")
            absent = scope.get("absent_tables")
            unreadable = scope.get("unreadable_tables")
            if not isinstance(absent, list) or not isinstance(unreadable, list):
                raise RuntimeError("release reader diagnostic scope mismatch")
            receipt["preflight"] = {
                "status": "verified",
                "database_identity": "verified",
                "authority": "verified",
                "read_table_count": authority["read_table_count"],
                "baseline_state": (
                    "candidate_already_applied"
                    if not absent and not unreadable
                    else "exact_pre_migration"
                ),
                "absent_table_count": len(absent),
                "unreadable_table_count": len(unreadable),
            }
    except Exception as error:
        receipt["preflight"] = {
            "status": "failed", "error_code": _error_code(error),
        }
    return _finalize(receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.name != "release-reader-diagnostic.json" or not output.parent.is_dir():
        raise RuntimeError(
            "output must be release-reader-diagnostic.json in an existing directory"
        )
    receipt = diagnose_release_reader(os.environ, args.project_ref, args.main_sha)
    output.write_text(canonical_json(receipt) + "\n")
    output.chmod(0o600)
    succeeded = receipt.get("preflight", {}).get("status") == "verified"
    print(canonical_json({
        "receipt_sha256": receipt["receipt_sha256"],
        "status": "verified" if succeeded else "failed",
    }))
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
