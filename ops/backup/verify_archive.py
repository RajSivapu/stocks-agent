#!/usr/bin/env python3
"""Build and verify the deterministic, secret-free recovery archive format."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|_)(?:token|secret|password|authorization|cookie|api_key)(?:$|_)", re.I
)
PLAINTEXT_CREDENTIAL_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\bsb_(?:secret|service_role)_[A-Za-z0-9_-]{12,}|"
    r"\b\d{6,12}:[A-Za-z0-9_-]{20,})"
)
ALLOWED_INTERNAL_NONCES = {"lease_token"}


class ArchiveValidationError(ValueError):
    """The recovery archive does not satisfy the fixed release-one contract."""


@dataclass(frozen=True)
class DatasetSpec:
    required_columns: frozenset[str]
    exact_columns: frozenset[str] | None = None


# Durable application state only. Transient claims, pairing codes, rate limits, cached quotes,
# step-up receipts, and provider trigger attempts are deliberately not recoverable.
DATASET_SPECS: Mapping[str, DatasetSpec] = {
    "profiles": DatasetSpec(
        frozenset({"id", "display_name", "timezone", "status", "onboarding_completed_at", "created_at", "updated_at"}),
        frozenset({"id", "display_name", "timezone", "status", "onboarding_completed_at", "created_at", "updated_at"}),
    ),
    "app_admins": DatasetSpec(frozenset({"user_id", "role", "created_at"})),
    "user_consents": DatasetSpec(frozenset({"id", "owner_id", "document_version", "source", "accepted_at"})),
    "notification_preferences": DatasetSpec(frozenset({"owner_id"})),
    "agent_connections": DatasetSpec(
        frozenset({"id", "owner_id", "public_id", "provider", "credential_type", "status", "last_handshake_at"})
    ),
    "analysis_schedules": DatasetSpec(frozenset({"owner_id", "primary_connection_id", "timezone"})),
    "telegram_links": DatasetSpec(frozenset({"owner_id", "telegram_chat_id", "telegram_user_id", "status"})),
    "owner_policy_overrides": DatasetSpec(frozenset({"owner_id", "policy_version"})),
    "holdings": DatasetSpec(frozenset({"owner_id", "ticker", "shares", "avg_cost", "projection_sequence"})),
    "analysis_runs": DatasetSpec(frozenset({"id", "owner_id", "kind", "status"})),
    "transactions": DatasetSpec(frozenset({"id", "owner_id", "ticker", "event_type", "ledger_sequence"})),
    "portfolio_commands": DatasetSpec(frozenset({"id", "owner_id", "status", "operation", "normalized_input"})),
    "suggestions": DatasetSpec(frozenset({"id", "owner_id", "ticker", "action"})),
    "suggestion_grades": DatasetSpec(frozenset({"id", "owner_id", "suggestion_id"})),
    "stock_observations": DatasetSpec(frozenset({"id", "owner_id", "ticker"})),
    "daily_snapshots": DatasetSpec(frozenset({"id", "owner_id", "ticker", "snap_date"})),
    "dry_powder": DatasetSpec(frozenset({"owner_id", "month"})),
    "radar": DatasetSpec(frozenset({"owner_id", "ticker"})),
    "lessons": DatasetSpec(frozenset({"id", "owner_id", "category", "content"})),
    "paper_watches": DatasetSpec(frozenset({"id", "owner_id", "ticker", "status"})),
    "market_gateway_requests": DatasetSpec(frozenset({"request_id", "owner_id", "status"})),
    "decision_evaluations": DatasetSpec(frozenset({"id", "owner_id", "request_id"})),
    "market_publications": DatasetSpec(frozenset({"id", "owner_id", "request_id", "status"})),
    "owner_investment_plans": DatasetSpec(frozenset({"id", "owner_id", "ticker", "active"})),
    "owner_ledger_counters": DatasetSpec(frozenset({"owner_id", "next_sequence"})),
    "run_evidence": DatasetSpec(frozenset({"id", "owner_id", "run_id", "evidence_id"})),
    "source_search_receipts": DatasetSpec(frozenset({"id", "owner_id", "run_id"})),
    "corporate_action_states": DatasetSpec(frozenset({"owner_id", "ticker", "state"})),
    "agent_analysis_submissions": DatasetSpec(frozenset({"id", "owner_id", "run_id", "request_id", "status"})),
    "operational_events": DatasetSpec(frozenset({"id", "owner_id", "code", "status"})),
    "owner_operational_state": DatasetSpec(frozenset({"owner_id", "mutations_paused"})),
    "operational_alerts": DatasetSpec(frozenset({"id", "owner_id", "event_id", "status"})),
    "telegram_updates": DatasetSpec(frozenset({"owner_id", "telegram_update_id", "kind", "received_at"})),
    "telegram_deliveries": DatasetSpec(frozenset({"id", "owner_id", "telegram_update_id", "status"})),
    "app_api_audit_events": DatasetSpec(frozenset({"id", "owner_id", "request_id", "route", "result_code"})),
    "deletion_tombstones": DatasetSpec(
        frozenset({"owner_id", "deletion_request_id", "deleted_at", "archives_expire_after"})
    ),
    "owner_ledger_reset_receipts": DatasetSpec(
        frozenset({"id", "owner_id", "step_up_receipt_id", "export_digest", "row_counts", "reset_at"})
    ),
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [copy.deepcopy(dict(row)) for row in rows]
    return sorted(copied, key=canonical_json_bytes)


def _scan_for_credentials(value: Any, path: str = "archive") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN_KEY_RE.search(str(key)) and str(key) not in ALLOWED_INTERNAL_NONCES:
                raise ArchiveValidationError(f"plaintext credential field at {path}.{key}")
            _scan_for_credentials(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_credentials(child, f"{path}[{index}]")
    elif isinstance(value, str) and PLAINTEXT_CREDENTIAL_RE.search(value):
        raise ArchiveValidationError(f"plaintext credential value at {path}")


def _relationship_edges(datasets: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[list[str]]:
    edges: list[list[str]] = []
    for dataset, rows in sorted(datasets.items()):
        for row in rows:
            record = str(row.get("id") or row.get("request_id") or row.get("ticker") or row.get("month") or "row")
            if row.get("owner_id") is not None:
                edges.append([dataset, record, "owner", str(row["owner_id"])])
            if dataset == "profiles" and row.get("id") is not None:
                edges.append([dataset, record, "identity", str(row["id"])])
            if dataset == "app_admins" and row.get("user_id") is not None:
                edges.append([dataset, record, "identity", str(row["user_id"])])
            for field in (
                "primary_connection_id",
                "run_id",
                "source_run_id",
                "request_id",
                "suggestion_id",
                "event_id",
                "command_id",
                "corrects_transaction_id",
                "telegram_update_id",
            ):
                if row.get(field) is not None:
                    edges.append([dataset, record, field, str(row[field])])
    return sorted(edges)


def _assert_no_deleted_owner_resurrection(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    identity_rows: Sequence[Mapping[str, Any]],
) -> None:
    deleted = {
        str(row["owner_id"])
        for row in datasets.get("deletion_tombstones", [])
        if row.get("owner_id") is not None
    }
    if not deleted:
        return
    for row in identity_rows:
        if str(row.get("owner_id")) in deleted:
            raise ArchiveValidationError("deleted owner appears in identity recovery map")
    for dataset, rows in datasets.items():
        if dataset == "deletion_tombstones":
            continue
        for row in rows:
            candidates = {str(row.get("owner_id")), str(row.get("user_id"))}
            if dataset == "profiles":
                candidates.add(str(row.get("id")))
            if deleted.intersection(candidates):
                raise ArchiveValidationError(f"deleted owner resurrected by {dataset}")


def _validate_source_rows(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    identity_rows: Sequence[Mapping[str, Any]],
) -> None:
    if set(datasets) != set(DATASET_SPECS):
        raise ArchiveValidationError("archive dataset set does not match the recovery contract")
    for dataset, rows in datasets.items():
        if not isinstance(rows, list):
            raise ArchiveValidationError(f"{dataset} rows must be an array")
        spec = DATASET_SPECS[dataset]
        for row in rows:
            if not isinstance(row, dict):
                raise ArchiveValidationError(f"{dataset} row must be an object")
            columns = set(row)
            if not spec.required_columns.issubset(columns):
                raise ArchiveValidationError(f"{dataset} row is missing required columns")
            if spec.exact_columns is not None and columns != set(spec.exact_columns):
                raise ArchiveValidationError(f"{dataset} row has unexpected columns")
    if not isinstance(identity_rows, list):
        raise ArchiveValidationError("identity recovery map must be an array")
    seen: set[str] = set()
    for row in identity_rows:
        if not isinstance(row, dict) or set(row) != {"owner_id", "email"}:
            raise ArchiveValidationError("identity recovery row has unexpected columns")
        owner_id = str(row["owner_id"])
        email = str(row["email"])
        if not UUID_RE.fullmatch(owner_id) or not EMAIL_RE.fullmatch(email) or len(email) > 254:
            raise ArchiveValidationError("identity recovery row is malformed")
        if owner_id in seen:
            raise ArchiveValidationError("identity recovery owner is duplicated")
        seen.add(owner_id)
    _scan_for_credentials({"datasets": datasets, "identity_recovery": identity_rows})
    _assert_no_deleted_owner_resurrection(datasets, identity_rows)


def build_archive(
    *,
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    identity_rows: Sequence[Mapping[str, Any]],
    exported_at: datetime,
) -> dict[str, Any]:
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise ArchiveValidationError("export timestamp must be timezone-aware")
    normalized = {name: _canonical_rows(datasets[name]) for name in sorted(datasets)}
    identities = _canonical_rows(identity_rows)
    _validate_source_rows(normalized, identities)
    entries = {
        name: {
            "columns": sorted({key for row in rows for key in row}),
            "count": len(rows),
            "row_digest": _digest(rows),
            "rows": rows,
        }
        for name, rows in normalized.items()
    }
    payload: dict[str, Any] = {
        "format": "stock-agent-recovery",
        "schema_version": SCHEMA_VERSION,
        "exported_at": exported_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "datasets": entries,
        "identity_recovery": identities,
        "identity_digest": _digest(identities),
        "relationship_digest": _digest(_relationship_edges(normalized)),
        "projection_digest": _digest({
            "holdings": normalized["holdings"],
            "transactions": normalized["transactions"],
        }),
    }
    payload["archive_digest"] = _digest(payload)
    return verify_archive(payload)


def verify_archive(value: Mapping[str, Any]) -> dict[str, Any]:
    archive = copy.deepcopy(dict(value))
    expected_keys = {
        "format", "schema_version", "exported_at", "datasets", "identity_recovery",
        "identity_digest", "relationship_digest", "projection_digest", "archive_digest",
    }
    if set(archive) != expected_keys or archive.get("format") != "stock-agent-recovery":
        raise ArchiveValidationError("archive envelope is malformed")
    if archive.get("schema_version") != SCHEMA_VERSION:
        raise ArchiveValidationError("archive schema version is unsupported")
    try:
        exported = datetime.fromisoformat(str(archive["exported_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise ArchiveValidationError("archive timestamp is malformed") from error
    if exported.tzinfo is None or exported.utcoffset() is None:
        raise ArchiveValidationError("archive timestamp is not timezone-aware")
    entries = archive.get("datasets")
    if not isinstance(entries, dict) or set(entries) != set(DATASET_SPECS):
        raise ArchiveValidationError("archive dataset set does not match the recovery contract")
    _scan_for_credentials({
        "datasets": entries,
        "identity_recovery": archive.get("identity_recovery"),
    })
    rows_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for name, spec in DATASET_SPECS.items():
        entry = entries[name]
        if not isinstance(entry, dict) or set(entry) != {"columns", "count", "row_digest", "rows"}:
            raise ArchiveValidationError(f"{name} dataset envelope is malformed")
        rows = entry["rows"]
        if not isinstance(rows, list) or entry["count"] != len(rows):
            raise ArchiveValidationError(f"{name} row count does not match")
        if entry["row_digest"] != _digest(rows):
            raise ArchiveValidationError(f"{name} row digest does not match")
        if rows != _canonical_rows(rows):
            raise ArchiveValidationError(f"{name} rows are not canonically ordered")
        columns = sorted({key for row in rows if isinstance(row, dict) for key in row})
        if entry["columns"] != columns:
            raise ArchiveValidationError(f"{name} column manifest does not match")
        rows_by_dataset[name] = rows
    identities = archive.get("identity_recovery")
    _validate_source_rows(rows_by_dataset, identities)
    if identities != _canonical_rows(identities) or archive["identity_digest"] != _digest(identities):
        raise ArchiveValidationError("identity recovery digest does not match")
    if archive["relationship_digest"] != _digest(_relationship_edges(rows_by_dataset)):
        raise ArchiveValidationError("relationship digest does not match")
    if archive["projection_digest"] != _digest({
        "holdings": rows_by_dataset["holdings"],
        "transactions": rows_by_dataset["transactions"],
    }):
        raise ArchiveValidationError("projection digest does not match")
    if not SHA256_RE.fullmatch(str(archive["archive_digest"])):
        raise ArchiveValidationError("archive digest is malformed")
    supplied_digest = archive.pop("archive_digest")
    if supplied_digest != _digest(archive):
        raise ArchiveValidationError("archive digest does not match")
    archive["archive_digest"] = supplied_digest
    return archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.archive.stat().st_size > 512 * 1024 * 1024:
            raise ArchiveValidationError("archive exceeds the verification limit")
        value = json.loads(args.archive.read_text(encoding="utf-8"))
        verified = verify_archive(value)
        print(json.dumps({
            "status": "verified",
            "schema_version": verified["schema_version"],
            "archive_digest": verified["archive_digest"],
            "dataset_count": len(verified["datasets"]),
        }, sort_keys=True))
        return 0
    except (ArchiveValidationError, json.JSONDecodeError, OSError) as error:
        print(f"archive verification failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
