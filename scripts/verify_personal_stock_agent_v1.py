#!/usr/bin/env python3
"""Verify one protected deployment against local Git bytes and queried production rows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.config import load_settings
from lib.intelligence.planner import load_source_capabilities
from lib.intelligence.policy import load_intelligence_policy
from scripts.export_recovery_bundle import (
    _validate_reference_semantic_lineage,
    canonical_json,
    sha256,
)
from scripts.verify_owner_dashboard_deployment import migration_statements_sha256, normalize_migration_statements

MAX_SCHEDULED_RECEIPT_AGE_SECONDS = 7 * 24 * 60 * 60
SHA = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    required_capability_ids: tuple[str, ...] = ()
    optional_failures: tuple[str, ...] = ()


def _verify_discovery_capability(receipt: Mapping[str, object]) -> VerificationResult:
    """Verify V1-C3 from frozen plans and protected, receipt-backed stage rows."""
    require(isinstance(receipt, Mapping), "discovery capability receipt is required")
    run = one(receipt.get("run"), "capability run")
    intelligence = one(receipt.get("intelligence_runs"), "capability intelligence run")
    run_id = run.get("id")
    require(
        isinstance(run_id, str) and UUID.fullmatch(run_id) is not None
        and intelligence.get("id") == run_id,
        "discovery capability run identity is invalid",
    )

    packet_row = one(receipt.get("packets"), "capability packet")
    completion = one(receipt.get("completions"), "capability completion")
    packet = packet_row.get("packet")
    payload = completion.get("payload")
    require(
        isinstance(packet, Mapping) and packet.get("contract_version") == 2
        and packet.get("run_id") == run_id and packet.get("execution_allowed") is False
        and packet_row.get("run_id") == run_id
        and packet_row.get("status") == "completed"
        and packet_row.get("packet_hash") == sha256(canonical_json(packet).encode())
        and completion.get("run_id") == run_id and isinstance(payload, Mapping)
        and isinstance(completion.get("receipt"), Mapping)
        and completion["receipt"].get("packet_id") == packet_row.get("id")
        and completion["receipt"].get("packet_hash") == packet_row.get("packet_hash")
        and isinstance(payload.get("packet"), Mapping)
        and payload["packet"].get("id") == packet_row.get("id")
        and payload["packet"].get("packet_hash") == packet_row.get("packet_hash")
        and payload["packet"].get("packet") == packet,
        "discovery capability packet or completion lineage is invalid",
    )
    coverage = packet.get("coverage")
    require(
        isinstance(coverage, Mapping) and coverage.get("complete_market_coverage") is False
        and payload.get("coverage") == coverage,
        "discovery capability coverage is invalid",
    )
    source_plan = coverage.get("source_plan")
    plan_keys = {
        "version", "source_capability_version", "reference_version",
        "required_baseline_capability_ids", "planned_task_ids", "required_tasks",
        "plan_hash",
    }
    require(isinstance(source_plan, Mapping) and set(source_plan) == plan_keys,
            "discovery required source plan is missing or malformed")
    plan_body = {key: source_plan[key] for key in source_plan if key != "plan_hash"}
    require(
        source_plan.get("version") == 1
        and source_plan.get("plan_hash") == sha256(canonical_json(plan_body).encode()),
        "discovery required source plan hash is invalid",
    )

    policy = load_intelligence_policy(load_settings())
    registry = load_source_capabilities()
    required_ids = tuple(policy.required_baseline_capability_ids)
    require(
        source_plan.get("source_capability_version") == policy.source_capability_version
        and source_plan.get("required_baseline_capability_ids") == list(required_ids)
        and all(
            capability_id in registry
            and registry[capability_id].requirement_tier == "required_baseline"
            and registry[capability_id].enabled
            and registry[capability_id].health == "enabled"
            and registry[capability_id].required_credential is None
            for capability_id in required_ids
        ),
        "discovery required capability registry or policy binding is invalid",
    )
    expected_due = {
        (capability_id, theme_id)
        for capability_id in required_ids
        for theme_id in (
            tuple(theme for theme in policy.seed_domains if theme in registry[capability_id].themes)
            if registry[capability_id].query_kind == "theme_search"
            else (None,)
        )
    }
    required_tasks = source_plan.get("required_tasks")
    planned_ids = source_plan.get("planned_task_ids")
    require(
        isinstance(required_tasks, list) and isinstance(planned_ids, list)
        and planned_ids and len(planned_ids) == len(set(planned_ids))
        and all(isinstance(value, str) and UUID.fullmatch(value) for value in planned_ids)
        and all(isinstance(row, Mapping) and set(row) == {"task_id", "capability_id", "theme_id"}
                and isinstance(row.get("task_id"), str) and UUID.fullmatch(row["task_id"])
                and row["task_id"] in planned_ids for row in required_tasks),
        "discovery required task plan is invalid",
    )
    planned_due = {
        (str(row["capability_id"]), row.get("theme_id")) for row in required_tasks
    }
    require(
        len(required_tasks) == len(planned_due) == len(expected_due)
        and planned_due == expected_due,
        "discovery required task coverage is incomplete",
    )

    task_rows = receipt.get("discovery_stage_tasks")
    require(isinstance(task_rows, list), "discovery required task evidence is missing")
    tasks = {
        str(row.get("id")): row for row in task_rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    require(
        len(tasks) == len(task_rows)
        and set(planned_ids) <= set(tasks)
        and all(tasks[task_id].get("run_id") == run_id for task_id in planned_ids),
        "discovery required task evidence is incomplete",
    )
    terminal_states = {"succeeded", "failed", "deferred", "uncertain"}
    require(all(tasks[task_id].get("state") in terminal_states for task_id in planned_ids),
            "discovery task plan is not terminal")
    planned_task_by_due: dict[tuple[str, object], Mapping[str, object]] = {}
    for planned in required_tasks:
        task = tasks[str(planned["task_id"])]
        capability_id = str(planned["capability_id"])
        capability = registry[capability_id]
        expected_stage = {
            "universe": "reference",
            "screener": "screen",
            "quote": "quote",
            "issuer_submissions": "enrich",
            "filing_document": "enrich",
        }.get(capability.query_kind, "signals")
        result = task.get("result")
        theme_id = result.get("theme_id") if isinstance(result, Mapping) else None
        due = (str(task.get("capability_id")), theme_id)
        require(
            due == (planned.get("capability_id"), planned.get("theme_id"))
            and task.get("provider") == capability.provider
            and task.get("query_kind") == capability.query_kind
            and task.get("stage") == expected_stage
            and task.get("state") == "succeeded",
            "discovery required capability task does not match its registry or success state",
        )
        planned_task_by_due[due] = task
    require(set(planned_task_by_due) == expected_due,
            "discovery required task evidence is incomplete")

    reference_id = "sec_company_tickers_universe"
    reference_task = planned_task_by_due.get((reference_id, None))
    require(reference_task is not None, "discovery required reference task is missing")
    reference_result = reference_task.get("result")
    reference_coverage = reference_result.get("reference_coverage") \
        if isinstance(reference_result, Mapping) else None
    manifest_id = coverage.get("reference_manifest_id")
    require(
        coverage.get("reference_status") == "healthy"
        and isinstance(manifest_id, str) and UUID.fullmatch(manifest_id)
        and isinstance(reference_coverage, Mapping)
        and reference_coverage.get("reference_status") == "healthy"
        and reference_coverage.get("reference_manifest_id") == manifest_id,
        "discovery reference must be a healthy selected manifest",
    )
    bindings = [row for row in receipt.get("reference_run_bindings", [])
                if isinstance(row, Mapping) and row.get("run_id") == run_id
                and row.get("capability_id") == reference_id]
    require(
        len(bindings) == 1 and bindings[0].get("reference_status") == "healthy"
        and bindings[0].get("manifest_id") == manifest_id,
        "discovery reference binding is unavailable or stale",
    )
    manifests = {row.get("id"): row for row in receipt.get("reference_manifests", [])
                 if isinstance(row, Mapping)}
    manifest = manifests.get(manifest_id)
    require(
        isinstance(manifest, Mapping)
        and manifest.get("reference_version") == source_plan.get("reference_version")
        and manifest.get("revision") == reference_coverage.get("reference_revision"),
        "discovery reference manifest version or revision is invalid",
    )
    selected_receipts = [row for row in receipt.get("reference_chunk_receipts", [])
                         if isinstance(row, Mapping) and row.get("manifest_id") == manifest_id]
    selected_memberships = [row for row in receipt.get("reference_snapshot_memberships", [])
                            if isinstance(row, Mapping) and row.get("manifest_id") == manifest_id]
    selected_seals = [row for row in receipt.get("reference_finalization_seals", [])
                      if isinstance(row, Mapping) and row.get("manifest_id") == manifest_id]
    revisions_by_id = {row.get("id"): row for row in receipt.get("security_reference_revisions", [])
                       if isinstance(row, Mapping)}
    require(
        len(selected_seals) == 1 and selected_receipts and selected_memberships
        and all(row.get("security_revision_id") in revisions_by_id for row in selected_memberships),
        "discovery reference finalized membership evidence is incomplete",
    )
    try:
        _validate_reference_semantic_lineage(
            {manifest_id: dict(manifest)},
            {str(row["security_revision_id"]): dict(revisions_by_id[row["security_revision_id"]])
             for row in selected_memberships},
            {manifest_id: [dict(row) for row in selected_receipts]},
            {manifest_id: [dict(row) for row in selected_memberships]},
            {manifest_id: dict(selected_seals[0])},
        )
    except ValueError as error:
        raise RuntimeError("discovery reference semantic lineage is invalid") from error

    persisted_receipt_rows = receipt.get("source_receipts", [])
    persisted_receipts = {row.get("id"): row for row in persisted_receipt_rows
                          if isinstance(row, Mapping)}
    reservations = {row.get("id"): row for row in receipt.get("source_quota_reservations", [])
                    if isinstance(row, Mapping)}
    source_items = {row.get("id"): row for row in receipt.get("source_items", [])
                    if isinstance(row, Mapping)}
    run_items = [row for row in receipt.get("intelligence_run_items", [])
                 if isinstance(row, Mapping)]
    source_provenance = {row.get("source_item_id"): row
                         for row in receipt.get("source_item_provenance", [])
                         if isinstance(row, Mapping)}
    run_provenance = {row.get("run_item_id"): row
                      for row in receipt.get("run_source_item_provenance", [])
                      if isinstance(row, Mapping)}
    completion_receipts = payload.get("receipts")
    require(isinstance(completion_receipts, list),
            "discovery required parsed receipt evidence is missing")
    completion_by_id = {
        row.get("source_receipt_id"): row for row in completion_receipts
        if isinstance(row, Mapping)
    }
    require(
        isinstance(persisted_receipt_rows, list)
        and len(persisted_receipts) == len(persisted_receipt_rows)
        and len(completion_by_id) == len(completion_receipts),
        "discovery source receipt identities are invalid or duplicated",
    )
    required_receipt_ids: set[str] = set()
    for due, task in planned_task_by_due.items():
        if due == (reference_id, None):
            continue
        result = task.get("result")
        checkpoint = result.get("checkpoint") if isinstance(result, Mapping) else None
        parsed = checkpoint.get("receipt") if isinstance(checkpoint, Mapping) else None
        receipt_id = parsed.get("source_receipt_id") if isinstance(parsed, Mapping) else None
        stored = persisted_receipts.get(receipt_id)
        coverage_status = parsed.get("metadata", {}).get("coverage_status") \
            if isinstance(parsed, Mapping) and isinstance(parsed.get("metadata"), Mapping) else None
        expected_receipt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:receipt:{run_id}:{task.get('id')}",
        ))
        require(
            isinstance(receipt_id, str) and receipt_id == expected_receipt_id
            and receipt_id not in required_receipt_ids
            and isinstance(checkpoint, Mapping)
            and checkpoint.get("cache_key") == parsed.get("cache_key")
            and isinstance(stored, Mapping) and completion_by_id.get(receipt_id) == parsed
            and parsed.get("provider") == task.get("provider") == stored.get("provider")
            and parsed.get("requested_window") == task.get("requested_window")
            and isinstance(parsed.get("metadata"), Mapping)
            and parsed["metadata"].get("capability_id") == task.get("capability_id")
            and parsed.get("status") in {"succeeded", "cache_hit"}
            and stored.get("status") in {"succeeded", "cache_hit"}
            and coverage_status in {"success_empty", "success_nonempty"}
            and all(parsed.get(key) == stored.get(db_key) for key, db_key in (
                ("provider", "provider"), ("reservation_id", "reservation_id"),
                ("cache_key", "cache_key"), ("requested_window", "requested_window"),
                ("retrieved_at", "retrieved_at"), ("expires_at", "expires_at"),
                ("request_cost", "request_cost"), ("returned_count", "returned_count"),
                ("accepted_count", "accepted_count"), ("duplicate_count", "duplicate_count"),
                ("dropped_count", "dropped_count"), ("response_hash", "response_hash"),
            ))
            and stored.get("run_id") == run_id
            and stored.get("reservation_id") in reservations
            and reservations[stored["reservation_id"]].get("run_id") == run_id
            and reservations[stored["reservation_id"]].get("provider") == stored.get("provider"),
            "discovery required capability lacks a parsed receipt-backed success",
        )
        required_receipt_ids.add(receipt_id)
        returned = stored.get("returned_count")
        accepted = stored.get("accepted_count")
        duplicates = stored.get("duplicate_count")
        dropped = stored.get("dropped_count")
        require(
            type(returned) is int and returned >= 0
            and type(accepted) is int and 0 <= accepted <= returned
            and type(duplicates) is int and duplicates >= 0
            and type(dropped) is int and dropped >= 0
            and (coverage_status == "success_empty") == (accepted == 0),
            "discovery required capability empty/nonempty receipt semantics are invalid",
        )
        persisted_run_items = [row for row in run_items
                               if row.get("source_receipt_id") == receipt_id]
        if coverage_status == "success_empty":
            require(not persisted_run_items,
                    "discovery success_empty receipt unexpectedly has saved items")
        else:
            require(persisted_run_items,
                    "discovery success_nonempty receipt lacks saved item provenance")
            for run_item in persisted_run_items:
                item_id = run_item.get("source_item_id")
                item = source_items.get(item_id)
                item_provenance = source_provenance.get(item_id)
                scoped_provenance = run_provenance.get(run_item.get("id"))
                require(
                    isinstance(item, Mapping) and isinstance(item_provenance, Mapping)
                    and isinstance(scoped_provenance, Mapping)
                    and isinstance(item_id, str) and UUID.fullmatch(item_id)
                    and isinstance(item.get("content_hash"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", item["content_hash"])
                    and item_id == str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"market-source:{item['content_hash']}",
                    ))
                    and item.get("source_receipt_id") == receipt_id
                    and item.get("provider") == stored.get("provider")
                    and run_item.get("run_id") == run_id
                    and run_item.get("disposition") in {"accepted", "near_duplicate"}
                    and run_item.get("drop_reason") is None
                    and item_provenance.get("provider") == item.get("provider")
                    and item_provenance.get("canonical_item_url") == item.get("canonical_url")
                    and str(item_provenance.get("request_url", "")).startswith("https://")
                    and scoped_provenance.get("run_id") == run_id
                    and scoped_provenance.get("source_item_id") == item_id
                    and scoped_provenance.get("source_receipt_id") == receipt_id
                    and scoped_provenance.get("provider") == item.get("provider")
                    and str(scoped_provenance.get("request_url", "")).startswith("https://"),
                    "discovery success_nonempty saved item provenance is invalid",
                )

    packet_evidence = packet.get("evidence")
    research = packet.get("research_candidates")
    actions = packet.get("action_candidates")
    require(isinstance(packet_evidence, list) and isinstance(research, list)
            and isinstance(actions, list), "discovery research/action lanes are invalid")
    evidence_by_id = {row.get("item_id"): row for row in packet_evidence
                      if isinstance(row, Mapping)}
    research_by_key: dict[str, Mapping[str, object]] = {}
    for candidate in research:
        require(isinstance(candidate, Mapping), "discovery research candidate is invalid")
        suitability = candidate.get("suitability")
        require(isinstance(suitability, Mapping), "discovery suitability lineage is missing")
        suitability_body = {key: value for key, value in suitability.items()
                            if key != "evaluation_hash"}
        candidate_body = {key: value for key, value in candidate.items()
                          if key != "candidate_hash"}
        key = candidate.get("candidate_key")
        lineage = suitability.get("lineage")
        refs = candidate.get("evidence")
        require(
            isinstance(key, str) and key not in research_by_key
            and suitability.get("evaluation_hash") == sha256(canonical_json(suitability_body).encode())
            and candidate.get("candidate_hash") == sha256(canonical_json(candidate_body).encode())
            and isinstance(lineage, Mapping) and lineage.get("run_id") == run_id
            and lineage.get("policy_version") == packet.get("policy_version")
            and lineage.get("reference_manifest_id") == manifest_id
            and lineage.get("reference_revision") == manifest.get("revision")
            and isinstance(refs, list),
            "discovery research suitability hash or lineage is invalid",
        )
        receipt_ids = lineage.get("evidence_receipt_ids")
        require(isinstance(receipt_ids, Mapping),
                "discovery research receipt lineage is invalid")
        for ref in refs:
            item_id = ref.get("item_id") if isinstance(ref, Mapping) else None
            evidence = evidence_by_id.get(item_id)
            item = source_items.get(item_id)
            require(
                isinstance(evidence, Mapping) and isinstance(item, Mapping)
                and evidence.get("content_hash") == item.get("content_hash")
                and isinstance(evidence.get("source_identity"), Mapping)
                and evidence["source_identity"].get("receipt_id") == receipt_ids.get(item_id)
                and receipt_ids.get(item_id) == item.get("source_receipt_id")
                and item.get("source_receipt_id") in persisted_receipts,
                "discovery research source lineage is invalid",
            )
        research_by_key[key] = candidate
    for action in actions:
        candidate = research_by_key.get(action.get("candidate_key")) \
            if isinstance(action, Mapping) else None
        suitability = candidate.get("suitability") if isinstance(candidate, Mapping) else None
        require(
            isinstance(action, Mapping) and isinstance(candidate, Mapping)
            and isinstance(suitability, Mapping)
            and candidate.get("research_state") == "analysis_ready"
            and suitability.get("state") == "eligible"
            and action.get("candidate_hash") == candidate.get("candidate_hash")
            and action.get("suitability_hash") == suitability.get("evaluation_hash"),
            "discovery research-to-action promotion is invalid",
        )

    optional_failures = tuple(sorted(
        f"{row.get('capability_id')}:{row.get('state')}"
        for row in task_rows
        if isinstance(row, Mapping)
        and row.get("capability_id") not in required_ids
        and row.get("state") != "succeeded"
    ))
    return VerificationResult(
        ok=True,
        required_capability_ids=required_ids,
        optional_failures=optional_failures,
    )


def verify_discovery_capability(receipt: Mapping[str, object]) -> VerificationResult:
    """Fail closed with one stable public error type for malformed protected rows."""
    try:
        return _verify_discovery_capability(receipt)
    except RuntimeError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise RuntimeError(
            "discovery capability evidence is unavailable or malformed"
        ) from error


def verify_component_artifacts(repo: Path, candidate: str, record: Mapping, source: ReleaseDataSource, *, deployed_at: datetime | None = None) -> None:
    """Bind protected management-plane readbacks to all changed candidate bytes."""
    names = (*FUNCTIONS, "owner-web-site")
    rows = record.get("component_readbacks")
    require(isinstance(rows, list) and [row.get("component") for row in rows] == list(names),
            "all four component readbacks are required")
    hosting = json.loads(git(repo, "show", f"{candidate}:.openai/hosting.json"))
    for row in rows:
        name = row["component"]
        require(row.get("candidate_sha") == candidate and isinstance(row.get("deployment_id"), str)
                and row["deployment_id"] and isinstance(row.get("version"), str) and row["version"]
                and row.get("origin") == "management_plane_download", "component platform identity is incomplete")
        expected = (git_files(repo, candidate, f"supabase/functions/{name}") if name in FUNCTIONS else None)
        files = source.artifact(row["artifact_id"])
        digest = tree_sha256(files)
        require(digest == row["deployed_sha256"], "component readback artifact bytes mismatch")
        if expected is not None:
            function = next(item for item in record["functions"] if item["function"] == name)
            require(files == expected and row["deployment_id"] == function.get("deployment_id")
                    and row["version"] == str(function["function_version"]), "component deployed byte/version parity mismatch")
        else:
            require(row.get("project_id") == hosting.get("project_id")
                    and hosting.get("static", {}).get("directory") == "dist"
                    and {path: sha256(raw) for path, raw in files.items()} == record["static_assets"]["files"],
                    "Site deployment identity or deployed bytes mismatch")
        prior = row.get("prior")
        require(isinstance(prior, Mapping) and type(prior.get("exists")) is bool
                and isinstance(prior.get("configuration"), Mapping), "component prior-state rollback capture is incomplete")
        captured = timestamp(prior.get("captured_at"))
        require(captured < (deployed_at or datetime.now(timezone.utc)), "component rollback capture is not predeployment")
        if prior["exists"]:
            require(isinstance(prior.get("deployment_id"), str) and prior["deployment_id"]
                    and isinstance(prior.get("version"), str) and prior["version"]
                    and tree_sha256(source.artifact(prior["artifact_id"])) == prior["source_sha256"],
                    "component prior rollback bytes or identity mismatch")
        else:
            require(prior.get("deployment_id") is None and prior.get("version") is None
                    and prior.get("artifact_id") is None, "component absence proof is inconsistent")


@runtime_checkable
class ReleaseDataSource(Protocol):
    def scheduled_run(self, deployed_at: str) -> str: ...
    def deployment(self, deployment_id: int) -> Mapping: ...
    def ci(self, workflow_run_id: int) -> Mapping: ...
    def merge(self, pull_request_number: int) -> Mapping: ...
    def reviews(self, pull_request_number: int) -> list[Mapping]: ...
    def release_rows(self, run_id: str) -> Mapping: ...
    def artifact(self, artifact_id: int) -> Mapping[str, bytes]: ...


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "receipt timestamp has no timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as error:
        raise RuntimeError("receipt timestamp is invalid") from error


def path_is_safe(path: str) -> bool:
    return bool(path) and "\\" not in path and not PurePosixPath(path).is_absolute() and all(part not in {"", ".", ".."} for part in path.split("/"))


def tree_sha256(files: Mapping[str, bytes]) -> str:
    require(files and all(path_is_safe(name) and isinstance(raw, bytes) for name, raw in files.items()), "artifact paths or bytes are invalid")
    digest = hashlib.sha256()
    for name, raw in sorted(files.items()):
        digest.update(name.encode() + b"\0" + raw + b"\0")
    return digest.hexdigest()


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    require(result.returncode == 0, "candidate Git object is unavailable")
    return result.stdout


def git_commit(repo: Path, sha: str) -> datetime:
    require(bool(SHA.fullmatch(sha)), "candidate SHA is malformed")
    raw = git(repo, "cat-file", "commit", sha)
    require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha, "candidate Git object hash mismatch")
    return timestamp(git(repo, "show", "-s", "--format=%cI", sha).decode().strip())


def git_files(repo: Path, sha: str, prefix: str) -> dict[str, bytes]:
    files = {}
    for entry in git(repo, "ls-tree", "-rz", sha, "--", prefix).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        require(mode in {"100644", "100755"} and kind == "blob", "candidate artifact includes non-file entries")
        name = path.decode()
        require(name.startswith(prefix + "/"), "candidate artifact path mismatch")
        files[name[len(prefix) + 1:]] = git(repo, "cat-file", "blob", object_id)
    require(files, "candidate source tree is empty")
    return files


def verify_artifacts(repo: Path, static_root: Path, candidate: str, record: Mapping, source: ReleaseDataSource, now: datetime, deployed: datetime) -> None:
    verify_component_artifacts(repo, candidate, record, source, deployed_at=deployed)
    migrations = git_files(repo, candidate, "sql/migrations")
    expected_migrations = [{"path": f"sql/migrations/{path}", "version": Path(path).name.split("_", 1)[0],
                            "sha256": migration_statements_sha256(normalize_migration_statements(raw.decode("utf-8")))}
                           for path, raw in sorted(migrations.items()) if path.endswith(".sql")]
    require(record["migrations"] == expected_migrations, "migration byte hashes or complete version set differ from candidate")
    functions = record["functions"]
    require(isinstance(functions, list) and [row["function"] for row in functions] == list(FUNCTIONS), "function evidence is incomplete")
    for row in functions:
        require(row["git_sha"] == candidate and type(row["function_version"]) is int and row["function_version"] > 0
                and row["source_sha256"] == tree_sha256(git_files(repo, candidate, f"supabase/functions/{row['function']}")), "function byte hash or candidate SHA mismatch")
    static = record["static_assets"]
    require(static["candidate_sha"] == candidate and static["source_sha256"] == tree_sha256(git_files(repo, candidate, "apps/web")), "static source candidate hash mismatch")
    require(static_root.is_dir() and not static_root.is_symlink(), "static build is unavailable")
    local_files = {}
    for path in sorted(static_root.rglob("*")):
        require(not path.is_symlink(), "static build includes a symlink")
        if path.is_file():
            local_files[path.relative_to(static_root).as_posix()] = sha256(path.read_bytes())
    require(local_files and "index.html" in local_files and local_files == static["files"], "static artifact bytes do not match protected deployment")
    capture = record["rollback_capture"]
    captured = timestamp(capture["captured_at"])
    require(captured < deployed <= now and git_commit(repo, capture["commit_sha"]) <= captured, "rollback capture is not predeployment")
    captured_files = source.artifact(capture["artifact_id"])
    captured_hash = tree_sha256(captured_files)
    require(captured_files == git_files(repo, capture["commit_sha"], "supabase/functions/market-briefing-gateway")
            and captured_hash == capture["source_sha256"], "captured rollback artifact bytes mismatch")
    outcome = record.get("deployment_outcome")
    if outcome == "succeeded":
        ready = record["rollback_readiness"]
        drill = ready["isolated_drill"]
        require(ready["status"] == "ready" and type(ready["function_version"]) is int and ready["function_version"] > 0
                and ready["source_sha256"] == captured_hash and drill["status"] == "verified" and drill["isolated"] is True
                and drill["source_sha256"] == captured_hash and timestamp(drill["started_at"]) <= timestamp(drill["completed_at"]), "successful deployment rollback readiness is incomplete")
    elif outcome == "failed":
        rollback = record["rollback"]
        gateway, runtime = rollback["gateway"], rollback["runtime_login"]
        require(rollback["status"] == "rolled_back" and gateway["status"] == "restored" and gateway["commit_sha"] == capture["commit_sha"]
                and gateway["source_sha256"] == captured_hash and type(gateway["function_version"]) is int and gateway["function_version"] > 0
                and captured <= timestamp(rollback["gateway_restored_at"]) <= timestamp(rollback["dashboard_cleaned_at"]) < deployed
                and runtime["login"] is False and runtime["memberships"] == 0
                and rollback["dashboard_secrets_unset"] == ["DASHBOARD_ALLOWED_ORIGINS", "DASHBOARD_DATABASE_URL", "DASHBOARD_OWNER_USER_ID"], "gateway-first rollback evidence is incomplete")
    else:
        raise RuntimeError("deployment outcome is missing or unsafe")


def one(rows: object, label: str) -> Mapping:
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping), f"scheduled {label} receipt is missing or ambiguous")
    return rows[0]


def report_identity(kind: str, market_date: str, packet_hash: str, report_hash: str) -> tuple[str, str]:
    key = sha256(f"v2:{kind}:{market_date}:{packet_hash}:{report_hash}".encode())
    return key, f"{key[:8]}-{key[8:12]}-5{key[13:16]}-8{key[17:20]}-{key[20:32]}"


def verify_scheduled(rows: Mapping, run_id: str, deployed: datetime, now: datetime) -> dict:
    run = one(rows["run"], "run")
    intelligence = one(rows["intelligence_runs"], "intelligence run")
    require(UUID.fullmatch(run_id) and run["id"] == intelligence["id"] == run_id, "scheduled run identity mismatch")
    phase, market_date = run["scheduled_phase"], run["scheduled_market_date"]
    start, end = timestamp(run["started_at"]), timestamp(run["finished_at"])
    require(phase in {"pre-market", "intraday", "post-market"} and run["kind"] == intelligence["phase"] == phase
            and intelligence["market_date"] == market_date and run["status"] in {"completed", "suppressed"}
            and deployed < start <= end <= now and (now - end).total_seconds() <= MAX_SCHEDULED_RECEIPT_AGE_SECONDS, "scheduled receipt is stale, future, or not postdeployment")
    requests = rows["requests"]
    require(all(isinstance(row, Mapping) and UUID.fullmatch(str(row.get("request_id", ""))) for row in requests), "scheduled request UUID is invalid")
    start_request = one([row for row in requests if row["request_id"] == run["gateway_request_id"]], "start")
    require(start_request["operation"] == "start_run" and start_request["run_id"] == run_id and start_request["status"] == "completed"
            and start_request["response"]["run_id"] == run_id and start_request["response"].get("duplicate") is False
            and start_request["response"].get("dry_run") is not True, "scheduled start is duplicate or dry-run")
    completion = one(rows["completions"], "collection")
    packet = one(rows["packets"], "packet")
    require(UUID.fullmatch(completion["completion_id"]) and completion["run_id"] == run_id
            and any(row["id"] == completion["completion_id"] and row["run_id"] == run_id and row["status"] == "completed" for row in rows["run_events"])
            and rows["checkpoints"] and all(row["run_id"] == run_id for row in rows["checkpoints"]), "scheduled collection stage is not persisted")
    require(UUID.fullmatch(packet["id"]) and packet["run_id"] == run_id and sha256(canonical_json(packet["packet"]).encode()) == packet["packet_hash"]
            and completion["receipt"]["packet_id"] == packet["id"] and completion["receipt"]["packet_hash"] == packet["packet_hash"], "scheduled packet content or collection hash mismatch")
    event_ids = set()
    for row in rows["events"]:
        require(UUID.fullmatch(row["id"]) and row["run_id"] == run_id and sha256(canonical_json(row["canonical"]).encode()) == row["content_hash"], "event content hash mismatch")
        event_ids.add(row["id"])
    for row in rows["rankings"]:
        require(UUID.fullmatch(row["id"]) and row["run_id"] == run_id and row["event_id"] in event_ids
                and sha256(canonical_json(row["canonical"]).encode()) == row["content_hash"], "ranking content or event relationship mismatch")
    evaluation = one([row for row in requests if row["operation"] == "evaluate_and_publish" and row["run_id"] == run_id and row["status"] == "completed"], "evaluation")
    evaluation_publication = one([row for row in rows["evaluation_publications"] if row["id"] == evaluation["response"]["publication_id"]], "evaluation publication")
    require(UUID.fullmatch(evaluation_publication["id"]) and evaluation_publication["run_id"] == run_id and evaluation_publication["phase"] == phase
            and evaluation_publication["market_date"] == market_date and evaluation_publication["status"] == "suppressed", "scheduled evaluation publication mismatch")
    origin = one(rows["origins"], "report origin")
    report_request = one([row for row in requests if row["request_id"] == origin["request_id"] and row["operation"] == "record_report" and row["run_id"] is None and row["status"] == "completed"], "report request")
    report = one([row for row in rows["reports"] if row["id"] == report_request["response"]["report_id"]], "report")
    require((origin["requested_idempotency_key"], origin["requested_report_id"]) == report_identity(origin["requested_kind"], market_date, packet["packet_hash"], origin["requested_report_hash"])
            and (report["idempotency_key"], report["id"]) == report_identity(report["kind"], market_date, packet["packet_hash"], report["report_hash"]), "scheduled original or rendered report identity mismatch")
    allowed = {"pre-market": {"morning", "monthly"}, "intraday": {"intraday", "urgent"}, "post-market": {"weekly", "monthly", "theme", "urgent"}}
    require(origin["run_id"] == run_id and origin["scheduled_phase"] == phase and origin["market_date"] == market_date and origin["requested_kind"] in allowed[phase]
            and UUID.fullmatch(origin["requested_report_id"]) and re.fullmatch(r"[0-9a-f]{64}", origin["requested_idempotency_key"])
            and re.fullmatch(r"[0-9a-f]{64}", origin["requested_report_hash"])
            and UUID.fullmatch(report["id"]) and report["run_id"] == run_id and report["market_date"] == market_date
            and report["packet_id"] == origin["requested_packet_id"] == packet["id"]
            and sha256(canonical_json(report["report"]).encode()) == report["report_hash"] == report_request["response"]["report_hash"]
            and sha256(report["rendered_text"].encode()) == report["rendered_hash"] == report_request["response"]["rendered_hash"], "scheduled report origin, relationship, or hash mismatch")
    publication = one([row for row in rows["publications"] if row["report_id"] == report["id"]], "publication")
    require(publication["idempotency_key"] == report["idempotency_key"] and re.fullmatch(r"[0-9a-f]{64}", publication["idempotency_key"]), "scheduled outbox identity mismatch")
    ids = publication["telegram_message_ids"]
    if publication["status"] == "delivered":
        accepted = timestamp(publication["telegram_accepted_at"])
        require(isinstance(ids, list) and ids and all(type(value) is int and value > 0 for value in ids)
                and start <= accepted <= end and publication.get("suppression_reason") is None and run["telegram_message_ids"] == ids, "original Telegram delivery receipt is incomplete")
        delivery = {"status": "accepted_by_telegram", "telegram_message_ids": ids, "telegram_accepted_at": publication["telegram_accepted_at"]}
    else:
        reason = publication.get("suppression_reason")
        require(publication["status"] == "suppressed" and ids == [] and publication["telegram_accepted_at"] is None
                and isinstance(reason, str) and reason.strip() and run["telegram_message_ids"] == [], "explicit scheduled suppression reason is missing")
        delivery = {"status": "suppressed", "telegram_message_ids": [], "suppression_reason": reason}
    require(rows["quota"] and all(row["run_id"] == run_id and UUID.fullmatch(row["id"]) and type(row["actual_requests"]) is int
            and type(row["reserved_requests"]) is int and 0 <= row["actual_requests"] <= row["reserved_requests"] for row in rows["quota"]), "scheduled quota receipts are incomplete")
    capability = verify_discovery_capability(rows)
    return {"run_id": run_id, "packet_id": packet["id"], "packet_hash": packet["packet_hash"], "report_id": report["id"], "report_hash": report["report_hash"],
            "stage_ids": {"collection": completion["completion_id"], "packet": packet["id"], "evaluation": evaluation["request_id"], "report": report_request["request_id"], "publication": publication["report_id"]},
            "publication_key": publication["idempotency_key"], "publication_receipt": delivery,
            "discovery_capability": {"ok": capability.ok,
                                     "required_capability_ids": list(capability.required_capability_ids),
                                     "optional_failures": list(capability.optional_failures)}}


def verify_release(source: ReleaseDataSource, *, deployment_id: int, repo_root: Path = ROOT, static_root: Path = ROOT / "dist",
                   clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, object]:
    require(not isinstance(source, Mapping) and isinstance(source, ReleaseDataSource), "protected production data source is required")
    try:
        now = clock()
        require(now.tzinfo is not None, "verifier clock must have timezone")
        record = source.deployment(deployment_id)
        candidate = record["sha"]
        candidate_commit_time = git_commit(repo_root, candidate)
        ci, merge = source.ci(record["workflow_run_id"]), source.merge(record["pull_request_number"])
        deployed, merged = timestamp(record["deployed_at"]), timestamp(merge["merged_at"])
        require(record["id"] == deployment_id and record["environment"] == "production" and record["candidate_sha"] == candidate
                and ci["head_sha"] == candidate and ci["status"] == "completed" and ci["conclusion"] == "success"
                and ci["path"] == ".github/workflows/owner-dashboard-ci.yml" and ci["id"] == record["workflow_run_id"]
                and merge["merged"] is True and merge["merge_commit_sha"] == candidate
                and merged <= candidate_commit_time < deployed <= now and candidate_commit_time <= timestamp(ci["updated_at"]) <= deployed, "protected CI/merge/deployment candidate SHA or time mismatch")
        reviewed_head = merge["head"]["sha"]
        require(bool(re.fullmatch(r"[0-9a-f]{40}", reviewed_head)), "reviewed PR head is malformed")
        reviewed_head_time = git_commit(repo_root, reviewed_head)
        reviews = source.reviews(record["pull_request_number"])
        require(any(row["state"] == "APPROVED" and row["commit_id"] == reviewed_head
                    and reviewed_head_time <= timestamp(row["submitted_at"]) <= merged <= candidate_commit_time <= deployed for row in reviews), "independent review of exact PR head is missing")
        if reviewed_head != candidate:
            ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", reviewed_head, candidate], cwd=repo_root, capture_output=True, check=False)
            if ancestor.returncode != 0:
                reviewed_tree = subprocess.run(["git", "rev-parse", f"{reviewed_head}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
                candidate_tree = subprocess.run(["git", "rev-parse", f"{candidate}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
                require(reviewed_tree.returncode == candidate_tree.returncode == 0 and reviewed_tree.stdout == candidate_tree.stdout, "reviewed PR head does not bind candidate merge/tree")
        verify_artifacts(repo_root, static_root, candidate, record, source, now, deployed)
        require(record.get("dry_run") is False, "protected deployment dry-run authority must be false")
        dry = record["dry_run_evidence"]
        before, after = dry["before"], dry["after"]
        argv = dry.get("safe_command_argv")
        script_sha = dry.get("candidate_script_sha256")
        expected_command_hash = hashlib.sha256(json.dumps({"argv": argv, "candidate_sha": candidate,
            "candidate_script_sha256": script_sha}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        require(isinstance(before, Mapping) and isinstance(after, Mapping) and before["tables"] == after["tables"]
                and isinstance(dry["table_deltas"], Mapping) and set(dry["table_deltas"]) == set(before["tables"])
                and all(type(value) is int and value == 0 for value in dry["table_deltas"].values())
                and type(dry["safe_command_exit_code"]) is int and dry["safe_command_exit_code"] == 0
                and isinstance(argv, list) and all(isinstance(arg, str) for arg in argv) and candidate in argv and "--dry-run" in argv
                and isinstance(script_sha, str) and script_sha == sha256(git(repo_root, "show", f"{candidate}:scripts/deploy_owner_dashboard_api.py"))
                and dry["safe_command_sha256"] == expected_command_hash
                and before.get("source") == after.get("source")
                and all(isinstance(value, Mapping) and set(value) == {"count", "rows_sha256"} and type(value.get("count")) is int
                        and isinstance(value.get("rows_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["rows_sha256"])
                        for value in before["tables"].values()), "protected dry-run side-effect evidence is incomplete")
        require(record["canaries"] == {"owner": 200, "anonymous": 401, "non_owner": 403}, "protected owner/denial canaries are incomplete")
        run_id = source.scheduled_run(record["deployed_at"])
        chain = verify_scheduled(source.release_rows(run_id), run_id, deployed, now)
        return {"status": "verified", "candidate_sha": candidate, "deployment_id": deployment_id, **chain}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise RuntimeError("protected release evidence is unavailable or malformed") from error


def main() -> int:
    from scripts.protected_evidence import GitHubProductionDataSource, PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--deployment-id", type=int, required=True)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--static-root", type=Path, required=True)
    args = parser.parse_args()
    with PostgresReadOnlySource(os.environ.get("RELEASE_READONLY_DATABASE_URL", ""), args.production_project_ref) as database:
        source = GitHubProductionDataSource(args.repository, args.production_project_ref, database)
        print(json.dumps(verify_release(source, deployment_id=args.deployment_id, static_root=args.static_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
