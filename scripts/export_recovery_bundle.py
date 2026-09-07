#!/usr/bin/env python3
"""Export a read-only database snapshot through authenticated local encryption."""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from typing import Mapping, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.intelligence.cursors import (  # noqa: E402
    CollectionPage,
    CollectionWindow,
    SourceCursor,
    parse_time,
    update_cursor,
)
from lib.intelligence.universe import (  # noqa: E402
    reference_manifest_semantic_document,
    security_revision_semantic_document,
)

REQUIRED_RECOVERY_RECORDS = (
    "holdings", "transactions", "commands", "command_acknowledgements", "runs",
    "gateway_requests", "policies", "intelligence_runs",
    "reference_manifests", "security_reference_revisions", "discovery_stage_tasks",
    "reference_chunk_receipts", "reference_snapshot_memberships",
    "reference_finalization_seals", "reference_run_bindings",
    "reference_predecessor_pins", "reference_transfer_requests", "reference_transfer_responses",
    "theme_episode_revisions", "exposure_facts", "research_nominations",
    "packets", "reports",
    "intelligence_run_events", "source_quota_reservations", "collection_checkpoints",
    "collection_checkpoint_history", "collection_completions", "report_origins",
    "publications", "evaluation_publications", "cash_ledger_state",
    "cash_snapshots", "run_terminal_outcomes", "decision_evaluations", "policy_comparisons", "roles", "schema_version", "release_migration_ledger",
)
NULLABLE_TEXT = (str, type(None))
DATASET_FIELDS = {
    "holdings": {"ticker": str, "shares": str, "average_cost": str,
                 **{key: NULLABLE_TEXT for key in ("bucket", "opened_at", "notes", "stop", "target", "high_water_price", "hold_override_until")},
                 **{key: (bool, type(None)) for key in ("stop_alert_active", "stop_near_alert_active", "target_near_alert_active", "target_alert_active")}},
    "transactions": {"id": str, "ticker": str, "quantity": str, "price": str, "ts": str, "side": str, "source": NULLABLE_TEXT, "executed_on": NULLABLE_TEXT},
    "commands": {**{key: str for key in ("id", "status", "telegram_update_id", "chat_id", "user_id", "operation", "ticker", "expires_at", "created_at", "updated_at")},
                 **{key: NULLABLE_TEXT for key in ("qty", "price", "executed_on", "bucket", "stop", "expected_shares",
                                                    "amount", "cadence", "next_due_on", "expected_plan_updated_at",
                                                    "confirmation_message_id", "applied_at", "realized_pnl", "error")},
                 "preview": dict, "result": (dict, type(None))},
    "command_acknowledgements": {
        "command_id": str, "telegram_update_id": str, "status": str, "result": dict,
        "error": NULLABLE_TEXT, "lease_token": NULLABLE_TEXT, "lease_expires_at": NULLABLE_TEXT,
        "attempt_count": int, "created_at": str, "updated_at": str,
    },
    "runs": {"id": str, "status": str, "phase": str, "started_at": str, "finished_at": NULLABLE_TEXT,
             "data_as_of": NULLABLE_TEXT, "source_status": dict, "symbols": list, "write_counts": dict,
             "telegram_message_ids": list, "summary": NULLABLE_TEXT, "error": NULLABLE_TEXT,
             "scheduled_phase": NULLABLE_TEXT, "scheduled_market_date": NULLABLE_TEXT,
             "gateway_request_id": NULLABLE_TEXT},
    "gateway_requests": {
        "request_id": str, "operation": str, "run_id": NULLABLE_TEXT, "status": str,
        "lease_token": str, "attempt_count": int, "response": (dict, type(None)),
        "response_digest": NULLABLE_TEXT, "created_at": str, "claimed_at": str,
        "finished_at": NULLABLE_TEXT,
    },
    "policies": {"version": int, "config": dict, "active": bool, "created_at": str, "activated_at": NULLABLE_TEXT},
    "intelligence_runs": {
        "id": str, "phase": str, "market_date": str, "policy_version": int,
        "reservation_plan": dict, "request_window": (dict, type(None)), "created_at": str,
    },
    "reference_manifests": {
        "id": str, "run_id": str, "reference_version": str, "revision": int,
        "capability_version": int, "taxonomy_version": int, "source_hash": str,
        "valid_from": str, "valid_to": NULLABLE_TEXT, "manifest": dict,
        "content_hash": str, "created_at": str,
    },
    "security_reference_revisions": {
        "id": str, "manifest_id": str, "run_id": str, "revision": int,
        "security_id": str, "entity_id": str, "ticker": str, "exchange": NULLABLE_TEXT,
        "instrument_type": str, "eligible": bool, "exclusion_reasons": list,
        "aliases": list, "source_ids": list, "valid_from": str, "valid_to": NULLABLE_TEXT,
        "content_hash": str, "semantic_encoding_version": int,
        "issuer_names": (dict, type(None)), "created_at": str,
    },
    "reference_chunk_receipts": {
        "manifest_id": str, "run_id": str, "capability_id": str,
        "chunk_index": int, "chunk_count": int, "entry_count": int,
        "chunk_hash": str, "predecessor_manifest_id": NULLABLE_TEXT,
        "payload": dict, "created_at": str,
    },
    "reference_snapshot_memberships": {
        "manifest_id": str, "security_revision_id": str, "security_id": str,
        "ordinal": int, "created_at": str,
    },
    "reference_finalization_seals": {
        "manifest_id": str, "run_id": str, "capability_id": str,
        "predecessor_manifest_id": NULLABLE_TEXT, "chunk_count": int,
        "security_count": int, "root_hash": str, "finalized_at": str,
    },
    "reference_run_bindings": {
        "run_id": str, "capability_id": str, "manifest_id": NULLABLE_TEXT,
        "reference_status": str, "reference_as_of": str,
        "source_retrieved_at": NULLABLE_TEXT, "request_payload": dict,
        "reference_age_seconds": (int, type(None)), "created_at": str,
    },
    "reference_predecessor_pins": {
        "run_id": str, "capability_id": str, "manifest_id": NULLABLE_TEXT,
        "reference_status": str, "reference_as_of": str,
        "source_retrieved_at": NULLABLE_TEXT, "request_payload": dict,
        "reference_age_seconds": (int, type(None)), "created_at": str,
    },
    "reference_transfer_requests": {
        "request_id": str, "run_id": str, "operation": str,
        "encoded_bytes": int, "request_hash": str, "request_payload": dict,
        "created_at": str,
    },
    "reference_transfer_responses": {
        "request_id": str, "run_id": str, "encoded_bytes": int,
        "response_hash": str, "created_at": str,
    },
    "discovery_stage_tasks": {
        "id": str, "run_id": str, "stage": str, "capability_id": str, "provider": str,
        "query_kind": str, "query_hash": str, "dependency_ids": list,
        "requested_window": dict, "state": str, "attempt_count": int,
        "request_budget": int, "result": dict, "created_at": str, "updated_at": str,
    },
    "theme_episode_revisions": {
        "id": str, "run_id": str, "task_id": str, "theme_id": str, "revision": int,
        "episode": dict, "source_ids": list, "valid_from": str, "valid_to": NULLABLE_TEXT,
        "content_hash": str, "created_at": str,
    },
    "exposure_facts": {
        "id": str, "run_id": str, "task_id": str, "security_revision_id": str,
        "theme_episode_revision_id": NULLABLE_TEXT, "exposure_kind": str, "fact": dict,
        "source_ids": list, "valid_from": str, "valid_to": NULLABLE_TEXT,
        "content_hash": str, "created_at": str,
    },
    "research_nominations": {
        "id": str, "run_id": str, "task_id": str, "security_revision_id": str,
        "theme_episode_revision_id": NULLABLE_TEXT, "exposure_fact_ids": list,
        "state": str, "rationale": dict, "created_at": str, "updated_at": str,
    },
    "intelligence_run_events": {
        "id": str, "run_id": str, "status": str, "detail": dict, "created_at": str,
    },
    "source_quota_reservations": {
        "id": str, "run_id": str, "provider": str, "market_date": str, "phase": str,
        "reserved_requests": int, "cache_keys": list, "created_at": str,
    },
    "collection_checkpoints": {
        "run_id": str, "cache_key": str, "request_window": dict,
        "source_receipt_id": str, "payload": dict, "created_at": str,
    },
    "collection_checkpoint_history": {
        "run_id": str, "cache_key": str, "source_receipt_id": str,
        "payload": dict, "replaced_at": str,
    },
    "collection_completions": {
        "completion_id": str, "run_id": str, "payload": dict, "receipt": dict,
        "created_at": str,
    },
    "packets": {"id": str, "run_id": str, "policy_version": int, "status": str,
                "candidate_count": int, "evidence_count": int, "packet_hash": str,
                "packet": dict, "created_at": str},
    "reports": {"id": str, "run_id": str, "packet_id": str, "idempotency_key": str,
                "market_date": str, "kind": str, "report_hash": str, "rendered_hash": str,
                "report": dict, "rendered_text": str, "created_at": str},
    "report_origins": {
        "request_id": str, "run_id": str, "scheduled_phase": str, "market_date": str,
        "requested_kind": str, "requested_report_id": str, "requested_packet_id": str,
        "requested_idempotency_key": str, "requested_report_hash": str, "created_at": str,
    },
    "publications": {"report_id": str, "idempotency_key": str, "status": str, "telegram_message_ids": list,
                     "telegram_accepted_at": NULLABLE_TEXT, "suppression_reason": NULLABLE_TEXT,
                     "attempt_count": int, "lease_token": NULLABLE_TEXT, "lease_expires_at": NULLABLE_TEXT,
                     "error": NULLABLE_TEXT, "created_at": str, "updated_at": str},
    "evaluation_publications": {
        "id": str, "idempotency_key": str, "run_id": NULLABLE_TEXT, "market_date": str,
        "phase": str, "kind": str, "template_version": int, "rendered_body": str,
        "rendered_hash": str, "status": str, "telegram_message_ids": list,
        "attempt_count": int, "lease_token": NULLABLE_TEXT, "sending_started_at": NULLABLE_TEXT,
        "delivered_at": NULLABLE_TEXT, "telegram_accepted_at": NULLABLE_TEXT,
        "error": NULLABLE_TEXT, "created_at": str, "updated_at": str,
    },
    "cash_ledger_state": {"singleton": bool, "revision": str, "updated_at": str},
    "cash_snapshots": {
        "id": str, "as_of": str, "fresh_through": str, "ledger_watermark": str,
        "core_available": str, "growth_available": str, "speculative_available": str,
        "created_at": str,
    },
    "run_terminal_outcomes": {
        "run_id": str, "evaluation_request_id": str, "outcome": str, "created_at": str,
    },
    "decision_evaluations": {
        "id": str, "request_id": NULLABLE_TEXT, "run_id": NULLABLE_TEXT, "candidate_id": str,
        "policy_version": (int, type(None)), "input_digest": str, "raw_action": str, "final_action": NULLABLE_TEXT,
        "policy_status": str, "reason_codes": list, "explanations": list, "normalized": dict,
        "evidence": list, "analyst": dict, "checker": dict, "created_at": str,
    },
    "policy_comparisons": {"id": str, "run_id": str, "packet_id": str, "evaluation_id": str,
                           "comparison": dict, "created_at": str},
    "roles": {"role": str, "login": bool, "inherit": bool, "superuser": bool, "bypass_rls": bool, "memberships": list, "grants": list},
    "schema_version": {"version": str, "statements": list, "sha256": str},
    "release_migration_ledger": {"path": str, "version": str, "sha256": str, "applied_at": str},
}
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@runtime_checkable
class RecoveryDataSource(Protocol):
    def identity(self) -> Mapping[str, object]: ...
    def read_records(self) -> Mapping[str, object]: ...
    def counts(self) -> Mapping[str, int]: ...


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


_REFERENCE_MANIFEST_FIELDS = (
    "id", "reference_version", "revision", "capability_version", "taxonomy_version",
    "source_hash", "valid_from", "valid_to", "manifest", "content_hash",
)
_REFERENCE_SECURITY_FIELDS_V1 = (
    "id", "manifest_id", "revision", "security_id", "entity_id", "ticker",
    "exchange", "instrument_type", "eligible", "exclusion_reasons", "aliases",
    "source_ids", "valid_from", "valid_to", "content_hash",
)


def _semantic_hash(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _chunk_hash(entries: list[Mapping[str, object]]) -> str:
    return hashlib.sha256("\n".join(
        "\x1f".join((str(row["security_id"]), str(row["id"]), str(row["content_hash"])))
        for row in entries
    ).encode()).hexdigest()


def _validate_reference_semantic_lineage(
    manifests: Mapping[str, dict[str, object]],
    security_revisions: Mapping[str, dict[str, object]],
    receipts_by_manifest: Mapping[str, list[dict[str, object]]],
    memberships_by_manifest: Mapping[str, list[dict[str, object]]],
    seals: Mapping[str, dict[str, object]],
) -> None:
    """Recompute the same versioned reference graph authenticated at ingestion."""
    try:
        for manifest in manifests.values():
            if manifest["content_hash"] != _semantic_hash(
                reference_manifest_semantic_document(manifest)
            ):
                raise ValueError("reference manifest semantic hash mismatch")
        for revision in security_revisions.values():
            if revision["content_hash"] != _semantic_hash(
                security_revision_semantic_document(revision)
            ):
                raise ValueError("reference security semantic hash mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("reference semantic content is invalid") from exc

    flattened_by_manifest: dict[str, list[dict[str, object]]] = {}
    for manifest_id, receipts in receipts_by_manifest.items():
        begin_rows = [row for row in receipts if row["chunk_index"] == -1]
        if len(begin_rows) != 1:
            raise ValueError("reference begin receipt lineage is invalid")
        begin = begin_rows[0]
        begin_payload = begin["payload"]
        if set(begin_payload) != {
            "manifest", "capability_id", "chunk_count", "security_count",
            "root_hash", "predecessor_manifest_id",
        }:
            raise ValueError("reference begin payload is invalid")
        payload_manifest = begin_payload["manifest"]
        if not isinstance(payload_manifest, Mapping) \
                or set(payload_manifest) != set(_REFERENCE_MANIFEST_FIELDS):
            raise ValueError("reference begin manifest is invalid")
        if payload_manifest["id"] != manifest_id \
                or payload_manifest["content_hash"] != _semantic_hash(
                    reference_manifest_semantic_document(payload_manifest)
                ):
            raise ValueError("reference begin manifest hash mismatch")
        stored_manifest = manifests.get(manifest_id)
        if stored_manifest is not None and (
            payload_manifest["content_hash"] != stored_manifest["content_hash"]
            or reference_manifest_semantic_document(payload_manifest)
            != reference_manifest_semantic_document(stored_manifest)
        ):
            raise ValueError("reference finalized manifest differs from begin")
        body = payload_manifest["manifest"]
        if not isinstance(body, Mapping):
            raise ValueError("reference manifest body is invalid")
        version = body.get("format_version", 1)
        if isinstance(version, bool) or version not in {1, 2}:
            raise ValueError("reference manifest format is invalid")
        if begin_payload["capability_id"] != begin["capability_id"] \
                or begin_payload["chunk_count"] != begin["chunk_count"] \
                or begin_payload["root_hash"] != begin["chunk_hash"] \
                or begin_payload["predecessor_manifest_id"] != begin["predecessor_manifest_id"]:
            raise ValueError("reference begin receipt differs from payload")

        chunks = sorted(
            (row for row in receipts if row["chunk_index"] >= 0),
            key=lambda row: row["chunk_index"],
        )
        flattened: list[dict[str, object]] = []
        seen_security_ids: set[str] = set()
        seen_revision_ids: set[str] = set()
        issuer_names_by_entity: dict[str, object] = {}
        security_fields = set(_REFERENCE_SECURITY_FIELDS_V1)
        if version == 2:
            security_fields.update({"semantic_encoding_version", "issuer_names"})
        for chunk in chunks:
            payload = chunk["payload"]
            if set(payload) != {
                "manifest_id", "chunk_index", "chunk_count", "entries", "chunk_hash",
            } or payload["manifest_id"] != manifest_id \
                    or payload["chunk_index"] != chunk["chunk_index"] \
                    or payload["chunk_count"] != chunk["chunk_count"]:
                raise ValueError("reference chunk payload lineage is invalid")
            entries = payload["entries"]
            if not isinstance(entries, list) or len(entries) != chunk["entry_count"] \
                    or not 1 <= len(entries) <= (88 if version == 2 else 200):
                raise ValueError("reference chunk entry count is invalid")
            for entry in entries:
                if not isinstance(entry, Mapping) or set(entry) != security_fields \
                        or entry["manifest_id"] != manifest_id \
                        or entry.get("semantic_encoding_version", 1) != version:
                    raise ValueError("reference chunk entry schema is invalid")
                if entry["content_hash"] != _semantic_hash(
                    security_revision_semantic_document(entry)
                ):
                    raise ValueError("reference chunk security hash mismatch")
                if entry["security_id"] in seen_security_ids or entry["id"] in seen_revision_ids:
                    raise ValueError("reference chunk identity is duplicated")
                seen_security_ids.add(str(entry["security_id"]))
                seen_revision_ids.add(str(entry["id"]))
                if version == 2:
                    entity_id = str(entry["entity_id"])
                    prior_names = issuer_names_by_entity.setdefault(
                        entity_id, entry["issuer_names"]
                    )
                    if prior_names != entry["issuer_names"]:
                        raise ValueError("reference chunk issuer names mismatch")
                flattened.append(dict(entry))
            expected_chunk_hash = _chunk_hash(entries)
            if payload["chunk_hash"] != expected_chunk_hash \
                    or chunk["chunk_hash"] != expected_chunk_hash:
                raise ValueError("reference chunk hash mismatch")
        root_hash = hashlib.sha256("".join(
            str(row["chunk_hash"]) for row in chunks
        ).encode()).hexdigest()
        if len(chunks) == begin["chunk_count"] and (
            root_hash != begin["chunk_hash"]
            or len(flattened) != begin_payload["security_count"]
        ):
            raise ValueError("reference transfer root mismatch")
        flattened_by_manifest[manifest_id] = flattened

    if set(manifests) != set(seals):
        raise ValueError("reference manifest finalization lineage mismatch")
    for manifest_id, seal in seals.items():
        entries = flattened_by_manifest.get(manifest_id)
        if entries is None or len(entries) != seal["security_count"]:
            raise ValueError("reference finalized entries are unavailable")
        manifest = manifests[manifest_id]
        if manifest["manifest"].get("security_count") != len(entries):
            raise ValueError("reference manifest security count mismatch")
        members = sorted(
            memberships_by_manifest.get(manifest_id, []), key=lambda row: row["ordinal"]
        )
        if len(members) != len(entries):
            raise ValueError("reference membership count mismatch")
        predecessor_id = seal["predecessor_manifest_id"]
        predecessor_members = {
            row["security_id"]: row for row in memberships_by_manifest.get(predecessor_id, [])
        } if predecessor_id is not None else {}
        for ordinal, (entry, member) in enumerate(zip(entries, members, strict=True)):
            revision = security_revisions.get(member["security_revision_id"])
            if member["ordinal"] != ordinal or member["security_id"] != entry["security_id"] \
                    or revision is None or revision["security_id"] != entry["security_id"] \
                    or revision["content_hash"] != entry["content_hash"] \
                    or security_revision_semantic_document(revision) != \
                    security_revision_semantic_document(entry):
                raise ValueError("reference membership semantic lineage mismatch")
            if member["security_revision_id"] != entry["id"]:
                predecessor_member = predecessor_members.get(str(entry["security_id"]))
                if predecessor_member is None or predecessor_member[
                    "security_revision_id"
                ] != member["security_revision_id"]:
                    raise ValueError("reference membership reuse lineage mismatch")
            predecessor_member = predecessor_members.get(str(entry["security_id"]))
            predecessor_revision = security_revisions.get(
                predecessor_member["security_revision_id"]
            ) if predecessor_member is not None else None
            reusable_revision_id = (
                predecessor_member["security_revision_id"]
                if predecessor_revision is not None
                and predecessor_revision["content_hash"] == entry["content_hash"]
                and security_revision_semantic_document(predecessor_revision)
                == security_revision_semantic_document(entry)
                else None
            )
            expected_revision_id = reusable_revision_id or entry["id"]
            if member["security_revision_id"] != expected_revision_id:
                raise ValueError("reference membership materialization lineage mismatch")


def _validate_reference_cross_ledger_lineage(
    receipts_by_manifest: Mapping[str, list[dict[str, object]]],
    seals: Mapping[str, dict[str, object]],
    predecessor_pins: Mapping[tuple[str, str], dict[str, object]],
) -> None:
    """Bind transfer receipts to the predecessor state the consuming run pinned."""
    for manifest_id, receipts in receipts_by_manifest.items():
        begin_rows = [row for row in receipts if row["chunk_index"] == -1]
        if len(begin_rows) != 1:
            raise ValueError("reference begin receipt lineage is invalid")
        begin = begin_rows[0]
        run_id = str(begin["run_id"])
        capability_id = str(begin["capability_id"])
        predecessor_id = begin["predecessor_manifest_id"]
        pin = predecessor_pins.get((run_id, capability_id))
        expected_status = "reference_stale" if predecessor_id is not None \
            else "reference_unavailable"
        if pin is None or pin["manifest_id"] != predecessor_id \
                or pin["reference_status"] != expected_status:
            raise ValueError("reference predecessor pin lineage mismatch")
        for receipt in receipts:
            if receipt["manifest_id"] != manifest_id \
                    or receipt["run_id"] != run_id \
                    or receipt["capability_id"] != capability_id \
                    or receipt["predecessor_manifest_id"] != predecessor_id:
                raise ValueError("reference receipt predecessor lineage mismatch")
        seal = seals.get(manifest_id)
        if seal is not None and (
            seal["manifest_id"] != manifest_id
            or seal["run_id"] != run_id
            or seal["capability_id"] != capability_id
            or seal["predecessor_manifest_id"] != predecessor_id
        ):
            raise ValueError("reference finalization predecessor lineage mismatch")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_identity(source: RecoveryDataSource) -> dict[str, object]:
    if isinstance(source, Mapping) or not isinstance(source, RecoveryDataSource):
        raise RuntimeError("a queried read-only database source is required")
    identity = dict(source.identity())
    if (not re.fullmatch(r"[a-z0-9]{20}", str(identity.get("project_ref", "")))
            or not isinstance(identity.get("connection_id"), str) or not identity["connection_id"].strip()
            or identity.get("read_only") is not True):
        raise RuntimeError("database source identity is unavailable or unsafe")
    return identity


def _no_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {"password", "secret", "token", "database_url", "access_token", "service_key", "telegram_token", "rolpassword"}:
                raise ValueError("recovery data contains a forbidden secret field")
            _no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _no_secrets(child)


def _validated_records(records: Mapping[str, object]) -> dict[str, list[dict[str, object]]]:
    if not isinstance(records, Mapping) or set(records) != set(REQUIRED_RECOVERY_RECORDS):
        raise ValueError("recovery records require exact allowlisted datasets")
    _no_secrets(records)
    result = {}
    for name, fields in DATASET_FIELDS.items():
        rows = records[name]
        if not isinstance(rows, list) or (name in {"runs", "policies", "roles", "schema_version", "cash_ledger_state"} and not rows):
            raise ValueError(f"recovery dataset {name} requires meaningful rows")
        clean = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != set(fields) or any(not isinstance(row[key], kind) for key, kind in fields.items()):
                raise ValueError(f"recovery dataset {name} has unknown or invalid fields")
            clean.append(dict(row))
        identity_fields = {
            "holdings": "ticker", "command_acknowledgements": "command_id",
            "gateway_requests": "request_id", "policies": "version",
            "publications": "report_id", "cash_ledger_state": "singleton",
            "run_terminal_outcomes": "run_id", "roles": "role", "schema_version": "version",
            "release_migration_ledger": "path",
            "collection_checkpoints": ("run_id", "cache_key"),
            "collection_checkpoint_history": ("run_id", "cache_key", "source_receipt_id"),
            "collection_completions": "completion_id", "report_origins": "request_id",
            "reference_chunk_receipts": ("manifest_id", "chunk_index"),
            "reference_snapshot_memberships": ("manifest_id", "security_id"),
            "reference_finalization_seals": "manifest_id",
            "reference_run_bindings": ("run_id", "capability_id"),
            "reference_predecessor_pins": ("run_id", "capability_id"),
            "reference_transfer_requests": "request_id",
            "reference_transfer_responses": "request_id",
        }.get(name, "id")
        if isinstance(identity_fields, str):
            identity_fields = (identity_fields,)
        identities = [tuple(row[field] for field in identity_fields) for row in clean]
        if any(any(value is None or value == "" for value in identity) for identity in identities) \
                or len(set(identities)) != len(identities):
            raise ValueError(f"recovery dataset {name} has duplicate or missing identities")
        result[name] = sorted(clean, key=canonical_json)
    expected_role_shapes = {
        "stock_agent_dashboard": {"login": False, "inherit": False},
        "stock_agent_dashboard_runtime": {"login": True, "inherit": True},
    }
    if ({row["role"] for row in result["roles"]} != set(expected_role_shapes)
            or any(row["superuser"] is not False or row["bypass_rls"] is not False
                   or row["login"] is not expected_role_shapes[row["role"]]["login"]
                   or row["inherit"] is not expected_role_shapes[row["role"]]["inherit"]
                   for row in result["roles"])):
        raise ValueError("recovery role shapes are invalid")
    runs = {row["id"] for row in result["runs"]}
    commands = {row["id"] for row in result["commands"]}
    requests = {row["request_id"] for row in result["gateway_requests"]}
    policies = {row["version"] for row in result["policies"]}
    packets = {row["id"]: row for row in result["packets"]}
    reports = {row["id"]: row for row in result["reports"]}
    evaluations = {row["id"]: row for row in result["decision_evaluations"]}
    for row in evaluations.values():
        if (not UUID.fullmatch(row["id"]) or not UUID.fullmatch(row["candidate_id"])
                or not HASH.fullmatch(row["input_digest"])
                or (row["request_id"] is not None and row["request_id"] not in requests)
                or (row["run_id"] is not None and row["run_id"] not in runs)
                or (row["policy_version"] is not None and row["policy_version"] not in policies)):
            raise ValueError("decision evaluation recovery dependency mismatch")
    for row in result["policy_comparisons"]:
        evaluation, packet = evaluations.get(row["evaluation_id"]), packets.get(row["packet_id"])
        if (not UUID.fullmatch(row["id"]) or evaluation is None or packet is None
                or row["run_id"] != evaluation["run_id"] or row["run_id"] != packet["run_id"]):
            raise ValueError("policy comparison recovery dependency mismatch")
    for name in ("commands", "runs", "intelligence_runs", "intelligence_run_events",
                 "source_quota_reservations", "packets", "reports", "evaluation_publications", "cash_snapshots"):
        if any(not UUID.fullmatch(row["id"]) for row in result[name]):
            raise ValueError(f"recovery dataset {name} has malformed UUIDs")
    if any(not UUID.fullmatch(row["request_id"]) or (row["run_id"] is not None and row["run_id"] not in runs)
           or (row["response_digest"] is not None and not HASH.fullmatch(row["response_digest"]))
           for row in result["gateway_requests"]):
        raise ValueError("gateway request relationship mismatch")
    if any(row["gateway_request_id"] is not None and row["gateway_request_id"] not in requests
           for row in result["runs"]):
        raise ValueError("analysis run gateway relationship mismatch")
    if any(row["command_id"] not in commands or row["attempt_count"] < 0
           or ((row["lease_token"] is None) != (row["lease_expires_at"] is None))
           for row in result["command_acknowledgements"]):
        raise ValueError("command acknowledgement relationship mismatch")
    if any(row["id"] not in runs or row["policy_version"] not in policies
           for row in result["intelligence_runs"]):
        raise ValueError("intelligence run dependency mismatch")
    intelligence_runs = {row["id"] for row in result["intelligence_runs"]}
    discovery_providers = {
        "gdelt", "alpha_vantage", "finnhub", "yahoo", "sec_edgar", "federal_register",
        "white_house", "doe", "dod", "eia", "fred", "bls", "bea", "social",
    }
    discovery_stages = {"reference", "signals", "resolve", "enrich", "screen", "quote"}
    discovery_query_kinds = {
        "feed", "theme_search", "issuer_submissions", "filing_document", "series",
        "screener", "quote", "universe",
    }
    forbidden_discovery_fields = {
        "price", "valuation", "portfoliooverlap", "action", "authority", "execution",
        "executionallowed", "broker", "brokerage", "order", "orderid", "orderdetails",
    }

    def discovery_field_semantic(key: object) -> str:
        return re.sub(r"[^a-z0-9]", "", str(key).lower())

    def valid_discovery_json(value: object, *, max_bytes: int) -> bool:
        if len(canonical_json(value).encode()) > max_bytes:
            return False
        if isinstance(value, Mapping):
            return (not any(discovery_field_semantic(key) in forbidden_discovery_fields for key in value)
                    and all(valid_discovery_json(child, max_bytes=max_bytes) for child in value.values()))
        if isinstance(value, list):
            return all(valid_discovery_json(child, max_bytes=max_bytes) for child in value)
        return True

    def valid_discovery_cursor_result(row: Mapping[str, object]) -> bool:
        task_result = row["result"]
        if not isinstance(task_result, Mapping):
            return False
        cursor_fields = {"request_cursor", "source_cursor", "cursor_key", "theme_id", "checkpoint"}
        if not cursor_fields.intersection(task_result):
            return True
        if set(task_result) != cursor_fields or not isinstance(task_result["checkpoint"], Mapping):
            return False
        try:
            request_cursor = SourceCursor.from_mapping(task_result["request_cursor"])
            source_cursor = SourceCursor.from_mapping(task_result["source_cursor"])
        except (TypeError, ValueError):
            return False
        theme = task_result["theme_id"]
        if theme is not None and (
            not isinstance(theme, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{2,79}", theme) is None
        ):
            return False
        return (
            request_cursor.provider == row["provider"]
            and request_cursor.capability_id == row["capability_id"]
            and source_cursor.provider == row["provider"]
            and source_cursor.capability_id == row["capability_id"]
            and task_result["cursor_key"]
            == f"{row['capability_id']}:{theme or 'default'}"
        )

    manifests = {row["id"]: row for row in result["reference_manifests"]}
    if any(not UUID.fullmatch(row["id"]) or row["run_id"] not in intelligence_runs
           or not re.fullmatch(r"[a-z0-9][a-z0-9:._-]{0,127}", row["reference_version"])
           or not 1 <= row["revision"] <= 10000
           or not 1 <= row["capability_version"] <= 10000
           or not 1 <= row["taxonomy_version"] <= 10000
           or not HASH.fullmatch(row["source_hash"]) or not HASH.fullmatch(row["content_hash"])
           or not valid_discovery_json(row["manifest"], max_bytes=65536)
           for row in manifests.values()):
        raise ValueError("discovery manifest dependency mismatch or invalid content")
    security_revisions = {row["id"]: row for row in result["security_reference_revisions"]}
    if any(not UUID.fullmatch(row["id"]) or row["manifest_id"] not in manifests
           or row["run_id"] not in intelligence_runs
           or manifests.get(row["manifest_id"], {}).get("run_id") != row["run_id"]
           or not 1 <= row["revision"] <= 10000
           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", row["security_id"])
           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", row["entity_id"])
           or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", row["ticker"])
           or row["instrument_type"] not in {"COMMON_STOCK", "ADR", "ETF", "PREFERRED", "WARRANT", "OTC_COMMON", "OTHER"}
           or not 1 <= len(row["source_ids"]) <= 16 or len(set(row["source_ids"])) != len(row["source_ids"])
           or len(row["aliases"]) > 32 or len(row["exclusion_reasons"]) > 16
           or not HASH.fullmatch(row["content_hash"])
           or not valid_discovery_json(row["exclusion_reasons"], max_bytes=4096)
           or not valid_discovery_json(row["aliases"], max_bytes=4096)
           or not valid_discovery_json(row["source_ids"], max_bytes=4096)
           or row["semantic_encoding_version"] not in {1, 2}
           or row["semantic_encoding_version"] != manifests[row["manifest_id"]]["manifest"].get("format_version", 1)
           or (row["semantic_encoding_version"] == 1) != (row["issuer_names"] is None)
           or (row["issuer_names"] is not None
               and (set(row["issuer_names"]) != {"canonical_name", "observed_names", "former_names"}
                    or not valid_discovery_json(row["issuer_names"], max_bytes=32768)))
           for row in security_revisions.values()):
        raise ValueError("discovery security dependency mismatch or invalid content")
    issuer_names_by_entity: dict[tuple[str, str], object] = {}
    for row in security_revisions.values():
        if row["semantic_encoding_version"] != 2:
            continue
        key = (row["manifest_id"], row["entity_id"])
        prior = issuer_names_by_entity.setdefault(key, row["issuer_names"])
        if prior != row["issuer_names"]:
            raise ValueError("discovery security issuer names mismatch")
    seals = {row["manifest_id"]: row for row in result["reference_finalization_seals"]}
    receipts_by_manifest: dict[str, list[dict[str, object]]] = {}
    for row in result["reference_chunk_receipts"]:
        receipts_by_manifest.setdefault(row["manifest_id"], []).append(row)
        if (not UUID.fullmatch(row["manifest_id"]) or row["run_id"] not in intelligence_runs
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", row["capability_id"])
                or not -1 <= row["chunk_index"] <= 511
                or not 1 <= row["chunk_count"] <= 512
                or not 0 <= row["entry_count"] <= 200
                or (manifests.get(row["manifest_id"], {}).get("manifest", {}).get("format_version", 1) == 2
                    and row["chunk_index"] >= 0 and row["entry_count"] > 88)
                or not HASH.fullmatch(row["chunk_hash"])
                or not valid_discovery_json(row["payload"], max_bytes=196608)):
            raise ValueError("discovery reference transfer dependency mismatch")
    memberships_by_manifest: dict[str, list[dict[str, object]]] = {}
    for row in result["reference_snapshot_memberships"]:
        memberships_by_manifest.setdefault(row["manifest_id"], []).append(row)
        revision = security_revisions.get(row["security_revision_id"])
        if (row["manifest_id"] not in manifests or revision is None
                or row["security_id"] != revision["security_id"]
                or not 0 <= row["ordinal"] <= 14999):
            raise ValueError("discovery reference membership dependency mismatch")
    for manifest_id, seal in seals.items():
        predecessor = seals.get(seal["predecessor_manifest_id"]) if seal["predecessor_manifest_id"] else None
        receipts = receipts_by_manifest.get(manifest_id, [])
        chunks = sorted((row for row in receipts if row["chunk_index"] >= 0), key=lambda row: row["chunk_index"])
        begin = next((row for row in receipts if row["chunk_index"] == -1), None)
        members = memberships_by_manifest.get(manifest_id, [])
        if (manifest_id not in manifests or seal["run_id"] not in intelligence_runs
                or manifests[manifest_id]["run_id"] != seal["run_id"]
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", seal["capability_id"])
                or not 1 <= seal["chunk_count"] <= 512
                or not 1 <= seal["security_count"] <= 15000
                or not HASH.fullmatch(seal["root_hash"])
                or begin is None or begin["run_id"] != seal["run_id"]
                or begin["capability_id"] != seal["capability_id"]
                or begin["chunk_hash"] != seal["root_hash"]
                or [row["chunk_index"] for row in chunks] != list(range(seal["chunk_count"]))
                or any(row["chunk_count"] != seal["chunk_count"] for row in receipts)
                or sum(row["entry_count"] for row in chunks) != seal["security_count"]
                or hashlib.sha256("".join(row["chunk_hash"] for row in chunks).encode()).hexdigest() != seal["root_hash"]
                or len(members) != seal["security_count"]
                or sorted(row["ordinal"] for row in members) != list(range(seal["security_count"]))
                or (seal["predecessor_manifest_id"] is not None
                    and (predecessor is None or predecessor["capability_id"] != seal["capability_id"]))):
            raise ValueError("discovery reference finalization dependency mismatch")
    _validate_reference_semantic_lineage(
        manifests,
        security_revisions,
        receipts_by_manifest,
        memberships_by_manifest,
        seals,
    )
    predecessor_pins = {
        (row["run_id"], row["capability_id"]): row
        for row in result["reference_predecessor_pins"]
    }
    for row in result["reference_run_bindings"]:
        seal = seals.get(row["manifest_id"]) if row["manifest_id"] else None
        unavailable = row["reference_status"] == "reference_unavailable"
        predecessor = predecessor_pins.get((row["run_id"], row["capability_id"]))
        request = row["request_payload"]
        request_status = request.get("reference_status") if isinstance(request, dict) else None
        request_manifest = request.get("manifest_id") if isinstance(request, dict) else None
        if (row["run_id"] not in intelligence_runs
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", row["capability_id"])
                or row["reference_status"] not in {"healthy", "reference_stale", "reference_unavailable"}
                or not valid_discovery_json(request, max_bytes=196608)
                or set(request) != {"capability_id", "binding_role", "manifest_id", "reference_status", "reference_as_of"}
                or request.get("capability_id") != row["capability_id"]
                or request.get("binding_role") != "current"
                or not isinstance(request.get("reference_as_of"), str)
                or request_status not in {"healthy", "reference_stale", "reference_unavailable"}
                or (row["reference_status"] == "healthy"
                    and (request_status != "healthy" or request_manifest != row["manifest_id"]))
                or (row["reference_status"] == "reference_stale"
                    and (request_status != "reference_stale"
                         or request_manifest not in {None, row["manifest_id"]}))
                or (unavailable and (request_status not in {"reference_stale", "reference_unavailable"}
                                     or request_manifest is not None))
                or unavailable != (row["manifest_id"] is None)
                or unavailable != (row["source_retrieved_at"] is None)
                or unavailable != (row["reference_age_seconds"] is None)
                or (row["reference_status"] in {"reference_stale", "reference_unavailable"}
                    and (predecessor is None
                         or predecessor["manifest_id"] != row["manifest_id"]
                         or predecessor["reference_status"] != row["reference_status"]))
                or (seal is not None and seal["capability_id"] != row["capability_id"])
                or (not unavailable and (seal is None or row["reference_age_seconds"] < 0))):
            raise ValueError("discovery reference binding dependency mismatch")
    for row in predecessor_pins.values():
        seal = seals.get(row["manifest_id"]) if row["manifest_id"] else None
        unavailable = row["reference_status"] == "reference_unavailable"
        request = row["request_payload"]
        if (row["run_id"] not in intelligence_runs
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", row["capability_id"])
                or row["reference_status"] not in {"reference_stale", "reference_unavailable"}
                or not valid_discovery_json(request, max_bytes=196608)
                or set(request) != {"capability_id", "binding_role", "manifest_id", "reference_status", "reference_as_of"}
                or request.get("capability_id") != row["capability_id"]
                or request.get("binding_role") != "predecessor"
                or request.get("manifest_id") is not None
                or request.get("reference_status") != "reference_stale"
                or unavailable != (row["manifest_id"] is None)
                or unavailable != (row["source_retrieved_at"] is None)
                or unavailable != (row["reference_age_seconds"] is None)
                or (seal is not None
                    and (seal["capability_id"] != row["capability_id"]
                         or seal["run_id"] == row["run_id"]))
                or (not unavailable and (seal is None or row["reference_age_seconds"] < 0))):
            raise ValueError("discovery reference predecessor dependency mismatch")
    _validate_reference_cross_ledger_lineage(
        receipts_by_manifest,
        seals,
        predecessor_pins,
    )
    transfer_operations = {
        "begin_discovery_reference", "record_discovery_reference_chunk",
        "finalize_discovery_reference", "pin_discovery_reference",
        "read_discovery_reference",
    }
    transfer_by_run: dict[str, list[dict]] = {}
    for row in result["reference_transfer_requests"]:
        envelope = {
            "dry_run": False, "operation": row["operation"],
            "payload": row["request_payload"], "request_id": row["request_id"],
            "run_id": row["run_id"], "schema_version": 1,
        }
        encoded = canonical_json(envelope).encode()
        if (row["run_id"] not in intelligence_runs
                or row["operation"] not in transfer_operations
                or not 1 <= row["encoded_bytes"] <= 262144
                or row["encoded_bytes"] != len(encoded)
                or row["request_hash"] != hashlib.sha256(encoded).hexdigest()
                or not valid_discovery_json(row["request_payload"], max_bytes=196608)):
            raise ValueError("discovery reference transfer request dependency mismatch")
        transfer_by_run.setdefault(row["run_id"], []).append(row)
    if any(len(rows) > 384 or sum(row["encoded_bytes"] for row in rows) > 50331648
           for rows in transfer_by_run.values()):
        raise ValueError("discovery reference transfer request dependency mismatch")
    requests_by_transfer_id = {
        row["request_id"]: row for row in result["reference_transfer_requests"]
    }
    response_by_run: dict[str, list[dict]] = {}
    for row in result["reference_transfer_responses"]:
        request = requests_by_transfer_id.get(row["request_id"])
        if request is None or row["run_id"] != request["run_id"] \
                or not 1 <= row["encoded_bytes"] <= 196608 \
                or not HASH.fullmatch(row["response_hash"]):
            raise ValueError("discovery reference transfer response dependency mismatch")
        response_by_run.setdefault(row["run_id"], []).append(row)
    if any(sum(row["encoded_bytes"] for row in rows) > 67108864
           for rows in response_by_run.values()):
        raise ValueError("discovery reference transfer response dependency mismatch")
    discovery_tasks = {row["id"]: row for row in result["discovery_stage_tasks"]}
    if any(not UUID.fullmatch(row["id"]) or row["run_id"] not in intelligence_runs
           or row["stage"] not in discovery_stages or row["provider"] not in discovery_providers
           or row["query_kind"] not in discovery_query_kinds
           or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", row["capability_id"])
           or not HASH.fullmatch(row["query_hash"])
           or not 0 <= row["attempt_count"] <= 10 or not 0 <= row["request_budget"] <= 100
           or row["state"] not in {"planned", "attempting", "succeeded", "failed", "deferred", "uncertain"}
           or len(row["dependency_ids"]) > 32 or len(set(row["dependency_ids"])) != len(row["dependency_ids"])
           or any(not isinstance(dependency, str) or not UUID.fullmatch(dependency)
                  for dependency in row["dependency_ids"])
           or not valid_discovery_json(row["requested_window"], max_bytes=2048)
           or not valid_discovery_json(row["result"], max_bytes=65536)
           or not valid_discovery_cursor_result(row)
           for row in discovery_tasks.values()):
        raise ValueError("discovery task dependency mismatch or invalid content")
    for row in discovery_tasks.values():
        for dependency_id in row["dependency_ids"]:
            dependency = discovery_tasks.get(dependency_id)
            if (dependency is None or dependency["run_id"] != row["run_id"]
                    or dependency["state"] != "succeeded"
                    or dependency["created_at"] >= row["created_at"]):
                raise ValueError("discovery task dependency mismatch")
    theme_episodes = {row["id"]: row for row in result["theme_episode_revisions"]}
    if any(not UUID.fullmatch(row["id"]) or row["run_id"] not in intelligence_runs
           or row["task_id"] not in discovery_tasks
           or discovery_tasks.get(row["task_id"], {}).get("run_id") != row["run_id"]
           or discovery_tasks.get(row["task_id"], {}).get("stage") != "signals"
           or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", row["theme_id"])
           or not 1 <= row["revision"] <= 10000 or not 1 <= len(row["source_ids"]) <= 64
           or len(set(row["source_ids"])) != len(row["source_ids"])
           or not HASH.fullmatch(row["content_hash"])
           or not valid_discovery_json(row["episode"], max_bytes=32768)
           or not valid_discovery_json(row["source_ids"], max_bytes=8192)
           for row in theme_episodes.values()):
        raise ValueError("discovery theme dependency mismatch or invalid content")
    exposure_facts = {row["id"]: row for row in result["exposure_facts"]}
    if any(not UUID.fullmatch(row["id"]) or row["run_id"] not in intelligence_runs
           or row["task_id"] not in discovery_tasks
           or discovery_tasks.get(row["task_id"], {}).get("run_id") != row["run_id"]
           or discovery_tasks.get(row["task_id"], {}).get("stage") != "enrich"
           or row["security_revision_id"] not in security_revisions
           or security_revisions.get(row["security_revision_id"], {}).get("run_id") != row["run_id"]
           or (row["theme_episode_revision_id"] is not None
               and (row["theme_episode_revision_id"] not in theme_episodes
                    or theme_episodes[row["theme_episode_revision_id"]]["run_id"] != row["run_id"]))
           or row["exposure_kind"] not in {"filing", "contract", "backlog", "revenue", "capacity", "official_fund", "supply_chain", "customer", "segment"}
           or not 1 <= len(row["source_ids"]) <= 64 or len(set(row["source_ids"])) != len(row["source_ids"])
           or not HASH.fullmatch(row["content_hash"])
           or not valid_discovery_json(row["fact"], max_bytes=32768)
           or not valid_discovery_json(row["source_ids"], max_bytes=8192)
           for row in exposure_facts.values()):
        raise ValueError("discovery exposure dependency mismatch or invalid content")
    nominations = {row["id"]: row for row in result["research_nominations"]}
    if any(not UUID.fullmatch(row["id"]) or row["run_id"] not in intelligence_runs
           or row["task_id"] not in discovery_tasks
           or discovery_tasks.get(row["task_id"], {}).get("run_id") != row["run_id"]
           or discovery_tasks.get(row["task_id"], {}).get("stage") != "screen"
           or row["security_revision_id"] not in security_revisions
           or security_revisions.get(row["security_revision_id"], {}).get("run_id") != row["run_id"]
           or (row["theme_episode_revision_id"] is not None
               and (row["theme_episode_revision_id"] not in theme_episodes
                    or theme_episodes[row["theme_episode_revision_id"]]["run_id"] != row["run_id"]))
           or not 1 <= len(row["exposure_fact_ids"]) <= 32
           or len(set(row["exposure_fact_ids"])) != len(row["exposure_fact_ids"])
           or any(fact_id not in exposure_facts or exposure_facts[fact_id]["run_id"] != row["run_id"]
                  for fact_id in row["exposure_fact_ids"])
           or row["state"] not in {"nominated", "researching", "accepted", "rejected", "deferred"}
           or not valid_discovery_json(row["rationale"], max_bytes=16384)
           for row in nominations.values()):
        raise ValueError("discovery nomination dependency mismatch or invalid content")
    intelligence_events = {row["id"]: row for row in result["intelligence_run_events"]}
    if (any(row["run_id"] not in intelligence_runs or row["status"] not in {"started", "completed", "failed"}
            for row in intelligence_events.values())
            or any(sum(row["run_id"] == run_id and row["status"] == "started"
                       for row in intelligence_events.values()) != 1 for run_id in intelligence_runs)
            or any(sum(row["run_id"] == run_id and row["status"] in {"completed", "failed"}
                       for row in intelligence_events.values()) > 1 for run_id in intelligence_runs)):
        raise ValueError("intelligence run event dependency mismatch")
    if any(row["run_id"] not in intelligence_runs or row["reserved_requests"] < 0
           for row in result["source_quota_reservations"]):
        raise ValueError("source quota reservation dependency mismatch")
    reservation_ids = {row["id"] for row in result["source_quota_reservations"]}
    reservations = {row["id"]: row for row in result["source_quota_reservations"]}
    for name in ("collection_checkpoints", "collection_checkpoint_history"):
        if any(row["run_id"] not in intelligence_runs
               or not UUID.fullmatch(row["source_receipt_id"])
               or row["payload"].get("receipt", {}).get("reservation_id") not in reservation_ids
               for row in result[name]):
            raise ValueError("collection checkpoint dependency mismatch")

    durable_checkpoints = {
        (row["run_id"], row["cache_key"], row["source_receipt_id"]): row
        for name in ("collection_checkpoints", "collection_checkpoint_history")
        for row in result[name]
    }
    receipt_fields = {
        "provider", "reservation_id", "status", "cache_key", "requested_window",
        "requested_limit", "retrieved_at", "observed_at", "expires_at", "request_cost",
        "upstream_remaining", "returned_count", "accepted_count", "duplicate_count",
        "dropped_count", "response_hash", "error_code", "source_receipt_id",
        "cache_predecessor_receipt_id",
    }

    def valid_cursor_transition(row: Mapping[str, object]) -> bool:
        task_result = row["result"]
        if not isinstance(task_result, Mapping) or "request_cursor" not in task_result:
            return True
        checkpoint = task_result.get("checkpoint")
        if not isinstance(checkpoint, Mapping) or set(checkpoint) != {"cache_key", "receipt"}:
            return False
        receipt = checkpoint.get("receipt")
        if not isinstance(receipt, Mapping) or set(receipt) != receipt_fields | {"metadata"}:
            return False
        metadata = receipt.get("metadata")
        if not isinstance(metadata, Mapping) or not {
            "capability_id", "coverage_status", "cursor_start", "cursor_end",
            "overlap_seconds", "page", "truncated", "backlog_remaining", "exhausted",
        }.issubset(metadata):
            return False
        try:
            request_cursor = SourceCursor.from_mapping(task_result["request_cursor"])
            source_cursor = SourceCursor.from_mapping(task_result["source_cursor"])
            requested_window = row["requested_window"]
            receipt_window = receipt["requested_window"]
            if not isinstance(requested_window, Mapping) or set(requested_window) != {"start", "end"}:
                return False
            if not isinstance(receipt_window, Mapping) or set(receipt_window) != {"start", "end"}:
                return False
            start = parse_time(requested_window["start"])
            end = parse_time(requested_window["end"])
            if (
                parse_time(receipt_window["start"]) != start
                or parse_time(receipt_window["end"]) != end
                or parse_time(metadata["cursor_start"]) != start
                or parse_time(metadata["cursor_end"]) != end
            ):
                return False
            if any(isinstance(metadata[key], bool) or not isinstance(metadata[key], int)
                   for key in ("overlap_seconds", "page")):
                return False
            if any(not isinstance(metadata[key], bool)
                   for key in ("truncated", "backlog_remaining", "exhausted")):
                return False
            if metadata["capability_id"] != row["capability_id"] \
                    or metadata["page"] != request_cursor.page:
                return False
            cache_key = checkpoint["cache_key"]
            source_receipt_id = receipt["source_receipt_id"]
            if (
                not isinstance(cache_key, str) or HASH.fullmatch(cache_key) is None
                or receipt["cache_key"] != cache_key
                or not isinstance(source_receipt_id, str)
                or UUID.fullmatch(source_receipt_id) is None
                or receipt["provider"] != row["provider"]
            ):
                return False
            reservation = reservations.get(receipt["reservation_id"])
            if reservation is None or reservation["run_id"] != row["run_id"] \
                    or reservation["provider"] != row["provider"]:
                return False

            status = receipt["status"]
            coverage = metadata["coverage_status"]
            if status in {"succeeded", "cache_hit"}:
                cursor_status = status
                expected_state = "succeeded"
            elif status == "configuration_missing":
                cursor_status, expected_state = "configuration_missing", "deferred"
            elif status == "quota_blocked":
                cursor_status, expected_state = "quota_blocked", "deferred"
            elif status == "failed" and coverage == "unsupported":
                cursor_status, expected_state = "unsupported", "deferred"
            elif status == "failed":
                cursor_status, expected_state = "failed", "failed"
            else:
                return False

            uncertain = row["state"] == "uncertain"
            if not uncertain and row["state"] != expected_state:
                return False
            successful = cursor_status in {"succeeded", "cache_hit"}
            exhausted = metadata["exhausted"]
            truncated = metadata["truncated"]
            backlog_remaining = metadata["backlog_remaining"]
            if exhausted is not (successful and not truncated and not backlog_remaining):
                return False
            backlog_token = metadata.get("backlog_token")
            if backlog_token is not None and (
                not isinstance(backlog_token, str) or not backlog_token or not backlog_remaining
            ):
                return False

            durable = durable_checkpoints.get((row["run_id"], cache_key, source_receipt_id))
            request_cost = receipt["request_cost"]
            if isinstance(request_cost, bool) or not isinstance(request_cost, int) \
                    or not 0 <= request_cost <= row["request_budget"]:
                return False
            items: object = []
            if request_cost > 0:
                if durable is None or durable["cache_key"] != cache_key:
                    return False
                durable_payload = durable["payload"]
                if not isinstance(durable_payload, Mapping) \
                        or set(durable_payload) != {"receipt", "items"}:
                    return False
                durable_receipt = durable_payload["receipt"]
                items = durable_payload["items"]
                if not isinstance(durable_receipt, Mapping) or set(durable_receipt) != receipt_fields \
                        or not isinstance(items, list):
                    return False
                for field in receipt_fields - {"requested_window"}:
                    if receipt[field] != durable_receipt[field]:
                        return False
                if durable_receipt["cache_key"] != cache_key \
                        or durable_receipt["source_receipt_id"] != durable["source_receipt_id"]:
                    return False
            elif durable is not None:
                return False

            if not isinstance(items, list) or any(
                not isinstance(item, Mapping) or item.get("provider") != row["provider"]
                or not isinstance(item.get("content_hash"), str)
                or HASH.fullmatch(item["content_hash"]) is None
                for item in items
            ):
                return False
            if receipt["accepted_count"] != len(items) \
                    or receipt["returned_count"] < receipt["accepted_count"]:
                return False
            if uncertain:
                return (
                    source_cursor == request_cursor
                    and metadata.get("cursor_outcome_unavailable") is True
                    and metadata.get("coverage_gap") is True
                )
            accepted_ids = tuple(
                item.get("upstream_item_id") or item["content_hash"] for item in items
            )
            expected_cursor = update_cursor(
                request_cursor,
                CollectionPage(
                    window=CollectionWindow(
                        start=start, end=end, overlap_seconds=metadata["overlap_seconds"],
                        backlog_token=request_cursor.backlog_token,
                    ),
                    status=cursor_status,
                    exhausted=exhausted,
                    truncated=truncated or (successful and backlog_remaining),
                    backlog_token=backlog_token,
                    accepted_item_ids=accepted_ids,
                    next_retry_phase=metadata.get("next_retry_phase"),
                ),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return expected_cursor == source_cursor

    if any(not valid_cursor_transition(row) for row in discovery_tasks.values()):
        raise ValueError("discovery task dependency mismatch or invalid content")
    if any(not UUID.fullmatch(row["completion_id"]) or row["run_id"] not in intelligence_runs
           or row["completion_id"] not in intelligence_events
           or intelligence_events[row["completion_id"]]["run_id"] != row["run_id"]
           or intelligence_events[row["completion_id"]]["status"] != "completed"
           for row in result["collection_completions"]):
        raise ValueError("collection completion dependency mismatch")
    if sum(row["active"] for row in result["policies"]) != 1:
        raise ValueError("recovery requires exactly one active policy")
    for row in packets.values():
        if (row["run_id"] not in runs or row["policy_version"] not in policies or row["status"] != "completed"
                or row["candidate_count"] < 0 or row["evidence_count"] < 0
                or sha256(canonical_json(row["packet"]).encode()) != row["packet_hash"]):
            raise ValueError("packet content or run relationship mismatch")
    for row in reports.values():
        packet = packets.get(row["packet_id"])
        if (packet is None or row["run_id"] not in runs or packet["run_id"] != row["run_id"]
                or sha256(canonical_json(row["report"]).encode()) != row["report_hash"]
                or sha256(row["rendered_text"].encode()) != row["rendered_hash"]
                or not HASH.fullmatch(row["idempotency_key"])
                or ("packet_hash" in row["report"] and row["report"]["packet_hash"] != packet["packet_hash"])):
            raise ValueError("report content or packet/run relationship mismatch")
    for row in result["publications"]:
        if (row["report_id"] not in reports or not HASH.fullmatch(row["idempotency_key"])
                or row["idempotency_key"] != reports[row["report_id"]]["idempotency_key"]):
            raise ValueError("publication report relationship mismatch")
        status, ids, accepted, reason = (row[k] for k in ("status", "telegram_message_ids", "telegram_accepted_at", "suppression_reason"))
        if status not in {"pending", "delivered", "failed", "uncertain", "suppressed"}:
            raise ValueError("invalid publication state")
        if status == "delivered":
            if not ids or any(type(value) is not int or value <= 0 for value in ids) or not accepted or reason is not None:
                raise ValueError("original Telegram delivery receipt is incomplete")
        elif ids != [] or accepted is not None or (status == "suppressed" and (not reason or not reason.strip())) or (status != "suppressed" and reason is not None):
            raise ValueError("publication suppression receipt is incomplete")
        if row["attempt_count"] < 0 or ((row["lease_token"] is None) != (row["lease_expires_at"] is None)):
            raise ValueError("publication delivery state is incomplete")
    if not {row["report_id"] for row in result["publications"]}.issubset(reports):
        raise ValueError("recovery publication has no report")
    if any(not row["version"].isdigit() or not row["statements"]
           or not HASH.fullmatch(row["sha256"]) or not all(isinstance(statement, str) and statement.strip() for statement in row["statements"])
           or sha256("\n".join(row["statements"]).encode()) != row["sha256"]
           for row in result["schema_version"]):
        raise ValueError("schema version hash is invalid")
    from scripts.deploy_owner_dashboard_api import (
        RECONCILIATION_BASELINE_PATH, RECONCILIATION_BASELINE_VERSION,
        migration_statements_sha256, reconciliation_baseline_manifest,
    )
    private_versions = {}
    baseline_row = None
    for row in result["release_migration_ledger"]:
        match = re.fullmatch(r"sql/migrations/(\d{8}(?:\d{4})?)_[a-z0-9][a-z0-9_]*\.sql", row["path"])
        is_baseline = row["path"] == RECONCILIATION_BASELINE_PATH and row["version"] == RECONCILIATION_BASELINE_VERSION
        if ((not is_baseline and (match is None or match.group(1) != row["version"])) or not HASH.fullmatch(row["sha256"])
                or row["version"] in private_versions):
            raise ValueError("release migration ledger identity is invalid")
        if is_baseline:
            baseline_row = row
        try:
            applied_at = datetime.fromisoformat(row["applied_at"].replace("Z", "+00:00"))
            if applied_at.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            raise ValueError("release migration ledger applied_at is invalid") from None
        private_versions[row["version"]] = row["sha256"]
    # The native export hash binds its exact stored statements[], while the
    # release ledger uses the reconciler's canonical statement-array identity.
    native_baseline = [row for row in result["schema_version"] if row["version"] == RECONCILIATION_BASELINE_VERSION]
    if baseline_row is not None or native_baseline:
        try:
            expected = reconciliation_baseline_manifest()
        except (RuntimeError, OSError) as error:
            raise ValueError("reconciliation migration baseline source is invalid") from error
        if (baseline_row is None or baseline_row["sha256"] != expected["sha256"]
                or len(result["schema_version"]) != 1 or len(native_baseline) != 1
                or migration_statements_sha256(native_baseline[0]["statements"]) != expected["sha256"]
                or any(row["path"] != RECONCILIATION_BASELINE_PATH
                       and row["version"] <= RECONCILIATION_BASELINE_VERSION
                       for row in result["release_migration_ledger"])):
            raise ValueError("reconciliation migration baseline pair is invalid")
    if private_versions and any(private_versions.get(row["version"]) != migration_statements_sha256(row["statements"]) for row in result["schema_version"]):
        raise ValueError("native/private migration ledgers diverge")
    for row in result["evaluation_publications"]:
        if (row["idempotency_key"] not in requests or (row["run_id"] is not None and row["run_id"] not in runs)
                or not HASH.fullmatch(row["rendered_hash"]) or row["attempt_count"] < 0
                or (row["status"] == "sending" and (row["lease_token"] is None or row["sending_started_at"] is None))
                or (row["status"] != "sending" and row["lease_token"] is not None)):
            raise ValueError("evaluation publication delivery state is incomplete")
    if len(result["cash_ledger_state"]) != 1 or result["cash_ledger_state"][0]["singleton"] is not True:
        raise ValueError("cash ledger state is incomplete")
    try:
        ledger_revision = int(result["cash_ledger_state"][0]["revision"])
        snapshot_revisions = [int(row["ledger_watermark"]) for row in result["cash_snapshots"]]
    except ValueError:
        raise ValueError("cash snapshot ledger relationship mismatch") from None
    if ledger_revision < 0 or any(revision < 0 or revision > ledger_revision for revision in snapshot_revisions):
        raise ValueError("cash snapshot ledger relationship mismatch")
    requests_by_id = {row["request_id"]: row for row in result["gateway_requests"]}
    for row in result["report_origins"]:
        request = requests_by_id.get(row["request_id"])
        packet = packets.get(row["requested_packet_id"])
        report = reports.get(row["requested_report_id"])
        if (request is None or request["operation"] != "record_report" or request["run_id"] is not None
                or row["run_id"] not in runs or packet is None or packet["run_id"] != row["run_id"]
                or not HASH.fullmatch(row["requested_idempotency_key"])
                or not HASH.fullmatch(row["requested_report_hash"])
                or (report is not None and (report["run_id"] != row["run_id"]
                    or report["packet_id"] != row["requested_packet_id"]
                    or report["idempotency_key"] != row["requested_idempotency_key"]
                    or report["report_hash"] != row["requested_report_hash"]))):
            raise ValueError("report origin dependency mismatch")
    if any(not UUID.fullmatch(row["run_id"]) or not UUID.fullmatch(row["evaluation_request_id"])
           or row["run_id"] not in runs or row["evaluation_request_id"] not in requests
           or requests_by_id[row["evaluation_request_id"]]["run_id"] != row["run_id"]
           or row["outcome"] not in {"no_trigger", "not_actionable"}
           for row in result["run_terminal_outcomes"]):
        raise ValueError("terminal outcome relationship mismatch")
    return result


def relationships(records: Mapping[str, list]) -> dict[str, list]:
    return {
        "packet_run": sorted([[row["id"], row["run_id"]] for row in records["packets"]]),
        "report_packet_run": sorted([[row["id"], row["packet_id"], row["run_id"]] for row in records["reports"]]),
        "publication_report": sorted([[row["idempotency_key"], row["report_id"]] for row in records["publications"]]),
        "command_acknowledgement": sorted([[row["command_id"], row["telegram_update_id"]] for row in records["command_acknowledgements"]]),
        "intelligence_analysis_run": sorted([[row["id"], row["policy_version"]] for row in records["intelligence_runs"]]),
        "discovery_manifest_run": sorted([[row["id"], row["run_id"], row["content_hash"]] for row in records["reference_manifests"]]),
        "discovery_security_manifest_run": sorted([[row["id"], row["manifest_id"], row["run_id"]] for row in records["security_reference_revisions"]]),
        "discovery_reference_chunks": sorted([[row["manifest_id"], row["chunk_index"], row["chunk_hash"]] for row in records["reference_chunk_receipts"]]),
        "discovery_reference_memberships": sorted([[row["manifest_id"], row["security_revision_id"], row["security_id"], row["ordinal"]] for row in records["reference_snapshot_memberships"]]),
        "discovery_reference_finalization": sorted([[row["manifest_id"], row["run_id"], row["predecessor_manifest_id"], row["root_hash"]] for row in records["reference_finalization_seals"]]),
        "discovery_reference_bindings": sorted([[row["run_id"], row["capability_id"], row["manifest_id"], row["reference_status"]] for row in records["reference_run_bindings"]]),
        "discovery_reference_predecessor_pins": sorted([[row["run_id"], row["capability_id"], row["manifest_id"], row["reference_status"]] for row in records["reference_predecessor_pins"]]),
        "discovery_reference_transfer_requests": sorted([[row["request_id"], row["run_id"], row["operation"], row["encoded_bytes"], row["request_hash"]] for row in records["reference_transfer_requests"]]),
        "discovery_reference_transfer_responses": sorted([[row["request_id"], row["run_id"], row["encoded_bytes"], row["response_hash"]] for row in records["reference_transfer_responses"]]),
        "discovery_task_run_dependencies": sorted([[row["id"], row["run_id"], row["dependency_ids"]] for row in records["discovery_stage_tasks"]]),
        "discovery_theme_task_run": sorted([[row["id"], row["task_id"], row["run_id"]] for row in records["theme_episode_revisions"]]),
        "discovery_exposure_lineage": sorted([[row["id"], row["task_id"], row["security_revision_id"], row["theme_episode_revision_id"]] for row in records["exposure_facts"]]),
        "discovery_nomination_lineage": sorted([[row["id"], row["task_id"], row["security_revision_id"], row["theme_episode_revision_id"], row["exposure_fact_ids"]] for row in records["research_nominations"]]),
        "intelligence_event_run": sorted([[row["id"], row["run_id"], row["status"]] for row in records["intelligence_run_events"]]),
        "quota_reservation_run": sorted([[row["id"], row["run_id"], row["provider"]] for row in records["source_quota_reservations"]]),
        "checkpoint_run": sorted([[row["run_id"], row["cache_key"], row["source_receipt_id"]] for row in records["collection_checkpoints"]]),
        "checkpoint_history_run": sorted([[row["run_id"], row["cache_key"], row["source_receipt_id"]] for row in records["collection_checkpoint_history"]]),
        "collection_completion_run": sorted([[row["completion_id"], row["run_id"]] for row in records["collection_completions"]]),
        "report_origin_request": sorted([[row["request_id"], row["run_id"], row["requested_report_id"]] for row in records["report_origins"]]),
        "evaluation_publication_request": sorted([[row["id"], row["idempotency_key"], row["run_id"]] for row in records["evaluation_publications"]]),
        "terminal_outcome_request": sorted([[row["run_id"], row["evaluation_request_id"]] for row in records["run_terminal_outcomes"]]),
    }


def _command(template: str, input_path: Path, output_path: Path, *, allow_failure: bool = False) -> bool:
    if not template or "{input}" not in template or "{output}" not in template:
        raise ValueError("encryption and decryption commands require {input} and {output}")
    try:
        args = [part.replace("{input}", str(input_path)).replace("{output}", str(output_path)) for part in shlex.split(template)]
        result = subprocess.run(args, check=False, capture_output=True, timeout=60)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("local encryption/decryption command failed") from error
    if result.returncode and not allow_failure:
        raise RuntimeError("local encryption/decryption command failed")
    return result.returncode == 0


def exact_file(path: Path, *, exists: bool = True) -> Path:
    if not path.is_absolute() or ".." in path.parts or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("recovery paths must be exact absolute paths without symlinks or traversal")
    if exists and (not path.is_file() or path.stat().st_size > MAX_PAYLOAD_BYTES):
        raise RuntimeError("recovery artifact is unavailable or exceeds the size limit")
    return path


def reject_plaintext(path: Path) -> None:
    exact_file(path)
    raw = path.read_bytes()
    if not raw or tarfile.is_tarfile(path):
        raise RuntimeError("encryption output is readable as plaintext/archive")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    try:
        envelope = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        envelope = b""
    if (raw == base64.urlsafe_b64encode(envelope)
            and len(envelope) >= 73 and envelope[0] == 0x80
            and (len(envelope) - 57) % 16 == 0):
        return
    raise RuntimeError("encryption output is readable as plaintext")


def read_payload(path: Path) -> tuple[dict, dict]:
    expected = {"payload/manifest.json", *(f"payload/data/{name}.ndjson" for name in REQUIRED_RECOVERY_RECORDS)}
    try:
        exact_file(path)
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            if len(members) != len(expected) or {member.name for member in members} != expected or any(not member.isfile() for member in members):
                raise ValueError("unsafe archive paths or members")
            if sum(member.size for member in members) > MAX_PAYLOAD_BYTES:
                raise ValueError("oversized archive")
            # Never extract archive paths to disk.
            files = {member.name: archive.extractfile(member).read() for member in members}
        manifest = json.loads(files["payload/manifest.json"])
        core = {key: value for key, value in manifest.items() if key != "root_hash"}
        if (set(core) != {"format", "record_sets", "files", "production_identity", "counts", "relationships", "secrets_included"}
                or core["format"] != "stocks-agent-recovery-v5" or core["record_sets"] != list(REQUIRED_RECOVERY_RECORDS)
                or core["secrets_included"] is not False or manifest["root_hash"] != sha256(canonical_json(core).encode())
                or set(core["files"]) != set(REQUIRED_RECOVERY_RECORDS)):
            raise ValueError("manifest root hash or fields invalid")
        records = {}
        for name in REQUIRED_RECOVERY_RECORDS:
            entry = core["files"][name]
            raw = files[f"payload/data/{name}.ndjson"]
            if set(entry) != {"path", "sha256", "records"} or entry["path"] != f"data/{name}.ndjson" or sha256(raw) != entry["sha256"]:
                raise ValueError("manifest file hash or path invalid")
            records[name] = [json.loads(line) for line in raw.splitlines()]
        normalized = _validated_records(records)
        counts = {name: len(rows) for name, rows in normalized.items()}
        if core["counts"] != counts or core["relationships"] != relationships(normalized):
            raise ValueError("manifest counts or relationships invalid")
        for name, rows in normalized.items():
            raw = "".join(canonical_json(row) + "\n" for row in rows).encode()
            if files[f"payload/data/{name}.ndjson"] != raw or core["files"][name]["records"] != len(rows):
                raise ValueError("noncanonical recovery dataset")
        return manifest, normalized
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError) as error:
        raise RuntimeError("encrypted recovery payload or root hash is invalid") from error


def decrypt_verified(artifact: Path, command: str, temporary: Path) -> tuple[dict, dict]:
    reject_plaintext(artifact)
    plain = temporary / "verified.tar"
    _command(command, artifact, plain)
    payload = read_payload(plain)
    altered = bytearray(artifact.read_bytes())
    altered[len(altered) // 2] ^= 1
    tampered = temporary / "tampered.enc"
    tampered.write_bytes(altered)
    if _command(command, tampered, temporary / "tampered.tar", allow_failure=True):
        raise RuntimeError("authenticated decryption self-test accepted modified ciphertext")
    return payload


def export_recovery_bundle(source: RecoveryDataSource, destination: Path, *, encrypt_command: str | None = None, decrypt_command: str | None = None) -> Path:
    if not encrypt_command or not decrypt_command:
        raise ValueError("encryption and decryption commands are required")
    identity = source_identity(source)
    destination = exact_file(destination, exists=False)
    receipt_path = destination.with_suffix(destination.suffix + ".receipt.json")
    if destination.exists() or receipt_path.exists():
        raise ValueError("recovery artifact and receipt destinations must not already exist")
    normalized = _validated_records(source.read_records())
    counts = {name: len(rows) for name, rows in normalized.items()}
    if source.counts() != counts:
        raise RuntimeError("production counts do not match the complete recovery snapshot")
    try:
        with tempfile.TemporaryDirectory(prefix="stocks-recovery-") as temporary:
            directory = Path(temporary).resolve()
            files = {name: "".join(canonical_json(row) + "\n" for row in rows).encode() for name, rows in normalized.items()}
            if sum(map(len, files.values())) > MAX_PAYLOAD_BYTES - 1024 * 1024:
                raise RuntimeError("complete recovery snapshot exceeds the payload limit")
            core = {"format": "stocks-agent-recovery-v5", "record_sets": list(REQUIRED_RECOVERY_RECORDS),
                    "files": {name: {"path": f"data/{name}.ndjson", "sha256": sha256(raw), "records": counts[name]} for name, raw in files.items()},
                    "production_identity": identity, "counts": counts, "relationships": relationships(normalized), "secrets_included": False}
            manifest = {**core, "root_hash": sha256(canonical_json(core).encode())}
            archive = directory / "payload.tar"
            with tarfile.open(archive, "w") as tar:
                members = {"payload/manifest.json": canonical_json(manifest).encode(), **{f"payload/data/{name}.ndjson": raw for name, raw in files.items()}}
                for name, raw in sorted(members.items()):
                    info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o600
                    tar.addfile(info, io.BytesIO(raw))
            _command(encrypt_command, archive, destination)
            verified, _ = decrypt_verified(destination, decrypt_command, directory)
            if verified["root_hash"] != manifest["root_hash"]:
                raise RuntimeError("encrypted recovery root hash differs from the exported snapshot")
            os.chmod(destination, 0o600)
            receipt_path.write_text(canonical_json({"format": core["format"], "root_hash": manifest["root_hash"], "artifact_sha256": sha256(destination.read_bytes())}) + "\n")
            os.chmod(receipt_path, 0o600)
        return destination
    except BaseException:
        # Exact newly created destinations only; a failed export must never leave financial plaintext behind.
        destination.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        raise


def main() -> int:
    from scripts.protected_evidence import PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--encrypt-command", required=True)
    parser.add_argument("--decrypt-command", required=True)
    args = parser.parse_args()
    with PostgresReadOnlySource(os.environ.get("RECOVERY_PRODUCTION_DATABASE_URL", ""), args.production_project_ref) as source:
        export_recovery_bundle(source, args.destination, encrypt_command=args.encrypt_command, decrypt_command=args.decrypt_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
