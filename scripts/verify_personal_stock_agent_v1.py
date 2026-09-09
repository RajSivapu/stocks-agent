#!/usr/bin/env python3
"""Verify one protected deployment against local Git bytes and queried production rows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tomllib
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.config import load_settings
from lib.intelligence.cursors import SourceCursor
from lib.intelligence.discovery import (
    detect_events,
    load_theme_taxonomy,
    select_reverse_discovery_tasks,
)
from lib.intelligence.normalize import SourceItem, _claim_polarity
from lib.intelligence.pipeline import (
    _collection_window_for_task,
    _timestamp as _pipeline_timestamp,
)
from lib.intelligence.planner import _query_for as _planned_query, load_source_capabilities
from lib.intelligence.policy import load_intelligence_policy
from lib.intelligence.themes import (
    evidence_key,
    propose_dynamic_theme,
    select_dynamic_theme_evidence,
    theme_fingerprint,
)
from lib.intelligence.universe import reference_snapshot_from_rows
from lib.intelligence.types import DiscoveryTask
from lib.release_baseline import expected_snapshot_tables
from scripts.export_recovery_bundle import (
    _ENRICHMENT_PHASE_ENVELOPES,
    _ENRICHMENT_QUERY_CONTRACTS,
    _semantic_hash,
    _validate_reference_semantic_lineage,
    canonical_json,
    sha256,
)
from scripts.function_runtime_manifest import configured_function_runtime
from scripts.verify_owner_dashboard_deployment import (
    validate_scheduled_readiness_receipt,
)

MAX_SCHEDULED_RECEIPT_AGE_SECONDS = 7 * 24 * 60 * 60
SHA = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")


def _configured_query_label(
    task: Mapping[str, object], registry: Mapping[str, object]
) -> str | None:
    result = task.get("result")
    reverse = result.get("reverse_descriptor") if isinstance(result, Mapping) else None
    if isinstance(reverse, Mapping):
        value = reverse.get("query")
        return value if isinstance(value, str) and value.strip() else None
    capability = registry.get(str(task.get("capability_id")))
    pack = getattr(capability, "query_pack", None)
    if not isinstance(pack, Mapping):
        return None
    if getattr(capability, "query_kind", None) == "theme_search":
        themes = pack.get("themes")
        theme_id = task.get("theme_id")
        if theme_id is None and isinstance(result, Mapping):
            theme_id = result.get("theme_id")
        query = themes.get(theme_id) if isinstance(themes, Mapping) else None
    else:
        query = pack.get("default")
    value = query.get("query") if isinstance(query, Mapping) else None
    return value if isinstance(value, str) and value.strip() else None


def _valid_request_cursor(value: object, *, provider: str, capability_id: str) -> bool:
    try:
        cursor = SourceCursor.from_mapping(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return (
        cursor.provider == provider
        and cursor.capability_id == capability_id
        and cursor.to_mapping() == value
    )


def _expected_requested_labels(
    dependencies: list[str],
    tasks: Mapping[str, Mapping[str, object]],
) -> list[str]:
    registry = load_source_capabilities()
    values: set[str] = set()
    for task_id in dependencies:
        task = tasks[task_id]
        result = task.get("result")
        theme_id = task.get("theme_id")
        if theme_id is None and isinstance(result, Mapping):
            theme_id = result.get("theme_id")
        for value in (_configured_query_label(task, registry), theme_id):
            if isinstance(value, str) and value.strip():
                values.add(value)
    return sorted(values)


def _expected_reverse_descriptor(
    task: Mapping[str, object], descriptor: Mapping[str, object]
) -> tuple[dict[str, object], str]:
    event_id = descriptor.get("event_id")
    hypothesis_id = descriptor.get("hypothesis_id")
    query = descriptor.get("query")
    result = task.get("result")
    theme_id = task.get("theme_id")
    if theme_id is None and isinstance(result, Mapping):
        theme_id = result.get("theme_id")
    taxonomy = load_theme_taxonomy()
    theme = taxonomy.themes.get(str(theme_id))
    require(
        isinstance(event_id, str) and UUID.fullmatch(event_id) is not None
        and isinstance(hypothesis_id, str) and UUID.fullmatch(hypothesis_id) is not None
        and isinstance(query, str) and 1 <= len(query) <= 2_000
        and theme is not None,
        "discovery reverse selection descriptor is invalid",
    )
    matching = []
    for edge in theme.value_chain:
        expected_hypothesis_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"value-chain:{event_id}:{theme_id}:{edge.edge_id}",
        ))
        terms = tuple(dict.fromkeys((*edge.query_terms[:3], *theme.synonyms[:1])))
        expected_query = "(" + " OR ".join(
            f'"{term}"' if " " in term else term for term in terms
        ) + ")"
        if hypothesis_id == expected_hypothesis_id and query == expected_query:
            matching.append({
                "adverse_path": edge.adverse_path,
                "direction": edge.direction,
                "evidence_requirement": edge.evidence_requirement,
                "exposure_supported": False,
                "geography": edge.geography,
                "horizon": edge.horizon,
                "invalidation_rule": edge.invalidation_rule,
                "role": edge.role,
                "status": "hypothesis",
            })
    require(len(matching) == 1,
            "discovery reverse selection descriptor does not match the taxonomy")
    selection_task_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"reverse-discovery:{event_id}:{hypothesis_id}:{query}",
    ))
    return matching[0], selection_task_id


def _authorized_dynamic_task_ids(
    receipt: Mapping[str, object],
    *,
    run_id: str,
    phase: object,
    tasks: Mapping[str, Mapping[str, object]],
    planned_ids: set[str],
) -> set[str]:
    """Bind post-plan tasks to reverse dependencies or sealed enrichment requests."""
    dynamic_ids = set(tasks) - planned_ids
    envelope = _ENRICHMENT_PHASE_ENVELOPES.get(str(phase))
    require(isinstance(envelope, Mapping), "discovery adaptive phase is invalid")
    raw_manifests = receipt.get("enrichment_selection_manifests", [])
    raw_descriptors = receipt.get("enrichment_request_descriptors", [])
    require(
        isinstance(raw_manifests, list) and len(raw_manifests) <= 3
        and isinstance(raw_descriptors, list) and len(raw_descriptors) <= 100,
        "discovery adaptive selection evidence is invalid",
    )
    manifests = {
        str(row.get("id")): row for row in raw_manifests
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    descriptors = {
        str(row.get("task_id")): row for row in raw_descriptors
        if isinstance(row, Mapping) and isinstance(row.get("task_id"), str)
    }
    require(
        len(manifests) == len(raw_manifests)
        and len(descriptors) == len(raw_descriptors)
        and set(descriptors) <= dynamic_ids,
        "discovery adaptive selection identities are invalid",
    )

    enrichment_ids: set[str] = set()
    by_manifest: dict[str, list[Mapping[str, object]]] = {}
    for task_id, row in descriptors.items():
        task = tasks.get(task_id)
        manifest_id = row.get("manifest_id")
        manifest = manifests.get(str(manifest_id))
        query_kind = row.get("query_kind")
        contract = _ENRICHMENT_QUERY_CONTRACTS.get(str(query_kind))
        descriptor = row.get("descriptor")
        expected_stage = "quote" if query_kind == "quote" else "enrich"
        require(
            isinstance(task, Mapping) and isinstance(manifest, Mapping)
            and isinstance(contract, tuple) and isinstance(descriptor, Mapping)
            and row.get("id") == task_id and row.get("run_id") == run_id
            and manifest.get("run_id") == run_id
            and (row.get("provider"), row.get("capability_id")) == contract[:2]
            and (task.get("provider"), task.get("capability_id"), task.get("query_kind"))
                == (row.get("provider"), row.get("capability_id"), query_kind)
            and task.get("stage") == expected_stage
            and set(descriptor) == contract[2]
            and len(canonical_json(descriptor).encode()) <= 32_768
            and descriptor.get("dependency_task_ids") == task.get("dependency_ids")
            and row.get("content_hash") == task.get("query_hash"),
            "discovery adaptive request descriptor is invalid",
        )
        request_document = {
            "request_id": row["id"], "task_id": task_id, "stage": task["stage"],
            "provider": row["provider"], "capability_id": row["capability_id"],
            "descriptor": descriptor, "query_kind": query_kind,
            "dependency_ids": task["dependency_ids"],
            "requested_window": task["requested_window"],
            "request_budget": task["request_budget"], "execution_allowed": False,
        }
        require(
            row.get("content_hash") == _semantic_hash(request_document)
            and isinstance(descriptor.get("dependency_task_ids"), list)
            and all(dependency in tasks for dependency in descriptor["dependency_task_ids"]),
            "discovery adaptive request semantic lineage is invalid",
        )
        if query_kind == "filing_document":
            dependencies = descriptor["dependency_task_ids"]
            parent = tasks.get(dependencies[0]) if len(dependencies) == 1 else None
            parent_descriptor = descriptors.get(dependencies[0]) if dependencies else None
            require(
                isinstance(parent, Mapping) and parent.get("state") == "succeeded"
                and isinstance(parent_descriptor, Mapping)
                and parent.get("query_kind") == "issuer_submissions"
                and parent_descriptor.get("query_kind") == "issuer_submissions"
                and all(
                    parent_descriptor.get("descriptor", {}).get(field) == descriptor.get(field)
                    for field in (
                        "entity_id", "security_id", "security_revision_id",
                        "reference_manifest_id", "cik",
                    )
                ),
                "discovery adaptive filing dependency is invalid",
            )
        by_manifest.setdefault(str(manifest_id), []).append(row)
        enrichment_ids.add(task_id)

    for manifest_id, row in manifests.items():
        manifest = row.get("manifest")
        children = by_manifest.get(manifest_id, [])
        selection_stage = row.get("selection_stage")
        allowed = {
            "holding_quotes": {"quote"},
            "initial": {"issuer_submissions", "quote"},
            "filing_documents": {"filing_document"},
        }.get(str(selection_stage))
        require(
            isinstance(manifest, Mapping) and isinstance(envelope, Mapping)
            and isinstance(allowed, set) and row.get("run_id") == run_id
            and row.get("phase") == phase and row.get("provider_reservations") == envelope
            and row.get("request_count") == len(children)
            and set(manifest) == {
                "deferred_reasons", "execution_allowed", "manifest_id", "phase",
                "provider_reservations", "request_descriptors", "run_id",
                "schema_version", "selection_stage", "semantic_hash",
            }
            and manifest.get("manifest_id") == manifest_id
            and manifest.get("run_id") == run_id and manifest.get("phase") == phase
            and manifest.get("selection_stage") == selection_stage
            and manifest.get("provider_reservations") == envelope
            and manifest.get("deferred_reasons") == row.get("deferred_reasons")
            and manifest.get("execution_allowed") is False
            and manifest.get("schema_version") == 1
            and isinstance(manifest.get("request_descriptors"), list)
            and all(child.get("query_kind") in allowed for child in children),
            "discovery adaptive selection manifest is invalid",
        )
        semantic = dict(manifest)
        semantic.pop("manifest_id")
        semantic.pop("semantic_hash")
        content_hash = _semantic_hash(semantic)
        expected_id = str(uuid.uuid5(uuid.UUID(run_id), f"enrichment-selection:{content_hash}"))
        expected_children = sorted(({
            "request_id": child["id"], "descriptor_hash": child["content_hash"],
        } for child in children), key=canonical_json)
        require(
            manifest_id == expected_id and row.get("content_hash") == content_hash
            and manifest.get("semantic_hash") == content_hash
            and sorted(manifest["request_descriptors"], key=canonical_json) == expected_children,
            "discovery adaptive selection semantic lineage is invalid",
        )
        counts = {
            query_kind: sum(child.get("query_kind") == query_kind for child in children)
            for query_kind in _ENRICHMENT_QUERY_CONTRACTS
        }
        require(
            counts["issuer_submissions"] <= envelope["sec_issuer_submissions"]
            and counts["filing_document"] <= envelope["sec_filing_document"]
            and counts["quote"] <= envelope["yahoo_security_quote"],
            "discovery adaptive selection capacity is invalid",
        )

    dynamic_theme_ids = {
        task_id for task_id in dynamic_ids - enrichment_ids
        if tasks[task_id].get("capability_id") == "dynamic_theme_evaluation"
    }
    require(len(dynamic_theme_ids) <= 1,
            "discovery dynamic theme task capacity is invalid")
    episode_rows = receipt.get("theme_episode_revisions", [])
    require(isinstance(episode_rows, list) and len(episode_rows) <= 50,
            "discovery dynamic theme episode evidence is invalid")
    require(
        all(isinstance(row, Mapping) and row.get("task_id") in dynamic_theme_ids
            for row in episode_rows),
        "discovery dynamic theme episode task lineage is invalid",
    )
    for task_id in dynamic_theme_ids:
        task = tasks[task_id]
        result = task.get("result")
        proposals = result.get("proposals") if isinstance(result, Mapping) else None
        require(
            task.get("stage") == "signals" and task.get("provider") == "gdelt"
            and task.get("query_kind") == "theme_search" and task.get("state") == "succeeded"
            and task.get("request_budget") == 1 and task.get("attempt_count") == 1
            and task.get("theme_id") is None
            and isinstance(result, Mapping) and set(result) == {
                "episode_count", "labels_truncated", "proposals", "research_state",
                "requested_labels", "source_ids_truncated",
            }
            and isinstance(proposals, list) and 1 <= len(proposals) <= 50
            and _sorted_unique_strings(result.get("requested_labels"), maximum=200)
            and type(result.get("episode_count")) is int
            and type(result.get("labels_truncated")) is int
            and result["labels_truncated"] >= 0
            and type(result.get("source_ids_truncated")) is int
            and 0 <= result["source_ids_truncated"] <= 2_000,
            "discovery dynamic theme task is invalid",
        )
        labels: list[str] = []
        all_source_ids: set[str] = set()
        eligible_by_theme: dict[str, Mapping[str, object]] = {}
        for proposal in proposals:
            label = proposal.get("label") if isinstance(proposal, Mapping) else None
            fingerprint = proposal.get("fingerprint") if isinstance(proposal, Mapping) else None
            source_ids = proposal.get("source_ids") if isinstance(proposal, Mapping) else None
            missing = proposal.get("missing_reasons") if isinstance(proposal, Mapping) else None
            eligible = proposal.get("eligible") if isinstance(proposal, Mapping) else None
            theme_id = proposal.get("theme_id") if isinstance(proposal, Mapping) else None
            require(
                isinstance(proposal, Mapping) and set(proposal) == {
                    "eligible", "fingerprint", "label", "missing_reasons",
                    "research_state", "source_ids", "theme_id",
                }
                and isinstance(label, str) and 1 <= len(label) <= 200
                and isinstance(fingerprint, str) and fingerprint == theme_fingerprint(label)
                and isinstance(theme_id, str) and theme_id == str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"market-theme:{fingerprint}",
                ))
                and type(eligible) is bool
                and isinstance(missing, list) and len(missing) <= 16
                and len(missing) == len(set(missing))
                and all(isinstance(reason, str) and bool(reason) for reason in missing)
                and eligible == (len(missing) == 0)
                and proposal.get("research_state") == (
                    "observed" if eligible else "unresolved"
                )
                and _sorted_unique_strings(source_ids, maximum=64)
                and bool(source_ids)
                and all(UUID.fullmatch(source_id) is not None for source_id in source_ids),
                "discovery dynamic theme proposal is invalid",
            )
            labels.append(label)
            all_source_ids.update(source_ids)
            if eligible:
                require(theme_id not in eligible_by_theme,
                        "discovery dynamic theme identity is duplicated")
                eligible_by_theme[theme_id] = proposal
        require(labels == sorted(set(labels)),
                "discovery dynamic theme labels are invalid or duplicated")
        selector_hash = sha256(canonical_json({
            "labels": labels,
            "requested_labels": result["requested_labels"],
            "source_ids": sorted(all_source_ids),
        }).encode())
        expected_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:dynamic-theme-evaluation:{run_id}:{selector_hash}",
        ))
        query_hash = sha256(canonical_json({
            "capability_id": "dynamic_theme_evaluation",
            "query": {
                "query": "dynamic-theme-evaluation", "labels": labels,
                "requested_labels": result["requested_labels"],
            },
            "cursor": None, "requested_window": task.get("requested_window"),
            "theme_id": None,
        }).encode())
        dependencies = task.get("dependency_ids")
        task_episodes = [row for row in episode_rows if row.get("task_id") == task_id]
        require(
            task_id == expected_id and task.get("query_hash") == query_hash
            and isinstance(dependencies, list) and bool(dependencies)
            and dependencies == sorted(set(dependencies))
            and set(dependencies) <= (planned_ids | (dynamic_ids - enrichment_ids - dynamic_theme_ids))
            and all(tasks[dependency].get("state") == "succeeded" for dependency in dependencies)
            and result["requested_labels"] == _expected_requested_labels(
                dependencies, tasks
            )
            and result.get("episode_count") == len(eligible_by_theme) == len(task_episodes)
            and result.get("research_state") == (
                "observed" if task_episodes else "unresolved"
            ),
            "discovery dynamic theme semantic lineage is invalid",
        )
        episodes_by_theme = {row.get("theme_id"): row for row in task_episodes}
        require(len(episodes_by_theme) == len(task_episodes),
                "discovery dynamic theme episode identity is duplicated")
        for theme_id, proposal in eligible_by_theme.items():
            row = episodes_by_theme.get(theme_id)
            episode = row.get("episode") if isinstance(row, Mapping) else None
            require(
                isinstance(row, Mapping) and row.get("run_id") == run_id
                and row.get("revision") == 1 and row.get("valid_to") is None
                and row.get("source_ids") == proposal.get("source_ids")
                and isinstance(row.get("content_hash"), str)
                and re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is not None
                and row.get("id") == str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"market-intelligence:theme-episode:{row['content_hash']}",
                ))
                and isinstance(episode, Mapping) and set(episode) == {
                    "coverage_label", "fingerprint", "label", "missing_reasons",
                    "research_state",
                }
                and episode.get("fingerprint") == proposal.get("fingerprint")
                and episode.get("label") == proposal.get("label")
                and episode.get("missing_reasons") == []
                and episode.get("research_state") == "observed"
                and isinstance(episode.get("coverage_label"), str)
                and 1 <= len(episode["coverage_label"]) <= 500,
                "discovery dynamic theme episode lineage is invalid",
            )

    reverse_ids = dynamic_ids - enrichment_ids - dynamic_theme_ids
    require(len(reverse_ids) <= envelope["gdelt_reverse"],
            "discovery reverse task capacity is invalid")
    for task_id in reverse_ids:
        task = tasks[task_id]
        dependencies = task.get("dependency_ids")
        result = task.get("result")
        hypothesis = result.get("hypothesis") if isinstance(result, Mapping) else None
        descriptor = result.get("reverse_descriptor") \
            if isinstance(result, Mapping) else None
        descriptor_keys = {
            "event_id", "hypothesis", "hypothesis_id", "query",
            "selection_task_id", "source_item_ids",
        }
        require(
            isinstance(descriptor, Mapping) and set(descriptor) == descriptor_keys,
            "discovery reverse selection descriptor is invalid",
        )
        expected_hypothesis, expected_selection_task_id = _expected_reverse_descriptor(
            task, descriptor
        )
        source_item_ids = descriptor.get("source_item_ids")
        request_cursor = result.get("request_cursor") if isinstance(result, Mapping) else None
        reverse_theme_id = result.get("theme_id") if isinstance(result, Mapping) else None
        expected_query_hash = sha256(canonical_json({
            "capability_id": "gdelt_theme_search",
            "query": dict(descriptor),
            "cursor": request_cursor,
            "requested_window": task.get("requested_window"),
            "theme_id": reverse_theme_id,
        }).encode())
        require(
            task.get("stage") == "resolve" and task.get("provider") == "gdelt"
            and task.get("capability_id") == "gdelt_theme_search"
            and task.get("query_kind") == "theme_search"
            and task.get("query_hash") == expected_query_hash
            and _valid_request_cursor(
                request_cursor, provider="gdelt", capability_id="gdelt_theme_search"
            )
            and isinstance(dependencies, list) and 1 <= len(dependencies) <= 32
            and dependencies == sorted(set(dependencies))
            and set(dependencies) <= planned_ids
            and all(tasks[dependency].get("state") == "succeeded" for dependency in dependencies)
            and _sorted_unique_strings(source_item_ids, maximum=32)
            and bool(source_item_ids)
            and all(UUID.fullmatch(source_id) is not None for source_id in source_item_ids)
            and descriptor.get("selection_task_id") == expected_selection_task_id
            and task_id == str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"market-intelligence:reverse-discovery-task:{run_id}:"
                f"{expected_selection_task_id}",
            ))
            and descriptor.get("hypothesis") == expected_hypothesis == hypothesis,
            "discovery reverse task lineage is invalid",
        )
    return dynamic_ids


def _verified_source_item(
    source_id: str,
    source_items: Mapping[object, Mapping[str, object]],
    persisted_receipts: Mapping[object, Mapping[str, object]],
    *,
    retrieved_at: datetime | None = None,
    source_provenance: Mapping[object, Mapping[str, object]] | None = None,
    observation_provenance: Mapping[str, object] | None = None,
) -> SourceItem:
    row = source_items.get(source_id)
    require(isinstance(row, Mapping), "discovery adaptive source item is missing")
    metadata = row.get("metadata")
    receipt = persisted_receipts.get(row.get("source_receipt_id"))
    require(
        isinstance(metadata, Mapping) and isinstance(receipt, Mapping)
        and isinstance(row.get("canonical_content"), str)
        and sha256(row["canonical_content"].encode()) == row.get("content_hash"),
        "discovery adaptive source metadata or receipt is missing",
    )
    published = timestamp(row["published_at"]) if row.get("published_at") is not None else None
    effective = timestamp(row["effective_at"]) if row.get("effective_at") is not None else None
    retrieved = retrieved_at or timestamp(receipt.get("retrieved_at"))
    provenance = source_provenance.get(source_id) \
        if isinstance(source_provenance, Mapping) else None
    canonical_url = provenance.get("canonical_item_url") \
        if isinstance(provenance, Mapping) else row.get("canonical_url")
    identifiers = observation_provenance \
        if isinstance(observation_provenance, Mapping) else provenance
    item = SourceItem(
        provider=str(row.get("provider")),
        upstream_item_id=(
            str(row["upstream_item_id"])
            if row.get("upstream_item_id") is not None else None
        ),
        canonical_url=str(canonical_url or ""),
        title=str(row.get("title") or ""),
        summary=str(row.get("normalized_text") or ""),
        canonical_content=str(row.get("canonical_content") or ""),
        content_hash=str(row.get("content_hash") or ""),
        published_at=published,
        effective_at=effective,
        retrieved_at=retrieved,
        authority="verified_source",
        metadata=dict(metadata),
        entity_ids=tuple(sorted(
            str(value).casefold()
            for value in (identifiers.get("entity_ids", [])
                          if isinstance(identifiers, Mapping) else [])
            if isinstance(value, str) and value
        )),
        security_ids=tuple(sorted(
            str(value).upper()
            for value in (identifiers.get("security_ids", [])
                          if isinstance(identifiers, Mapping) else [])
            if isinstance(value, str) and value
        )),
        claim_polarity=_claim_polarity(
            str(row.get("title") or ""),
            str(row.get("normalized_text") or ""),
            metadata,
        ),
    )
    require(evidence_key(item) == source_id,
            "discovery adaptive source identity is inconsistent")
    return item


def _verified_task_observations(
    task_ids: list[str],
    *,
    run_id: str,
    source_items: Mapping[object, Mapping[str, object]],
    persisted_receipts: Mapping[object, Mapping[str, object]],
    source_provenance: Mapping[object, Mapping[str, object]],
    run_items: list[Mapping[str, object]],
    run_provenance: Mapping[object, Mapping[str, object]],
    receipt_id_by_task: Mapping[str, str],
    duplicate_references: list[Mapping[str, object]],
) -> list[tuple[str, SourceItem]]:
    observations: list[tuple[str, SourceItem]] = []
    for task_id in task_ids:
        receipt_id = receipt_id_by_task.get(task_id)
        persisted_receipt = persisted_receipts.get(receipt_id)
        require(isinstance(persisted_receipt, Mapping),
                "discovery adaptive candidate receipt is missing")
        direct = [
            row for row in run_items
            if row.get("run_id") == run_id
            and row.get("source_receipt_id") == receipt_id
            and row.get("disposition") in {"accepted", "near_duplicate"}
        ]
        source_ids = {
            str(row["source_item_id"]) for row in direct
            if isinstance(row.get("source_item_id"), str)
        } | {
            str(row["item_id"]) for row in duplicate_references
            if row.get("receipt_id") == receipt_id
        }
        retrieved_candidates = [
            timestamp(run_provenance[row["id"]].get("retrieved_at"))
            for row in direct
            if isinstance(run_provenance.get(row.get("id")), Mapping)
        ]
        receipt_retrieved = timestamp(persisted_receipt.get("retrieved_at"))
        retrieved_at = max((*retrieved_candidates, receipt_retrieved))
        for source_id in sorted(source_ids):
            canonical_run_item = next((
                row for row in run_items
                if row.get("run_id") == run_id
                and row.get("source_item_id") == source_id
            ), None)
            observation_provenance = (
                run_provenance.get(canonical_run_item.get("id"))
                if isinstance(canonical_run_item, Mapping) else None
            )
            observations.append((task_id, _verified_source_item(
                source_id, source_items, persisted_receipts,
                retrieved_at=retrieved_at,
                source_provenance=source_provenance,
                observation_provenance=(
                    observation_provenance
                    if isinstance(observation_provenance, Mapping) else None
                ),
            )))
    return observations


def _verify_dynamic_theme_semantics(
    receipt: Mapping[str, object],
    *,
    run_id: str,
    tasks: Mapping[str, Mapping[str, object]],
    source_items: Mapping[object, Mapping[str, object]],
    persisted_receipts: Mapping[object, Mapping[str, object]],
    source_provenance: Mapping[object, Mapping[str, object]],
    run_items: list[Mapping[str, object]],
    run_provenance: Mapping[object, Mapping[str, object]],
    receipt_id_by_task: Mapping[str, str],
    duplicate_references: list[Mapping[str, object]],
    candidate_task_ids: list[str],
) -> None:
    episode_rows = receipt.get("theme_episode_revisions", [])
    require(isinstance(episode_rows, list),
            "discovery dynamic theme episode evidence is invalid")
    observations = _verified_task_observations(
        candidate_task_ids,
        run_id=run_id,
        source_items=source_items,
        persisted_receipts=persisted_receipts,
        source_provenance=source_provenance,
        run_items=run_items,
        run_provenance=run_provenance,
        receipt_id_by_task=receipt_id_by_task,
        duplicate_references=duplicate_references,
    )
    selection = select_dynamic_theme_evidence(observations)
    dynamic_tasks = [
        (task_id, task) for task_id, task in tasks.items()
        if task.get("capability_id") == "dynamic_theme_evaluation"
    ]
    require(
        len(dynamic_tasks) == (1 if selection.labels else 0),
        "discovery dynamic theme evidence selection is incomplete",
    )
    for task_id, task in dynamic_tasks:
        result = task["result"]
        proposals = result["proposals"]
        requested_labels = _expected_requested_labels(
            list(selection.dependency_ids), tasks
        )
        expected_sources_by_label = {
            label: sorted(evidence_key(item) for item in selection.evidence_by_label[label])
            for label in selection.labels
        }
        declared_sources_by_label = {
            proposal["label"]: proposal["source_ids"] for proposal in proposals
        }
        require(
            task.get("dependency_ids") == list(selection.dependency_ids)
            and result["requested_labels"] == requested_labels
            and result["labels_truncated"] == selection.labels_truncated
            and result["source_ids_truncated"] == selection.source_ids_truncated
            and declared_sources_by_label == expected_sources_by_label,
            "discovery dynamic theme evidence selection is incomplete",
        )
        verified = {
            evidence_key(item): item
            for label in selection.labels
            for item in selection.evidence_by_label[label]
        }
        coverage_label = "bounded sources: " + ",".join(sorted({
            item.provider for item in verified.values()
        }))
        expected_proposals = []
        eligible = {}
        for label in selection.labels:
            evidence = selection.evidence_by_label[label]
            expected = propose_dynamic_theme(
                label, evidence,
                coverage_label=coverage_label,
                requested_labels=requested_labels,
            )
            expected_row = {
                "eligible": expected.eligible,
                "fingerprint": expected.fingerprint,
                "label": expected.label,
                "missing_reasons": list(expected.missing_reasons),
                "research_state": "observed" if expected.eligible else "unresolved",
                "source_ids": sorted(evidence_key(item) for item in expected.evidence),
                "theme_id": expected.theme_id,
            }
            expected_proposals.append(expected_row)
            if expected.eligible:
                eligible[expected.theme_id] = (expected, expected_row)
        require(
            proposals == expected_proposals
            and result["episode_count"] == len(eligible)
            and result["research_state"] == ("observed" if eligible else "unresolved"),
            "discovery dynamic theme eligibility is not source-derived",
        )
        task_episodes = [row for row in episode_rows if row.get("task_id") == task_id]
        episodes_by_theme = {row.get("theme_id"): row for row in task_episodes}
        require(len(episodes_by_theme) == len(task_episodes) == len(eligible),
                "discovery dynamic theme episode coverage is invalid")
        for theme_id, (proposal, proposal_row) in eligible.items():
            row = episodes_by_theme.get(theme_id)
            require(isinstance(row, Mapping),
                    "discovery dynamic theme episode is missing")
            observed = min(
                item.published_at or item.effective_at or item.retrieved_at
                for item in proposal.evidence
            )
            valid_from = observed.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
            episode = {
                "coverage_label": coverage_label,
                "fingerprint": proposal.fingerprint,
                "label": proposal.label,
                "missing_reasons": [],
                "research_state": "observed",
            }
            semantic = {
                "theme_id": theme_id,
                "revision": 1,
                "episode": episode,
                "source_ids": proposal_row["source_ids"],
                "valid_from": valid_from,
                "valid_to": None,
            }
            content_hash = sha256(canonical_json(semantic).encode())
            require(
                row.get("run_id") == run_id and row.get("task_id") == task_id
                and row.get("theme_id") == theme_id and row.get("revision") == 1
                and row.get("episode") == episode
                and row.get("source_ids") == proposal_row["source_ids"]
                and timestamp(row.get("valid_from")) == timestamp(valid_from)
                and row.get("valid_to") is None
                and row.get("content_hash") == content_hash
                and row.get("id") == str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"market-intelligence:theme-episode:{content_hash}",
                )),
                "discovery dynamic theme episode semantic lineage is invalid",
            )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    required_capability_ids: tuple[str, ...] = ()
    optional_failures: tuple[str, ...] = ()


def _sorted_unique_strings(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum
        and all(isinstance(row, str) and bool(row) for row in value)
        and value == sorted(set(value))
    )


def _fixed_scores(value: Mapping[object, object]) -> bool:
    for raw in value.values():
        if not isinstance(raw, str) or re.fullmatch(r"(?:0|1)\.[0-9]{6}", raw) is None:
            return False
        try:
            parsed = Decimal(raw)
        except InvalidOperation:
            return False
        if parsed < 0 or parsed > 1:
            return False
    return True


def _fixed_sum(values: Mapping[object, object], raw_total: object) -> bool:
    if not isinstance(raw_total, str) or re.fullmatch(r"[0-4]\.[0-9]{6}", raw_total) is None:
        return False
    try:
        return Decimal(raw_total) == sum(
            (Decimal(str(raw)) for raw in values.values()), Decimal("0"),
        )
    except InvalidOperation:
        return False


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
        and set(packet) == {
            "action_candidates", "contract_version", "coverage", "evidence",
            "execution_allowed", "limitations", "observed_at", "omissions",
            "policy_version", "research_candidates", "run_id",
        }
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
    payload_coverage = payload.get("coverage")
    collector_drops = (
        payload_coverage.get("collector_drops", [])
        if isinstance(payload_coverage, Mapping) else None
    )
    persisted_coverage = (
        {key: value for key, value in payload_coverage.items()
         if key != "collector_drops"}
        if isinstance(payload_coverage, Mapping) else None
    )
    require(
        isinstance(coverage, Mapping) and coverage.get("complete_market_coverage") is False
        and isinstance(payload_coverage, Mapping)
        and (payload_coverage == coverage or persisted_coverage == coverage)
        and isinstance(collector_drops, list) and len(collector_drops) <= 3000
        and all(
            isinstance(row, Mapping)
            and set(row) in ({"candidate_key", "item_id", "kind", "reason", "stage"},
                             {"candidate_key", "item_id", "kind", "reason"})
            and isinstance(row.get("candidate_key"), str)
            and (row.get("item_id") is None or (
                isinstance(row.get("item_id"), str)
                and UUID.fullmatch(row["item_id"]) is not None
            ))
            and isinstance(row.get("kind"), str) and bool(row.get("kind"))
            and isinstance(row.get("reason"), str) and bool(row.get("reason"))
            and ("stage" not in row or (
                isinstance(row.get("stage"), str) and bool(row.get("stage"))
            ))
            for row in collector_drops
        ),
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
    planned_id_set = set(planned_ids)
    require(
        len(task_rows) <= 100 and len(tasks) == len(task_rows)
        and planned_id_set <= set(tasks)
        and all(UUID.fullmatch(task_id) is not None for task_id in tasks)
        and all(task.get("run_id") == run_id for task in tasks.values()),
        "discovery required task evidence is incomplete",
    )
    terminal_states = {"succeeded", "failed", "deferred", "uncertain"}
    require(all(task.get("state") in terminal_states for task in tasks.values()),
            "discovery task plan is not terminal")
    dynamic_task_ids = _authorized_dynamic_task_ids(
        receipt, run_id=run_id, phase=intelligence.get("phase"), tasks=tasks,
        planned_ids=planned_id_set,
    )
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
        and source_plan.get("reference_version") in {
            "sec:unresolved", manifest.get("reference_version"),
        }
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
    try:
        reference_snapshot = reference_snapshot_from_rows(
            manifest,
            [revisions_by_id[row["security_revision_id"]]
             for row in sorted(selected_memberships, key=lambda value: value["ordinal"])],
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("discovery reference hydration is invalid") from error

    persisted_receipt_rows = receipt.get("source_receipts", [])
    persisted_receipts = {row.get("id"): row for row in persisted_receipt_rows
                          if isinstance(row, Mapping)}
    persisted_request_window = intelligence.get("request_window")
    global_receipt_window = (
        {
            "start": persisted_request_window.get("start"),
            "end": persisted_request_window.get("end"),
        }
        if isinstance(persisted_request_window, Mapping) else None
    )
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
    duplicate_references = coverage.get("duplicate_references", [])
    require(
        isinstance(duplicate_references, list)
        and all(
            isinstance(row, Mapping)
            and set(row) == {"item_id", "receipt_id", "reason"}
            and isinstance(row.get("item_id"), str)
            and UUID.fullmatch(row["item_id"]) is not None
            and isinstance(row.get("receipt_id"), str)
            and UUID.fullmatch(row["receipt_id"]) is not None
            and row.get("reason") in {"same_content_hash", "same_upstream_item_id"}
            for row in duplicate_references
        )
        and len({
            (row["item_id"], row["receipt_id"])
            for row in duplicate_references
        }) == len(duplicate_references),
        "discovery duplicate receipt references are invalid or duplicated",
    )
    require(
        {(row["item_id"], row["reason"]) for row in duplicate_references}
        == {
            (row["item_id"], row["reason"])
            for row in collector_drops
            if row.get("kind") == "source_item"
            and row.get("stage") == "deduplication"
        },
        "discovery duplicate receipt drops do not reconcile",
    )
    duplicate_references_by_receipt: dict[str, list[Mapping[str, object]]] = {}
    for row in duplicate_references:
        duplicate_references_by_receipt.setdefault(str(row["receipt_id"]), []).append(row)
    completion_receipts = payload.get("receipts")
    require(isinstance(completion_receipts, list),
            "discovery required parsed receipt evidence is missing")
    completion_by_id = {
        row.get("id"): row for row in completion_receipts
        if isinstance(row, Mapping)
    }
    require(
        isinstance(persisted_receipt_rows, list)
        and len(persisted_receipts) == len(persisted_receipt_rows)
        and len(completion_by_id) == len(completion_receipts),
        "discovery source receipt identities are invalid or duplicated",
    )
    required_task_ids = {str(row["task_id"]) for row in required_tasks}
    verified_receipt_ids: set[str] = set()
    required_receipt_ids: set[str] = set()
    receipt_id_by_task: dict[str, str] = {}
    for task_id in tasks:
        task = tasks[task_id]
        capability_id = task.get("capability_id")
        if capability_id == "dynamic_theme_evaluation":
            continue
        capability = registry.get(str(capability_id))
        expected_stage = ({
            "universe": "reference",
            "screener": "screen",
            "quote": "quote",
            "issuer_submissions": "enrich",
            "filing_document": "enrich",
        }.get(capability.query_kind, "signals") if capability is not None else None)
        required = task_id in required_task_ids
        dynamic = task_id not in planned_id_set
        label = "required capability" if required else (
            "adaptive capability" if dynamic else "planned capability"
        )
        require(
            capability is not None
            and capability.enabled and capability.health in {"enabled", "degraded"}
            and task.get("provider") == capability.provider
            and task.get("query_kind") == capability.query_kind
            and (dynamic or task.get("stage") == expected_stage)
            and (not required or task.get("state") == "succeeded"),
            f"discovery {label} task does not match its registry or success state",
        )
        if capability_id == reference_id:
            continue
        if task.get("state") != "succeeded":
            continue
        result = task.get("result")
        checkpoint = result.get("checkpoint") if isinstance(result, Mapping) else None
        parsed = checkpoint.get("receipt") if isinstance(checkpoint, Mapping) else None
        receipt_id = parsed.get("source_receipt_id") if isinstance(parsed, Mapping) else None
        stored = persisted_receipts.get(receipt_id)
        expected_planned_window = None
        expected_planned_query_hash = None
        if task_id in planned_id_set and isinstance(result, Mapping) \
                and capability is not None:
            request_cursor = result.get("request_cursor")
            theme_id = result.get("theme_id")
            planned_query = _planned_query(capability, theme_id)
            try:
                cursor = SourceCursor.from_mapping(request_cursor)  # type: ignore[arg-type]
                planned_task = DiscoveryTask(
                    task_id=task_id,
                    stage=task.get("stage"),  # type: ignore[arg-type]
                    provider=capability.provider,
                    capability_id=capability.capability_id,
                    query_kind=capability.query_kind,
                    theme_id=theme_id if isinstance(theme_id, str) else None,
                    query=planned_query or {},
                    window=persisted_request_window,  # type: ignore[arg-type]
                    dependencies=tuple(task.get("dependency_ids", ())),
                    max_attempts=int(task.get("request_budget", 0)),
                    requires_credential=capability.required_credential is not None,
                )
                expected_collection_window = _collection_window_for_task(
                    planned_task, cursor,
                )
                expected_planned_window = {
                    "start": _pipeline_timestamp(expected_collection_window.start),
                    "end": _pipeline_timestamp(expected_collection_window.end),
                }
                expected_planned_query_hash = sha256(canonical_json({
                    "capability_id": capability.capability_id,
                    "query": dict(planned_query or {}),
                    "cursor": cursor.to_mapping(),
                    "requested_window": expected_planned_window,
                    "theme_id": planned_task.theme_id,
                }).encode())
                if cursor.to_mapping() != request_cursor or planned_query is None:
                    expected_planned_window = None
                    expected_planned_query_hash = None
            except (TypeError, ValueError):
                expected_planned_window = None
                expected_planned_query_hash = None
        coverage_status = parsed.get("metadata", {}).get("coverage_status") \
            if isinstance(parsed, Mapping) and isinstance(parsed.get("metadata"), Mapping) else None
        expected_receipt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:receipt:{run_id}:{task.get('id')}",
        ))
        require(
            isinstance(receipt_id, str) and receipt_id == expected_receipt_id
            and receipt_id not in verified_receipt_ids
            and isinstance(checkpoint, Mapping)
            and checkpoint.get("cache_key") == parsed.get("cache_key")
            and isinstance(stored, Mapping)
            and isinstance(completion_by_id.get(receipt_id), Mapping)
            and parsed.get("provider") == task.get("provider") == stored.get("provider")
            and parsed.get("requested_window") == task.get("requested_window")
            and (
                task_id not in planned_id_set
                or task.get("requested_window") == expected_planned_window
                and task.get("query_hash") == expected_planned_query_hash
            )
            and stored.get("requested_window") == global_receipt_window
            and isinstance(parsed.get("metadata"), Mapping)
            and parsed["metadata"].get("capability_id") == task.get("capability_id")
            and parsed.get("status") in {"succeeded", "cache_hit"}
            and stored.get("status") in {"succeeded", "cache_hit"}
            and coverage_status in {"success_empty", "success_nonempty"}
            and all(parsed.get(key) == stored.get(db_key) for key, db_key in (
                ("provider", "provider"), ("reservation_id", "reservation_id"),
                ("cache_key", "cache_key"),
                ("request_cost", "request_cost"), ("returned_count", "returned_count"),
                ("accepted_count", "accepted_count"), ("duplicate_count", "duplicate_count"),
                ("dropped_count", "dropped_count"), ("response_hash", "response_hash"),
            ))
            and timestamp(parsed.get("retrieved_at")) == timestamp(stored.get("retrieved_at"))
            and (
                parsed.get("expires_at") is None and stored.get("expires_at") is None
                or parsed.get("expires_at") is not None and stored.get("expires_at") is not None
                and timestamp(parsed["expires_at"]) == timestamp(stored["expires_at"])
            )
            and all(completion_by_id[receipt_id].get(key) == stored.get(db_key)
                    for key, db_key in (
                        ("id", "id"), ("reservation_id", "reservation_id"),
                        ("status", "status"), ("cache_key", "cache_key"),
                        ("requested_window", "requested_window"),
                        ("request_cost", "request_cost"),
                        ("upstream_remaining", "upstream_remaining"),
                        ("returned_count", "returned_count"),
                        ("accepted_count", "accepted_count"),
                        ("duplicate_count", "duplicate_count"),
                        ("dropped_count", "dropped_count"),
                        ("response_hash", "response_hash"),
                    ))
            and timestamp(completion_by_id[receipt_id].get("retrieved_at"))
                == timestamp(stored.get("retrieved_at"))
            and (
                completion_by_id[receipt_id].get("expires_at") is None
                and stored.get("expires_at") is None
                or completion_by_id[receipt_id].get("expires_at") is not None
                and stored.get("expires_at") is not None
                and timestamp(completion_by_id[receipt_id]["expires_at"])
                    == timestamp(stored["expires_at"])
            )
            and completion_by_id[receipt_id].get("error") == stored.get("error")
            and stored.get("run_id") == run_id
            and stored.get("reservation_id") in reservations
            and reservations[stored["reservation_id"]].get("run_id") == run_id
            and reservations[stored["reservation_id"]].get("provider") == stored.get("provider"),
            f"discovery {label} lacks a parsed receipt-backed success",
        )
        verified_receipt_ids.add(receipt_id)
        receipt_id_by_task[task_id] = receipt_id
        if required:
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
        duplicate_item_references = duplicate_references_by_receipt.get(receipt_id, [])
        if coverage_status == "success_empty":
            require(not persisted_run_items and not duplicate_item_references,
                    "discovery success_empty receipt unexpectedly has saved items")
        else:
            require(persisted_run_items or duplicate_item_references,
                    "discovery success_nonempty receipt lacks saved item provenance")
            for duplicate_reference in duplicate_item_references:
                duplicate_item_id = duplicate_reference["item_id"]
                duplicate_item = source_items.get(duplicate_item_id)
                canonical_run_items = [
                    row for row in run_items
                    if row.get("run_id") == run_id
                    and row.get("source_item_id") == duplicate_item_id
                ]
                require(
                    isinstance(duplicate_item, Mapping)
                    and duplicate_item.get("source_receipt_id") in persisted_receipts
                    and isinstance(duplicate_item.get("content_hash"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", duplicate_item["content_hash"])
                    and duplicate_item_id == str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"market-source:{duplicate_item['content_hash']}",
                    ))
                    and canonical_run_items
                    and all(
                        isinstance(run_provenance.get(row.get("id")), Mapping)
                        and run_provenance[row["id"]].get("run_id") == run_id
                        and run_provenance[row["id"]].get("source_item_id") == duplicate_item_id
                        and str(run_provenance[row["id"]].get("request_url", "")).startswith(
                            "https://"
                        )
                        for row in canonical_run_items
                    ),
                    "discovery duplicate receipt item provenance is invalid",
                )
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
                    and isinstance(item.get("canonical_content"), str)
                    and sha256(item["canonical_content"].encode()) == item["content_hash"]
                    and item_id == str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"market-source:{item['content_hash']}",
                    ))
                    and item.get("source_receipt_id") in persisted_receipts
                    and item.get("provider") == stored.get("provider")
                    and run_item.get("run_id") == run_id
                    and run_item.get("disposition") in {"accepted", "near_duplicate"}
                    and (
                        (run_item.get("disposition") == "accepted"
                         and run_item.get("drop_reason") is None)
                        or (run_item.get("disposition") == "near_duplicate"
                            and isinstance(run_item.get("drop_reason"), str)
                            and bool(run_item.get("drop_reason")))
                    )
                    and item_provenance.get("provider") == item.get("provider")
                    and str(item.get("canonical_url", "")).startswith("https://")
                    and str(item_provenance.get("canonical_item_url", "")).startswith(
                        "https://"
                    )
                    and str(item_provenance.get("request_url", "")).startswith("https://")
                    and scoped_provenance.get("run_id") == run_id
                    and scoped_provenance.get("source_item_id") == item_id
                    and scoped_provenance.get("source_receipt_id") == receipt_id
                    and scoped_provenance.get("provider") == item.get("provider")
                    and str(scoped_provenance.get("request_url", "")).startswith("https://"),
                    "discovery success_nonempty saved item provenance is invalid",
                )

    require(
        required_receipt_ids <= verified_receipt_ids
        and len(required_receipt_ids) == len(required_task_ids) - 1,
        "discovery required capability receipt coverage is incomplete",
    )

    initial_task_ids = [
        task_id for task_id in planned_ids
        if task_id in receipt_id_by_task and tasks[task_id].get("stage") != "reference"
    ]
    initial_observations = _verified_task_observations(
        initial_task_ids,
        run_id=run_id,
        source_items=source_items,
        persisted_receipts=persisted_receipts,
        source_provenance=source_provenance,
        run_items=run_items,
        run_provenance=run_provenance,
        receipt_id_by_task=receipt_id_by_task,
        duplicate_references=duplicate_references,
    )
    phase = str(intelligence.get("phase"))
    reservation_plan = intelligence.get("reservation_plan")
    plan_reservation_rows = (
        reservation_plan.get("reservations")
        if isinstance(reservation_plan, Mapping) else None
    )
    request_window = intelligence.get("request_window")
    adaptive_envelope = _ENRICHMENT_PHASE_ENVELOPES.get(phase)
    reverse_capability = registry.get("gdelt_theme_search")
    valid_plan_reservations = (
        isinstance(reservation_plan, Mapping)
        and set(reservation_plan) == {"reservations"}
        and isinstance(plan_reservation_rows, list)
        and bool(plan_reservation_rows)
        and all(
            isinstance(row, Mapping)
            and set(row) == {"id", "provider", "requests", "cache_keys"}
            and isinstance(row.get("id"), str)
            and UUID.fullmatch(row["id"]) is not None
            and isinstance(row.get("provider"), str) and bool(row["provider"])
            and type(row.get("requests")) is int and row["requests"] > 0
            and isinstance(row.get("cache_keys"), list)
            and all(
                isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                for key in row["cache_keys"]
            )
            for row in plan_reservation_rows
        )
    )
    plan_reservations_by_provider = {
        str(row["provider"]): row for row in plan_reservation_rows
    } if valid_plan_reservations else {}
    plan_reservation_ids = {
        str(row["id"]) for row in plan_reservation_rows
    } if valid_plan_reservations else set()
    current_reservation_ids = {
        str(row_id) for row_id, row in reservations.items()
        if row.get("run_id") == run_id
    }
    valid_plan_reservations = (
        valid_plan_reservations
        and len(plan_reservations_by_provider) == len(plan_reservation_rows)
        and plan_reservation_ids == current_reservation_ids
        and all(
            reservations[row["id"]].get("provider") == row["provider"]
            and reservations[row["id"]].get("reserved_requests") == row["requests"]
            and reservations[row["id"]].get("market_date") == intelligence.get("market_date")
            and reservations[row["id"]].get("phase") == phase
            for row in plan_reservation_rows
        )
    )
    static_reverse_calls = sum(
        tasks[task_id].get("capability_id") == "gdelt_theme_search"
        for task_id in planned_ids
        if tasks[task_id].get("stage") != "reference"
    )
    static_gdelt_calls = sum(
        tasks[task_id].get("provider") == "gdelt"
        for task_id in planned_ids
        if tasks[task_id].get("stage") != "reference"
    )
    required_gdelt_task_ids = {
        str(row["task_id"])
        for row in required_tasks
        if row.get("capability_id") == "gdelt_theme_search"
    }
    planned_gdelt_task_ids = {
        task_id for task_id in planned_ids
        if tasks[task_id].get("provider") == "gdelt"
        and tasks[task_id].get("stage") != "reference"
    }
    gdelt_plan = plan_reservations_by_provider.get("gdelt")
    gdelt_reserved_requests = (
        gdelt_plan.get("requests") if isinstance(gdelt_plan, Mapping) else None
    )
    valid_request_window = (
        isinstance(request_window, Mapping)
        and set(request_window) == {"start", "end", "timezone", "market_date", "phase"}
        and all(isinstance(value, str) for value in request_window.values())
        and request_window.get("timezone") == "America/Chicago"
        and request_window.get("market_date") == intelligence.get("market_date")
        and request_window.get("phase") == phase
    )
    if valid_request_window:
        try:
            valid_request_window = (
                timestamp(request_window["start"]) < timestamp(request_window["end"])
            )
        except RuntimeError:
            valid_request_window = False
    adaptive_budget = int(policy.adaptive_enrichment_budget.get(phase, -1))
    planned_task_ceiling = max(0, 100 - adaptive_budget)
    require(
        valid_request_window and valid_plan_reservations
        and isinstance(adaptive_envelope, Mapping)
        and reverse_capability is not None
        and planned_gdelt_task_ids == required_gdelt_task_ids
        and adaptive_budget >= 0
        and len(planned_ids) <= planned_task_ceiling
        and type(gdelt_reserved_requests) is int
        and gdelt_reserved_requests
            == static_gdelt_calls + int(adaptive_envelope["gdelt_reverse"])
        and adaptive_budget == sum(int(value) for value in adaptive_envelope.values()),
        "discovery reverse selection inputs are invalid",
    )
    reverse_capacity = min(
        adaptive_budget,
        int(adaptive_envelope["gdelt_reverse"]),
        max(0, reverse_capability.max_requests_per_run - static_reverse_calls),
        max(0, 100 - 1 - planned_task_ceiling),
    )
    expected_reverse = select_reverse_discovery_tasks(
        tuple(initial_observations), reference_snapshot, max_tasks=reverse_capacity,
    )
    expected_reverse_by_id: dict[str, tuple[object, dict[str, object]]] = {}
    for selection in expected_reverse:
        row = selection.task
        hypothesis = {
            "adverse_path": row.adverse_path,
            "direction": row.direction,
            "evidence_requirement": row.evidence_requirement,
            "exposure_supported": False,
            "geography": row.geography,
            "horizon": row.horizon,
            "invalidation_rule": row.invalidation_rule,
            "role": row.role,
            "status": "hypothesis",
        }
        descriptor = {
            "event_id": row.event_id,
            "hypothesis": hypothesis,
            "hypothesis_id": row.hypothesis_id,
            "query": row.query_text,
            "selection_task_id": row.task_id,
            "source_item_ids": list(row.dependency_ids)[:32],
        }
        expected_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:reverse-discovery-task:{run_id}:{row.task_id}",
        ))
        expected_reverse_by_id[expected_id] = (selection, descriptor)
    actual_reverse_ids = {
        task_id for task_id in dynamic_task_ids
        if tasks[task_id].get("stage") == "resolve"
    }
    require(
        actual_reverse_ids == set(expected_reverse_by_id),
        "discovery reverse evidence selection is incomplete",
    )
    for task_id, (selection, descriptor) in expected_reverse_by_id.items():
        task = tasks[task_id]
        result = task.get("result")
        request_cursor = result.get("request_cursor") if isinstance(result, Mapping) else None
        try:
            cursor = SourceCursor.from_mapping(request_cursor)  # type: ignore[arg-type]
            expected_task = DiscoveryTask(
                task_id=task_id,
                stage="resolve",
                provider=selection.task.provider,
                capability_id=selection.task.capability_id,
                query_kind=selection.task.query_kind,  # type: ignore[arg-type]
                theme_id=selection.task.theme_id,
                query=descriptor,
                window=request_window,
                dependencies=selection.dependency_task_ids,
                max_attempts=selection.task.max_attempts,
                requires_credential=False,
            )
            expected_window = _collection_window_for_task(expected_task, cursor)
            expected_requested_window = {
                "start": _pipeline_timestamp(expected_window.start),
                "end": _pipeline_timestamp(expected_window.end),
            }
        except (TypeError, ValueError):
            expected_requested_window = None
        require(
            task.get("dependency_ids") == list(selection.dependency_task_ids)
            and task.get("requested_window") == expected_requested_window
            and isinstance(result, Mapping)
            and result.get("reverse_descriptor") == descriptor,
            "discovery reverse evidence selection is inconsistent",
        )

    for task_id, task in tasks.items():
        if task.get("capability_id") != "dynamic_theme_evaluation":
            continue
        dependencies = task["dependency_ids"]
        dependency_receipts = {
            receipt_id_by_task[dependency] for dependency in dependencies
            if dependency in receipt_id_by_task
        }
        require(len(dependency_receipts) == len(dependencies),
                "discovery dynamic theme source tasks lack verified receipts")
        dynamic_source_ids = {
            source_id
            for proposal in task["result"]["proposals"]
            for source_id in proposal["source_ids"]
        }
        for source_id in dynamic_source_ids:
            origin_receipts = {
                str(row.get("source_receipt_id")) for row in run_items
                if row.get("run_id") == run_id and row.get("source_item_id") == source_id
                and row.get("disposition") in {"accepted", "near_duplicate"}
            } | {
                str(row.get("receipt_id")) for row in duplicate_references
                if row.get("item_id") == source_id
            }
            require(
                source_id in source_items
                and bool(origin_receipts & dependency_receipts),
                "discovery dynamic theme source lineage is invalid",
            )

    _verify_dynamic_theme_semantics(
        receipt,
        run_id=run_id,
        tasks=tasks,
        source_items=source_items,
        persisted_receipts=persisted_receipts,
        source_provenance=source_provenance,
        run_items=run_items,
        run_provenance=run_provenance,
        receipt_id_by_task=receipt_id_by_task,
        duplicate_references=duplicate_references,
        candidate_task_ids=[
            *initial_task_ids,
            *(task_id for task_id in expected_reverse_by_id
              if task_id in receipt_id_by_task),
        ],
    )
    for task_id, task in tasks.items():
        if task_id in planned_id_set or task.get("stage") != "resolve":
            continue
        result = task.get("result")
        descriptor = result.get("reverse_descriptor") if isinstance(result, Mapping) else None
        if not isinstance(descriptor, Mapping):
            continue
        dependencies = task["dependency_ids"]
        dependency_receipts = {
            receipt_id_by_task[dependency] for dependency in dependencies
            if dependency in receipt_id_by_task
        }
        reverse_source_ids = descriptor["source_item_ids"]
        require(len(dependency_receipts) == len(dependencies),
                "discovery reverse source tasks lack verified receipts")
        reverse_items = []
        for source_id in reverse_source_ids:
            origin_receipts = {
                str(row.get("source_receipt_id")) for row in run_items
                if row.get("run_id") == run_id and row.get("source_item_id") == source_id
                and row.get("disposition") in {"accepted", "near_duplicate"}
            } | {
                str(row.get("receipt_id")) for row in duplicate_references
                if row.get("item_id") == source_id
            }
            require(bool(origin_receipts & dependency_receipts),
                    "discovery reverse source lineage is invalid")
            reverse_items.append(_verified_source_item(
                source_id, source_items, persisted_receipts,
                source_provenance=source_provenance,
            ))
        events = detect_events(tuple(reverse_items), load_theme_taxonomy())
        matching_events = [
            event for event in events
            if event.event_id == descriptor.get("event_id")
            and sorted(evidence_key(item) for item in event.evidence)
                == reverse_source_ids
        ]
        require(len(matching_events) == 1,
                "discovery reverse event is not derived from verified sources")

    packet_evidence = packet.get("evidence")
    research = packet.get("research_candidates")
    actions = packet.get("action_candidates")
    require(isinstance(packet_evidence, list) and isinstance(research, list)
            and isinstance(actions, list), "discovery research/action lanes are invalid")
    evidence_by_id = {row.get("item_id"): row for row in packet_evidence
                      if isinstance(row, Mapping)}
    require(len(evidence_by_id) == len(packet_evidence),
            "discovery packet evidence identities are invalid or duplicated")
    research_by_key: dict[str, Mapping[str, object]] = {}
    referenced_evidence_ids: set[str] = set()
    for candidate in research:
        require(isinstance(candidate, Mapping), "discovery research candidate is invalid")
        require(set(candidate) == {
            "adverse_paths", "candidate_hash", "candidate_key", "entity_id",
            "event_ids", "evidence", "exposure_fact_ids", "limitations",
            "priority_components", "priority_score", "research_state", "roles",
            "security_id", "suitability", "theme_ids", "ticker",
        }, "discovery research candidate shape is invalid")
        suitability = candidate.get("suitability")
        require(isinstance(suitability, Mapping), "discovery suitability lineage is missing")
        require(set(suitability) == {
            "component_scores", "evaluation_hash", "lineage", "missing_reasons",
            "state", "veto_reasons",
        }, "discovery suitability shape is invalid")
        suitability_body = {key: value for key, value in suitability.items()
                            if key != "evaluation_hash"}
        candidate_body = {key: value for key, value in candidate.items()
                          if key != "candidate_hash"}
        key = candidate.get("candidate_key")
        lineage = suitability.get("lineage")
        refs = candidate.get("evidence")
        component_scores = suitability.get("component_scores")
        priority_components = candidate.get("priority_components")
        require(
            isinstance(key, str) and key not in research_by_key
            and suitability.get("evaluation_hash") == sha256(canonical_json(suitability_body).encode())
            and candidate.get("candidate_hash") == sha256(canonical_json(candidate_body).encode())
            and isinstance(lineage, Mapping) and set(lineage) == {
                "cash_revision", "evidence_receipt_ids", "observed_at",
                "policy_version", "portfolio_revision", "quote_as_of",
                "quote_expires_at", "quote_receipt_id", "reference_expires_at",
                "reference_manifest_id", "reference_revision", "run_id",
                "security_revision_id",
            }
            and lineage.get("run_id") == run_id
            and lineage.get("observed_at") == packet.get("observed_at")
            and lineage.get("policy_version") == packet.get("policy_version")
            and (
                (candidate.get("security_id") is None
                 and lineage.get("reference_manifest_id") is None
                 and lineage.get("reference_revision") is None
                 and lineage.get("security_revision_id") is None)
                or (isinstance(candidate.get("security_id"), str)
                    and lineage.get("reference_manifest_id") == manifest_id
                    and lineage.get("reference_revision") == manifest.get("revision"))
            )
            and isinstance(refs, list) and 1 <= len(refs) <= 8
            and isinstance(component_scores, Mapping)
            and set(component_scores) == {
                "concentration_penalty", "duplication_penalty", "liquidity",
                "portfolio_relevance",
            }
            and isinstance(priority_components, Mapping)
            and set(priority_components) == {
                "authority_corroboration", "exposure", "materiality", "recency",
            }
            and _fixed_scores(component_scores)
            and _fixed_scores(priority_components)
            and _fixed_sum(priority_components, candidate.get("priority_score")),
            "discovery research suitability hash or lineage is invalid",
        )
        receipt_ids = lineage.get("evidence_receipt_ids")
        require(isinstance(receipt_ids, Mapping),
                "discovery research receipt lineage is invalid")
        require(
            candidate.get("research_state") in {
                "unresolved", "resolved", "exposure_supported", "analysis_ready",
            }
            and suitability.get("state") in {"unknown", "vetoed", "eligible"}
            and not (
                candidate.get("research_state") == "analysis_ready"
                and suitability.get("state") == "eligible"
            )
            and isinstance(suitability.get("missing_reasons"), list)
            and isinstance(suitability.get("veto_reasons"), list)
            and _sorted_unique_strings(suitability["missing_reasons"], maximum=32)
            and _sorted_unique_strings(suitability["veto_reasons"], maximum=32)
            and _sorted_unique_strings(candidate.get("adverse_paths"), maximum=32)
            and _sorted_unique_strings(candidate.get("event_ids"), maximum=32)
            and _sorted_unique_strings(candidate.get("exposure_fact_ids"), maximum=64)
            and _sorted_unique_strings(candidate.get("limitations"), maximum=64)
            and _sorted_unique_strings(candidate.get("roles"), maximum=32)
            and _sorted_unique_strings(candidate.get("theme_ids"), maximum=32)
            and (
                suitability.get("state") == "unknown"
                and bool(suitability.get("missing_reasons"))
                or suitability.get("state") == "vetoed"
                and bool(suitability.get("veto_reasons"))
                or suitability.get("state") == "eligible"
                and not suitability.get("missing_reasons")
                and not suitability.get("veto_reasons")
            )
            and (
                candidate.get("research_state") == "unresolved"
                and candidate.get("security_id") is None
                and candidate.get("ticker") is None
                and not candidate.get("exposure_fact_ids")
                and all(isinstance(ref, Mapping)
                        and ref.get("relationship_eligible") is False for ref in refs)
                or candidate.get("research_state") in {
                    "resolved", "exposure_supported", "analysis_ready",
                }
                and isinstance(candidate.get("security_id"), str)
                and isinstance(candidate.get("ticker"), str)
            )
            and (
                candidate.get("research_state") != "resolved"
                or not candidate.get("exposure_fact_ids")
            )
            and (
                candidate.get("research_state") not in {"exposure_supported", "analysis_ready"}
                or bool(candidate.get("exposure_fact_ids"))
                and any(isinstance(ref, Mapping)
                        and ref.get("relationship_eligible") is True for ref in refs)
            ),
            "discovery research suitability semantics are invalid",
        )
        for ref in refs:
            item_id = ref.get("item_id") if isinstance(ref, Mapping) else None
            evidence = evidence_by_id.get(item_id)
            item = source_items.get(item_id)
            current_run_item = next((
                row for row in run_items
                if row.get("run_id") == run_id and row.get("source_item_id") == item_id
                and row.get("disposition") in {"accepted", "near_duplicate"}
            ), None)
            current_receipt_id = (
                current_run_item.get("source_receipt_id")
                if isinstance(current_run_item, Mapping) else None
            )
            require(
                isinstance(ref, Mapping) and set(ref) == {
                    "claim_type", "item_id", "relationship_eligible", "role",
                }
                and isinstance(ref.get("claim_type"), str)
                and ref.get("role") in {"supporting", "opposing"}
                and type(ref.get("relationship_eligible")) is bool
                and isinstance(evidence, Mapping) and set(evidence) == {
                    "authority", "canonical_url", "claim_type", "content_hash",
                    "effective_at", "item_id", "normalized_text", "published_at",
                    "reporting_at", "retrieved_at", "source_identity",
                }
                and isinstance(item, Mapping) and isinstance(current_run_item, Mapping)
                and evidence.get("content_hash") == item.get("content_hash")
                and isinstance(evidence.get("source_identity"), Mapping)
                and set(evidence["source_identity"]) == {
                    "provider", "receipt_id", "upstream_item_id",
                }
                and evidence["source_identity"].get("provider") == item.get("provider")
                and evidence["source_identity"].get("upstream_item_id") == item.get("upstream_item_id")
                and evidence["source_identity"].get("receipt_id") == receipt_ids.get(item_id)
                and receipt_ids.get(item_id) == current_receipt_id
                and current_receipt_id in verified_receipt_ids
                and item.get("source_receipt_id") in persisted_receipts,
                "discovery research source lineage is invalid",
            )
            referenced_evidence_ids.add(str(item_id))
        research_by_key[key] = candidate
    require(referenced_evidence_ids == set(evidence_by_id),
            "discovery packet evidence membership is invalid")
    # The protected V1 schema has no issuer-valuation authority.  Market-wide
    # research is useful, but no rehashed packet can promote it to action.
    require(not actions, "discovery research-to-action promotion is invalid")

    optional_failures = tuple(sorted(
        f"{row.get('capability_id')}:{row.get('state')}"
        for row in task_rows
        if isinstance(row, Mapping)
        and row.get("id") not in required_task_ids
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
    """Bind protected backend readbacks to candidate bytes and one exact artifact."""
    names = FUNCTIONS
    rows = record.get("component_readbacks")
    require(isinstance(rows, list) and [row.get("component") for row in rows] == list(names),
            "all protected backend component readbacks are required")
    evidence = record.get("backend_evidence_artifact")
    require(isinstance(evidence, Mapping) and set(evidence) == {
        "artifact_id", "name", "digest", "manifest_sha256", "recovery_metadata_sha256"
    } and type(evidence.get("artifact_id")) is int and evidence["artifact_id"] > 0
        and evidence.get("name") == f"backend-component-evidence-{record.get('release_workflow_run_id')}-{record.get('release_workflow_run_attempt')}"
        and isinstance(evidence.get("digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["digest"])
        and all(isinstance(evidence.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", evidence[key])
                for key in ("manifest_sha256", "recovery_metadata_sha256")),
        "protected backend evidence artifact identity is incomplete")
    artifact = source.artifact(evidence["artifact_id"])
    require("manifest.json" in artifact and "recovery-metadata.json" in artifact
            and sha256(artifact["manifest.json"]) == evidence["manifest_sha256"]
            and sha256(artifact["recovery-metadata.json"]) == evidence["recovery_metadata_sha256"],
            "protected backend evidence manifest or recovery metadata mismatch")
    manifest = json.loads(artifact["manifest.json"])
    require(manifest == {"format": "stocks-protected-backend-evidence-v1",
            "candidate_sha": candidate, "project_ref": record.get("project_ref"),
            "release_run_id": record.get("release_workflow_run_id"),
            "release_run_attempt": record.get("release_workflow_run_attempt"),
            "components": [{"component": row["component"],
                "deployed_prefix": row["artifact_prefix"], "deployed_sha256": row["deployed_sha256"],
                "prior_prefix": row["prior"].get("artifact_prefix"),
                "prior_sha256": row["prior"].get("source_sha256")} for row in rows]},
            "protected backend evidence manifest is inconsistent")
    require(json.loads(artifact["recovery-metadata.json"]) == record.get("recovery_journal"),
            "protected recovery journal receipt is inconsistent")

    consumed_paths = {"manifest.json", "recovery-metadata.json"}

    def files_at(prefix: str) -> dict[str, bytes]:
        marker = prefix + "/"
        files = {path[len(marker):]: raw for path, raw in artifact.items() if path.startswith(marker)}
        require(files and all(path_is_safe(path) for path in files), "component artifact prefix is empty or unsafe")
        consumed_paths.update(marker + path for path in files)
        return files

    for row in rows:
        name = row["component"]
        require(row.get("candidate_sha") == candidate and isinstance(row.get("deployment_id"), str)
                and row["deployment_id"] and isinstance(row.get("version"), str) and row["version"]
                and row.get("origin") == "management_plane_download"
                and row.get("artifact_id") == evidence["artifact_id"]
                and isinstance(row.get("artifact_prefix"), str), "component platform identity is incomplete")
        expected, expected_configuration = git_function_runtime(repo, candidate, name)
        files = files_at(row["artifact_prefix"])
        digest = tree_sha256(files)
        require(digest == row["deployed_sha256"], "component readback artifact bytes mismatch")
        function = next(item for item in record["functions"] if item["function"] == name)
        require(files == expected and row["deployment_id"] == function.get("deployment_id")
                and row["version"] == str(function["function_version"])
                and row.get("configuration") == expected_configuration,
                "component deployed byte/version/configuration parity mismatch")
        prior = row.get("prior")
        require(isinstance(prior, Mapping) and type(prior.get("exists")) is bool
                and isinstance(prior.get("configuration"), Mapping), "component prior-state rollback capture is incomplete")
        captured = timestamp(prior.get("captured_at"))
        require(captured < (deployed_at or datetime.now(timezone.utc)), "component rollback capture is not predeployment")
        if prior["exists"]:
            require(isinstance(prior.get("deployment_id"), str) and prior["deployment_id"]
                    and isinstance(prior.get("version"), str) and prior["version"]
                    and prior.get("artifact_id") == evidence["artifact_id"]
                    and isinstance(prior.get("artifact_prefix"), str)
                    and tree_sha256(files_at(prior["artifact_prefix"])) == prior["source_sha256"],
                    "component prior rollback bytes or identity mismatch")
        else:
            require(prior.get("deployment_id") is None and prior.get("version") is None
                    and prior.get("artifact_id") is None and prior.get("artifact_prefix") is None,
                    "component absence proof is inconsistent")
    require(set(artifact) == consumed_paths, "protected backend artifact has unexpected files")


@runtime_checkable
class ReleaseDataSource(Protocol):
    def scheduled_run(self, deployed_at: str) -> str: ...
    def deployment(self, deployment_id: int) -> Mapping: ...
    def ci(self, workflow_run_id: int) -> Mapping: ...
    def merge(self, pull_request_number: int) -> Mapping: ...
    def reviews(self, pull_request_number: int) -> list[Mapping]: ...
    def authorization_comments(self, pull_request_number: int) -> list[Mapping]: ...
    def repository_owner_id(self) -> int: ...
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


def git_function_runtime(
    repo: Path,
    candidate: str,
    name: str,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Read one exact, self-contained deploy manifest from the candidate Git tree."""
    require(name in FUNCTIONS, "candidate function is not allowlisted")
    try:
        configured = tomllib.loads(
            git(repo, "show", f"{candidate}:supabase/config.toml").decode()
        )["functions"].get(name)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("candidate function configuration is malformed") from error
    return configured_function_runtime(
        name,
        git_files(repo, candidate, f"supabase/functions/{name}"),
        configured,
    )


def verify_artifacts(repo: Path, static_root: Path, candidate: str, record: Mapping, source: ReleaseDataSource, now: datetime, deployed: datetime) -> None:
    verify_component_artifacts(repo, candidate, record, source, deployed_at=deployed)
    migrations = git_files(repo, candidate, "sql/migrations")
    expected_migrations = [{"path": f"sql/migrations/{path}", "version": Path(path).name.split("_", 1)[0],
                            "sha256": hashlib.sha256(raw).hexdigest()}
                           for path, raw in sorted(migrations.items()) if path.endswith(".sql")]
    require(record["migrations"] == expected_migrations, "migration byte hashes or complete version set differ from candidate")
    application = record.get("migration_application")
    require(isinstance(application, Mapping) and set(application) == {"candidate", "applied", "skipped"}
            and application["candidate"] == expected_migrations
            and all(isinstance(rows, list) for rows in (application["applied"], application["skipped"])),
            "migration application receipt is incomplete")
    applied = [canonical_json(row) for row in application["applied"]]
    skipped = [canonical_json(row) for row in application["skipped"]]
    expected = [canonical_json(row) for row in expected_migrations]
    require(len(applied) == len(set(applied)) and len(skipped) == len(set(skipped))
            and not set(applied).intersection(skipped)
            and set(applied).union(skipped) == set(expected),
            "migration application receipt does not partition the candidate manifest")
    functions = record["functions"]
    require(isinstance(functions, list) and [row["function"] for row in functions] == list(FUNCTIONS), "function evidence is incomplete")
    for row in functions:
        expected_function, _configuration = git_function_runtime(
            repo, candidate, row["function"]
        )
        require(row["git_sha"] == candidate and type(row["function_version"]) is int and row["function_version"] > 0
                and row["source_sha256"] == tree_sha256(expected_function), "function byte hash or candidate SHA mismatch")
    static = record["static_assets"]
    require(static["candidate_sha"] == candidate and static["source_sha256"] == tree_sha256(git_files(repo, candidate, "apps/web")), "static source candidate hash mismatch")
    require(static_root.is_dir() and not static_root.is_symlink(), "static build is unavailable")
    local_files = {}
    for path in sorted(static_root.rglob("*")):
        require(not path.is_symlink(), "static build includes a symlink")
        if path.is_file():
            local_files[path.relative_to(static_root).as_posix()] = sha256(path.read_bytes())
    require(local_files and "index.html" in local_files and local_files == static["files"], "static artifact bytes do not match protected deployment")
    recovery = record.get("recovery_journal")
    require(isinstance(recovery, Mapping) and set(recovery) == {
        "sequence", "run_id", "run_attempt", "captured_at", "ciphertext_sha256"
    } and type(recovery.get("sequence")) is int and recovery["sequence"] > 0
        and recovery.get("run_id") == record.get("release_workflow_run_id")
        and recovery.get("run_attempt") == record.get("release_workflow_run_attempt")
        and re.fullmatch(r"[0-9a-f]{64}", str(recovery.get("ciphertext_sha256", "")))
        and timestamp(recovery.get("captured_at")) <= deployed,
        "protected recovery journal identity is incomplete")
    outcome = record.get("deployment_outcome")
    if outcome == "succeeded":
        validate_scheduled_readiness_receipt(
            record.get("scheduled_readiness_at_release"),
        )
        require(record.get("evidence_classes", {}).get("protected_backend") == {
            "status": "verified", "candidate_sha": candidate},
            "successful protected backend evidence class is incomplete")
        require(record.get("evidence_classes", {}).get("owner_site") == {
            "status": "pending", "required_evidence": "current_authenticated_native_connector_observation"},
            "owner Site evidence must remain pending in the backend release record")
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
    run_outcomes = rows.get("run_outcomes")
    require(isinstance(run_outcomes, list), "scheduled terminal outcome evidence is missing")
    if run_outcomes:
        outcome = one(run_outcomes, "terminal outcome")
        response = evaluation.get("response")
        research = packet["packet"].get("research_candidates")
        source_ids = sorted({
            evidence["item_id"]
            for candidate in research if isinstance(candidate, Mapping)
            for evidence in candidate.get("evidence", []) if isinstance(evidence, Mapping)
        }) if isinstance(research, list) else None
        expected_outcome = {"run_id": run_id, "outcome": "no_trigger", "duplicate": False}
        require(
            phase == "intraday" and run.get("telegram_message_ids") == []
            and outcome.get("run_id") == run_id
            and outcome.get("evaluation_request_id") == evaluation.get("request_id")
            and outcome.get("outcome") == "no_trigger"
            and start <= timestamp(outcome.get("created_at")) <= end
            and isinstance(response, Mapping)
            and response.get("run_id") == run_id
            and response.get("publication_id") == evaluation_publication["id"]
            and response.get("publication_status") == "suppressed"
            and response.get("telegram_message_ids") == []
            and response.get("evaluation_count") == 0
            and response.get("policy_decision_ids") == []
            and response.get("source_ids") == source_ids
            and response.get("intelligence_packet") == {
                "id": packet["id"], "content_hash": packet["packet_hash"],
            }
            and response.get("run_outcome") == expected_outcome
            and rows["origins"] == [] and rows["reports"] == []
            and rows["publications"] == [],
            "scheduled quiet intraday outcome is incomplete or mismatched",
        )
        require(rows["quota"] and all(
            row["run_id"] == run_id and UUID.fullmatch(row["id"])
            and type(row["actual_requests"]) is int
            and type(row["reserved_requests"]) is int
            and 0 <= row["actual_requests"] <= row["reserved_requests"]
            for row in rows["quota"]
        ), "scheduled quota receipts are incomplete")
        capability = verify_discovery_capability(rows)
        return {
            "run_id": run_id, "packet_id": packet["id"],
            "packet_hash": packet["packet_hash"], "report_id": None,
            "report_hash": None,
            "stage_ids": {"collection": completion["completion_id"],
                "packet": packet["id"], "evaluation": evaluation["request_id"],
                "report": None, "publication": evaluation_publication["id"]},
            "publication_key": None,
            "publication_receipt": {"status": "no_trigger", "telegram_message_ids": []},
            "discovery_capability": {"ok": capability.ok,
                "required_capability_ids": list(capability.required_capability_ids),
                "optional_failures": list(capability.optional_failures)},
            "operational_receipt": {"status": "verified", "run_id": run_id,
                "packet_id": packet["id"], "report_id": None,
                "publication": "no_trigger"},
            "capability_receipt": {"status": "verified", "checkpoint": "V1-C3",
                "run_id": run_id,
                "required_capability_ids": list(capability.required_capability_ids),
                "optional_failures": list(capability.optional_failures)},
        }
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
                                     "optional_failures": list(capability.optional_failures)},
            "operational_receipt": {
                "status": "verified", "run_id": run_id, "packet_id": packet["id"],
                "report_id": report["id"], "publication": delivery["status"],
            },
            "capability_receipt": {
                "status": "verified", "checkpoint": "V1-C3", "run_id": run_id,
                "required_capability_ids": list(capability.required_capability_ids),
                "optional_failures": list(capability.optional_failures),
            }}


def verify_release(source: ReleaseDataSource, *, deployment_id: int,
                   native_site_archive: bytes | None = None,
                   repo_root: Path = ROOT, static_root: Path = ROOT / "dist",
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
                and record.get("repository") == ci.get("repository", {}).get("full_name")
                and ci["head_sha"] == candidate and ci["status"] == "completed" and ci["conclusion"] == "success"
                and ci.get("name") == "Owner dashboard verification"
                and ci["path"] == ".github/workflows/owner-dashboard-ci.yml"
                and ci.get("event") == "push" and ci.get("head_branch") == "main"
                and ci["id"] == record["workflow_run_id"]
                and merge.get("number") == record["pull_request_number"]
                and merge.get("base", {}).get("repo", {}).get("full_name") == record.get("repository")
                and merge.get("base", {}).get("ref") == "main"
                and merge["merged"] is True and merge["merge_commit_sha"] == candidate
                and merged <= candidate_commit_time < deployed <= now and candidate_commit_time <= timestamp(ci["updated_at"]) <= deployed, "protected CI/merge/deployment candidate SHA or time mismatch")
        release_artifact = record.get("release_artifact")
        require(isinstance(release_artifact, Mapping)
                and type(release_artifact.get("artifact_id")) is int and release_artifact["artifact_id"] > 0
                and release_artifact.get("name") == f"release-record-{deployment_id}"
                and re.fullmatch(r"sha256:[0-9a-f]{64}", str(release_artifact.get("digest", "")))
                and release_artifact.get("workflow_run_id") == record.get("release_workflow_run_id")
                and release_artifact.get("workflow_run_attempt") == record.get("release_workflow_run_attempt")
                and release_artifact.get("repository") == record.get("repository")
                and release_artifact.get("workflow_name") == "Protected owner dashboard release"
                and release_artifact.get("workflow_path") == ".github/workflows/owner-dashboard-release.yml"
                and release_artifact.get("event") == "workflow_dispatch"
                and release_artifact.get("head_branch") == "main"
                and release_artifact.get("head_sha") == candidate,
                "protected release artifact workflow identity is incomplete")
        reviewed_head = merge["head"]["sha"]
        require(bool(re.fullmatch(r"[0-9a-f]{40}", reviewed_head))
                and record.get("reviewed_sha") == reviewed_head,
                "release record does not bind the exact reviewed PR head")
        reviewed_head_time = git_commit(repo_root, reviewed_head)
        authorization = record.get("release_authorization")
        require(isinstance(authorization, Mapping)
                and authorization.get("kind") in {"github_review", "owner_comment"}
                and type(authorization.get("id")) is int and authorization["id"] > 0
                and type(authorization.get("pr_ci_workflow_run_id")) is int
                and authorization["pr_ci_workflow_run_id"] > 0,
                "release authorization identity is incomplete")
        pr_ci = source.ci(authorization["pr_ci_workflow_run_id"])
        pr_head = merge.get("head")
        pr_ci_links = pr_ci.get("pull_requests")
        pr_head_ref = pr_head.get("ref") if isinstance(pr_head, Mapping) else None
        pr_head_repo = pr_head.get("repo") if isinstance(pr_head, Mapping) else None
        pr_head_repo_name = (
            pr_head_repo.get("full_name") if isinstance(pr_head_repo, Mapping) else None
        )
        pr_ci_head_branch = pr_ci.get("head_branch")
        pr_ci_head_repo = pr_ci.get("head_repository")
        pr_ci_head_repo_name = (
            pr_ci_head_repo.get("full_name")
            if isinstance(pr_ci_head_repo, Mapping) else None
        )
        durable_pr_ci_binding = (
            isinstance(pr_head, Mapping)
            and isinstance(pr_head_ref, str)
            and bool(pr_head_ref)
            and isinstance(pr_head_repo, Mapping)
            and isinstance(pr_head_repo_name, str)
            and bool(pr_head_repo_name)
            and isinstance(pr_ci_head_branch, str)
            and bool(pr_ci_head_branch)
            and isinstance(pr_ci_head_repo, Mapping)
            and isinstance(pr_ci_head_repo_name, str)
            and bool(pr_ci_head_repo_name)
            and pr_ci_head_branch == pr_head_ref
            and pr_ci_head_repo_name == pr_head_repo_name
            and isinstance(pr_ci_links, list)
            and all(
                isinstance(row, Mapping)
                and type(row.get("number")) is int
                and row["number"] > 0
                for row in pr_ci_links
            )
            and (
                not pr_ci_links
                or any(
                    row["number"] == record["pull_request_number"]
                    for row in pr_ci_links
                )
            )
        )
        require(pr_ci.get("id") == authorization["pr_ci_workflow_run_id"]
                and pr_ci.get("repository", {}).get("full_name") == record.get("repository")
                and pr_ci.get("head_sha") == reviewed_head
                and pr_ci.get("status") == "completed" and pr_ci.get("conclusion") == "success"
                and pr_ci.get("name") == "Owner dashboard verification"
                and pr_ci.get("path") == ".github/workflows/owner-dashboard-ci.yml"
                and pr_ci.get("event") == "pull_request"
                and durable_pr_ci_binding
                and reviewed_head_time <= timestamp(pr_ci.get("updated_at")) <= merged,
                "release authorization PR CI identity is incomplete")
        reviews = source.reviews(record["pull_request_number"])
        require(not any(row.get("state") == "CHANGES_REQUESTED" for row in reviews),
                "release authorization is blocked by a current changes-requested review")
        if authorization["kind"] == "github_review":
            require(any(row.get("id") == authorization["id"] and row.get("state") == "APPROVED"
                    and row.get("commit_id") == reviewed_head
                    and reviewed_head_time <= timestamp(row.get("submitted_at")) <= merged
                    <= candidate_commit_time <= deployed for row in reviews),
                    "independent review of exact PR head is missing")
        else:
            expected_body = ("OWNER_RELEASE_APPROVAL_V1\n"
                f"reviewed_sha={reviewed_head}\n"
                f"pr_ci_workflow_run_id={authorization['pr_ci_workflow_run_id']}")
            owner_id = source.repository_owner_id()
            require(type(owner_id) is int and owner_id > 0, "release authorization owner identity is unavailable")
            comments = source.authorization_comments(record["pull_request_number"])
            require(any(row.get("id") == authorization["id"]
                    and row.get("user", {}).get("id") == owner_id
                    and row.get("author_association") == "OWNER"
                    and row.get("body") in {expected_body, expected_body + "\n"}
                    and timestamp(pr_ci["updated_at"]) <= timestamp(row.get("created_at"))
                    <= timestamp(row.get("updated_at")) <= merged
                    <= candidate_commit_time <= deployed for row in comments),
                    "CI-bound owner release authorization is missing")
        if reviewed_head != candidate:
            reviewed_tree = subprocess.run(["git", "rev-parse", f"{reviewed_head}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
            candidate_tree = subprocess.run(["git", "rev-parse", f"{candidate}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
            require(reviewed_tree.returncode == candidate_tree.returncode == 0
                    and reviewed_tree.stdout == candidate_tree.stdout,
                    "reviewed PR head does not bind candidate merge/tree")
        verify_artifacts(repo_root, static_root, candidate, record, source, now, deployed)
        native_site_comparison = None
        if native_site_archive is not None:
            from scripts.verify_native_site_release import compare_native_site_release
            native_site_comparison = compare_native_site_release(
                native_site_archive, candidate, record["project_ref"], repo_root,
                static_root=static_root, protected_build_receipt=record.get("static_assets"),
            )
        require(record.get("dry_run") is False, "protected deployment dry-run authority must be false")
        dry = record["dry_run_evidence"]
        before, after = dry["before"], dry["after"]
        argv = dry.get("safe_command_argv")
        script_sha = dry.get("candidate_script_sha256")
        expected_command_hash = hashlib.sha256(json.dumps({"argv": argv, "candidate_sha": candidate,
            "candidate_script_sha256": script_sha}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        omissions = before.get("pre_migration_omissions") if isinstance(before, Mapping) else None
        expected_snapshot_table_names = expected_snapshot_tables(omissions)
        require(isinstance(before, Mapping) and isinstance(after, Mapping) and before["tables"] == after["tables"]
                and isinstance(dry["table_deltas"], Mapping) and set(dry["table_deltas"]) == set(before["tables"])
                and all(type(value) is int and value == 0 for value in dry["table_deltas"].values())
                and type(dry["safe_command_exit_code"]) is int and dry["safe_command_exit_code"] == 0
                and isinstance(argv, list) and all(isinstance(arg, str) for arg in argv) and candidate in argv and "--dry-run" in argv
                and isinstance(script_sha, str) and script_sha == sha256(git(repo_root, "show", f"{candidate}:scripts/deploy_owner_dashboard_api.py"))
                and dry["safe_command_sha256"] == expected_command_hash
                and before.get("source") == after.get("source")
                and omissions == after.get("pre_migration_omissions")
                and expected_snapshot_table_names is not None
                and set(before["tables"]) == set(expected_snapshot_table_names)
                and all(isinstance(value, Mapping) and set(value) == {"count", "rows_sha256"} and type(value.get("count")) is int
                        and isinstance(value.get("rows_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["rows_sha256"])
                        for value in before["tables"].values()), "protected dry-run side-effect evidence is incomplete")
        require(record["canaries"] == {"owner": 200, "anonymous": 401, "non_owner": 403}, "protected owner/denial canaries are incomplete")
        source_reconciliation = record.get("source_reconciliation")
        evidence_reader = (
            source_reconciliation.get("evidence")
            if isinstance(source_reconciliation, Mapping)
            else None
        )
        evidence_authority = (
            evidence_reader.get("authority")
            if isinstance(evidence_reader, Mapping)
            else None
        )
        require(
            isinstance(source_reconciliation, Mapping)
            and set(source_reconciliation) == {
                "status", "dashboard", "evidence", "canonical_hashes",
                "run_relationships", "claims_checked",
            }
            and source_reconciliation.get("status") == "verified"
            and source_reconciliation.get("dashboard") == {
                "role": "stock_agent_dashboard_runtime",
                "transaction_read_only": True,
            }
            and isinstance(evidence_reader, Mapping)
            and set(evidence_reader) == {
                "role", "transaction_read_only", "authority",
            }
            and evidence_reader.get("role") == "stock_agent_release_reader_runtime"
            and evidence_reader.get("transaction_read_only") is True
            and isinstance(evidence_authority, Mapping)
            and set(evidence_authority) == {
                "status", "connection_id", "read_only", "isolated_guard",
            }
            and evidence_authority.get("status") == "verified"
            and isinstance(evidence_authority.get("connection_id"), str)
            and re.fullmatch(r"[0-9a-f]{64}", evidence_authority["connection_id"])
            is not None
            and evidence_authority.get("read_only") is True
            and evidence_authority.get("isolated_guard") is False
            and source_reconciliation.get("canonical_hashes") == "verified"
            and source_reconciliation.get("run_relationships") == "verified"
            and source_reconciliation.get("claims_checked") == 11,
            "protected source-reconciliation evidence is incomplete",
        )
        expected_auth_inventory = {
            "status": "verified", "identity_count": 2,
            "privileged_owner_count": 1, "denied_canary_count": 1,
        }
        require(record.get("auth_canary") == {
            "owner_session": "revoked",
            "non_owner_session": "revoked",
            "inventory_preflight": expected_auth_inventory,
            "inventory_readback": expected_auth_inventory,
        }, "protected Auth canary lifecycle evidence is incomplete")
        run_id = source.scheduled_run(record["deployed_at"])
        chain = verify_scheduled(source.release_rows(run_id), run_id, deployed, now)
        result = {
            "status": "protected_backend_and_scheduled_verified",
            "candidate_sha": candidate,
            "deployment_id": deployment_id,
            "evidence_classes": {
                "protected_backend": {"status": "verified", "candidate_sha": candidate},
                "owner_site": {
                    "status": "pending",
                    "required_evidence": "current_authenticated_native_connector_observation",
                },
                "operational_scheduled": dict(chain["operational_receipt"]),
                "discovery_capability": dict(chain["capability_receipt"]),
            },
            **chain,
        }
        if native_site_comparison is not None:
            result["native_site_comparison"] = native_site_comparison
        return result
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise RuntimeError("protected release evidence is unavailable or malformed") from error


def main() -> int:
    from scripts.protected_evidence import GitHubProductionDataSource, PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--deployment-id", type=int, required=True)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--static-root", type=Path, required=True)
    parser.add_argument(
        "--native-site-archive", type=Path,
        help="optional local package bytes; never closes owner-Site provenance",
    )
    args = parser.parse_args()
    with PostgresReadOnlySource(os.environ.get("RELEASE_READONLY_DATABASE_URL", ""), args.production_project_ref) as database:
        source = GitHubProductionDataSource(args.repository, args.production_project_ref, database)
        from scripts.verify_native_site_release import _load_bytes
        print(json.dumps(verify_release(source, deployment_id=args.deployment_id,
            native_site_archive=(
                _load_bytes(args.native_site_archive)
                if args.native_site_archive is not None else None
            ),
            static_root=args.static_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
