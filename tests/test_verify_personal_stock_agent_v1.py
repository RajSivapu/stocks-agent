import copy
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import uuid

import pytest

from lib.release_baseline import expected_snapshot_tables, pre_migration_omissions
from scripts import verify_personal_stock_agent_v1 as release_verifier
from scripts.verify_personal_stock_agent_v1 import verify_release
from scripts.verify_owner_dashboard_deployment import migration_statements_sha256, normalize_migration_statements
from test_recovery_bundle import recovery_records, digest

NOW = datetime(2026, 9, 5, 21, tzinfo=timezone.utc)
RUN, PACKET, REPORT, START, COLLECTION, EVALUATION, PUBLICATION, REQUEST = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(1, 9)]
_ledger = recovery_records()
REPORT_KEY = hashlib.sha256(f"v2:weekly:2026-09-05:{_ledger['packets'][0]['packet_hash']}:{_ledger['reports'][0]['report_hash']}".encode()).hexdigest()
REPORT = f"{REPORT_KEY[:8]}-{REPORT_KEY[8:12]}-5{REPORT_KEY[13:16]}-8{REPORT_KEY[17:20]}-{REPORT_KEY[20:32]}"


def tree_hash(files):
    hasher = hashlib.sha256()
    for path, raw in sorted(files.items()):
        hasher.update(path.encode() + b"\0" + raw + b"\0")
    return hasher.hexdigest()


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def test_release_exposes_a_separate_discovery_capability_verifier():
    assert callable(getattr(release_verifier, "verify_discovery_capability", None))


def _capability_rows():
    from lib.intelligence.cursors import SourceCursor
    from lib.intelligence.planner import _query_for, load_source_capabilities

    records = recovery_records()
    manifest = records["reference_manifests"][0]
    # The selected finalized manifest may originate in an earlier run, while
    # the current run must persist its own healthy binding to that exact ID.
    records["reference_run_bindings"][0]["run_id"] = RUN
    themes = [
        "macro_and_policy",
        "technology_ai_and_semiconductors",
        "energy_nuclear_and_grid_infrastructure",
        "industrial_infrastructure",
        "critical_minerals_and_magnets",
        "healthcare",
        "consumer",
        "defense_trade_and_geopolitics",
        "earnings_and_mergers_and_acquisitions",
    ]
    reference_task_id = str(uuid.uuid5(uuid.UUID(RUN), "required:reference"))
    tasks = [{
        "id": reference_task_id,
        "run_id": RUN,
        "stage": "reference",
        "provider": "sec_edgar",
        "capability_id": "sec_company_tickers_universe",
        "query_kind": "universe",
        "query_hash": "1" * 64,
        "dependency_ids": [],
        "requested_window": {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"},
        "state": "succeeded",
        "attempt_count": 1,
        "request_budget": 1,
        "result": {"reference_coverage": {
            "coverage_status": "scope_not_guaranteed",
            "reference_status": "healthy",
            "reference_manifest_id": manifest["id"],
            "reference_age_seconds": 120,
            "reference_revision": manifest["revision"],
        }},
        "created_at": "2026-09-05T19:30:00Z",
        "updated_at": "2026-09-05T19:31:00Z",
    }]
    receipts = []
    reservation_id = str(uuid.uuid5(uuid.UUID(RUN), "reservation:gdelt"))
    cache_keys = []
    required_tasks = [{
        "task_id": reference_task_id,
        "capability_id": "sec_company_tickers_universe",
        "theme_id": None,
    }]
    gdelt_capability = load_source_capabilities()["gdelt_theme_search"]
    for index, theme in enumerate(themes, 1):
        task_id = str(uuid.uuid5(uuid.UUID(RUN), f"required:gdelt:{theme}"))
        receipt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:receipt:{RUN}:{task_id}",
        ))
        cache_key = hashlib.sha256(theme.encode()).hexdigest()
        cache_keys.append(cache_key)
        window = {
            "start": "2026-09-05T12:00:00.000Z",
            "end": "2026-09-05T20:00:00.000Z",
        }
        request_cursor = SourceCursor(
            provider="gdelt", capability_id="gdelt_theme_search",
        ).to_mapping()
        planned_query = _query_for(gdelt_capability, theme)
        query_hash = digest({
            "capability_id": "gdelt_theme_search",
            "query": dict(planned_query),
            "cursor": request_cursor,
            "requested_window": window,
            "theme_id": theme,
        })
        receipt = {
            "provider": "gdelt", "reservation_id": reservation_id,
            "status": "succeeded", "cache_key": cache_key,
            "requested_window": window, "requested_limit": 20,
            "retrieved_at": "2026-09-05T19:40:00Z",
            "observed_at": "2026-09-05T19:40:00Z",
            "expires_at": "2026-09-05T19:55:00Z", "request_cost": 1,
            "upstream_remaining": None, "returned_count": 0,
            "accepted_count": 0, "duplicate_count": 0, "dropped_count": 0,
            "response_hash": hashlib.sha256(f"empty:{theme}".encode()).hexdigest(),
            "error_code": None, "source_receipt_id": receipt_id,
            "cache_predecessor_receipt_id": None,
            "metadata": {
                "capability_id": "gdelt_theme_search", "coverage_status": "success_empty",
                "cursor_start": window["start"], "cursor_end": window["end"],
                "overlap_seconds": 7200, "page": 1, "truncated": False,
                "backlog_remaining": False, "exhausted": True,
                "next_retry_phase": "post-market",
            },
        }
        tasks.append({
            "id": task_id, "run_id": RUN, "stage": "signals", "provider": "gdelt",
            "capability_id": "gdelt_theme_search", "query_kind": "theme_search",
            "query_hash": query_hash,
            "dependency_ids": [], "requested_window": window, "state": "succeeded",
            "attempt_count": 1, "request_budget": 1,
            "result": {
                "theme_id": theme, "request_cursor": request_cursor,
                "checkpoint": {"cache_key": cache_key, "receipt": receipt},
            },
            "created_at": f"2026-09-05T19:{31 + index:02d}:00Z",
            "updated_at": f"2026-09-05T19:{32 + index:02d}:00Z",
        })
        receipts.append({
            "id": receipt_id, "run_id": RUN, "reservation_id": reservation_id,
            "provider": "gdelt", "status": "succeeded", "cache_key": cache_key,
            "requested_window": window, "retrieved_at": receipt["retrieved_at"],
            "expires_at": receipt["expires_at"], "request_cost": 1,
            "upstream_remaining": None, "returned_count": 0, "accepted_count": 0,
            "duplicate_count": 0, "dropped_count": 0, "error": None,
            "response_hash": receipt["response_hash"], "created_at": receipt["retrieved_at"],
        })
        required_tasks.append({
            "task_id": task_id, "capability_id": "gdelt_theme_search", "theme_id": theme,
        })
    reservations = [{
        "id": reservation_id, "run_id": RUN, "provider": "gdelt",
        "market_date": "2026-09-05", "phase": "post-market",
        "reserved_requests": len(themes) + 2, "cache_keys": cache_keys,
        "created_at": "2026-09-05T19:30:00Z",
    }]
    plan_body = {
        "version": 1,
        "source_capability_version": 1,
        "reference_version": manifest["reference_version"],
        "required_baseline_capability_ids": ["sec_company_tickers_universe", "gdelt_theme_search"],
        "planned_task_ids": [row["id"] for row in tasks],
        "required_tasks": required_tasks,
    }
    source_plan = {**plan_body, "plan_hash": digest(plan_body)}
    coverage = {
        "complete_market_coverage": False, "mode": "bounded",
        "reference_manifest_id": manifest["id"], "reference_status": "healthy",
        "source_plan": source_plan,
    }
    packet = {
        "action_candidates": [], "contract_version": 2, "coverage": coverage,
        "evidence": [], "execution_allowed": False, "limitations": [],
        "observed_at": "2026-09-05T19:40:00.000Z", "omissions": [],
        "policy_version": 1, "research_candidates": [], "run_id": RUN,
    }
    packet_row = {
        "id": PACKET, "run_id": RUN, "policy_version": 1, "status": "completed",
        "candidate_count": 0, "evidence_count": 0, "packet_hash": digest(packet),
        "packet": packet, "created_at": "2026-09-05T19:45:00Z",
    }
    completion_receipts = []
    for checkpoint_receipt in (row["result"]["checkpoint"]["receipt"] for row in tasks[1:]):
        completion_receipts.append({
            "id": checkpoint_receipt["source_receipt_id"],
            "reservation_id": checkpoint_receipt["reservation_id"],
            "status": checkpoint_receipt["status"],
            "cache_key": checkpoint_receipt["cache_key"],
            "requested_window": checkpoint_receipt["requested_window"],
            "retrieved_at": checkpoint_receipt["retrieved_at"],
            "expires_at": checkpoint_receipt["expires_at"],
            "request_cost": checkpoint_receipt["request_cost"],
            "upstream_remaining": checkpoint_receipt["upstream_remaining"],
            "returned_count": checkpoint_receipt["returned_count"],
            "accepted_count": checkpoint_receipt["accepted_count"],
            "duplicate_count": checkpoint_receipt["duplicate_count"],
            "dropped_count": checkpoint_receipt["dropped_count"],
            "error": None,
            "response_hash": checkpoint_receipt["response_hash"],
            "cache_predecessor_receipt_id": None,
        })
    return {
        "run": [{"id": RUN, "scheduled_phase": "post-market", "scheduled_market_date": "2026-09-05"}],
        "intelligence_runs": [{
            "id": RUN, "phase": "post-market", "market_date": "2026-09-05",
            "reservation_plan": {"reservations": [{
                "id": reservation_id, "provider": "gdelt",
                "requests": len(themes) + 2, "cache_keys": [],
            }]},
            "request_window": {
                "start": "2026-09-05T12:00:00.000Z",
                "end": "2026-09-05T20:00:00.000Z",
                "timezone": "America/Chicago", "market_date": "2026-09-05",
                "phase": "post-market",
            },
        }],
        "reference_manifests": records["reference_manifests"],
        "security_reference_revisions": records["security_reference_revisions"],
        "reference_chunk_receipts": records["reference_chunk_receipts"],
        "reference_snapshot_memberships": records["reference_snapshot_memberships"],
        "reference_finalization_seals": records["reference_finalization_seals"],
        "reference_run_bindings": records["reference_run_bindings"],
        "reference_predecessor_pins": [],
        "discovery_stage_tasks": tasks,
        "source_quota_reservations": reservations,
        "source_receipts": receipts,
        "source_items": [], "intelligence_run_items": [],
        "source_item_provenance": [], "run_source_item_provenance": [],
        "packets": [packet_row],
        "completions": [{
            "completion_id": COLLECTION, "run_id": RUN,
            "payload": {"receipts": completion_receipts,
                        "packet": packet_row, "coverage": coverage},
            "receipt": {"packet_id": PACKET, "packet_hash": packet_row["packet_hash"]},
        }],
    }


def _verify_capability(rows):
    verifier = getattr(release_verifier, "verify_discovery_capability", None)
    assert callable(verifier)
    return verifier(rows)


def _rebind_packet_completion(rows):
    packet_row = rows["packets"][0]
    packet_row["packet_hash"] = digest(packet_row["packet"])
    rows["completions"][0]["receipt"]["packet_hash"] = packet_row["packet_hash"]
    rows["completions"][0]["payload"]["packet"] = copy.deepcopy(packet_row)
    rows["completions"][0]["payload"]["coverage"] = copy.deepcopy(
        packet_row["packet"]["coverage"]
    )


def _reverse_descriptors_for_source(rows, item_id, *, max_tasks):
    from lib.intelligence.discovery import (
        build_reverse_discovery_tasks,
        detect_events,
        expand_value_chain,
        load_theme_taxonomy,
    )

    source_items = {row["id"]: row for row in rows["source_items"]}
    source_receipts = {row["id"]: row for row in rows["source_receipts"]}
    source = release_verifier._verified_source_item(
        item_id, source_items, source_receipts
    )
    taxonomy = load_theme_taxonomy()
    event = detect_events((source,), taxonomy)[0]
    selected = build_reverse_discovery_tasks(
        event, expand_value_chain(event, taxonomy), max_tasks=max_tasks,
    )
    result = []
    for row in selected:
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
            "source_item_ids": list(row.dependency_ids),
        }
        result.append((descriptor, str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:reverse-discovery-task:{RUN}:{row.task_id}",
        )), row.theme_id))
    return result


def _reverse_descriptor_for_source(rows, item_id):
    return _reverse_descriptors_for_source(rows, item_id, max_tasks=1)[0]


def test_discovery_capability_accepts_receipt_backed_success_empty_for_every_due_task():
    rows = _capability_rows()
    result = _verify_capability(rows)

    assert result.ok is True
    assert result.required_capability_ids == (
        "sec_company_tickers_universe", "gdelt_theme_search",
    )
    for field, value in (
        ("timezone", "UTC"),
        ("market_date", "2026-09-04"),
        ("phase", "intraday"),
    ):
        inconsistent = copy.deepcopy(rows)
        inconsistent["intelligence_runs"][0]["request_window"][field] = value
        with pytest.raises(RuntimeError, match="reverse selection inputs"):
            _verify_capability(inconsistent)


def _make_first_required_receipt_nonempty(rows, *, persist_lineage):
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    parsed = task["result"]["checkpoint"]["receipt"]
    receipt_id = parsed["source_receipt_id"]
    parsed.update(returned_count=1, accepted_count=1)
    parsed["metadata"]["coverage_status"] = "success_nonempty"
    completion = next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == receipt_id
    )
    completion.update(returned_count=1, accepted_count=1)
    stored = next(row for row in rows["source_receipts"] if row["id"] == receipt_id)
    stored.update(returned_count=1, accepted_count=1)
    if not persist_lineage:
        return
    content_hash = hashlib.sha256(b"parsed nonempty evidence").hexdigest()
    item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-source:{content_hash}"))
    run_item_id = str(uuid.uuid5(uuid.UUID(RUN), f"run-item:{item_id}"))
    rows["source_items"] = [{
        "id": item_id, "source_receipt_id": receipt_id, "provider": "gdelt",
        "upstream_item_id": "nonempty-1", "canonical_url": "https://publisher.example/nonempty-1",
        "published_at": "2026-09-05T19:35:00Z", "effective_at": None,
        "title": "Market event", "normalized_text": "Parsed market evidence",
        "canonical_content": "parsed nonempty evidence", "content_hash": content_hash,
        "metadata": {}, "created_at": "2026-09-05T19:40:00Z",
    }]
    rows["intelligence_run_items"] = [{
        "id": run_item_id, "run_id": RUN, "source_item_id": item_id,
        "source_receipt_id": receipt_id, "disposition": "accepted", "drop_reason": None,
        "created_at": "2026-09-05T19:40:00Z",
    }]
    rows["source_item_provenance"] = [{
        "source_item_id": item_id, "provider": "gdelt",
        "canonical_item_url": "https://publisher.example/nonempty-1",
        "request_url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "retrieved_at": "2026-09-05T19:40:00Z", "reporting_at": None,
        "entity_ids": [], "security_ids": [], "discovery_status": "qualified",
        "created_at": "2026-09-05T19:40:00Z",
    }]
    rows["run_source_item_provenance"] = [{
        "run_item_id": run_item_id, "run_id": RUN, "source_item_id": item_id,
        "source_receipt_id": receipt_id, "provider": "gdelt",
        "request_url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "retrieved_at": "2026-09-05T19:40:00Z", "reporting_at": None,
        "entity_ids": [], "security_ids": [], "discovery_status": "qualified",
        "created_at": "2026-09-05T19:40:00Z",
    }]


def test_discovery_capability_rejects_success_nonempty_without_saved_item_provenance():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=False)

    with pytest.raises(RuntimeError, match="nonempty|provenance|item"):
        _verify_capability(rows)


def test_discovery_capability_accepts_success_nonempty_with_saved_item_provenance():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)

    assert _verify_capability(rows).ok is True


def test_discovery_capability_accepts_overlapping_query_duplicate_receipt_lineage():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    duplicate_task = next(
        row for row in rows["discovery_stage_tasks"][2:]
        if row["capability_id"] == "gdelt_theme_search"
    )
    parsed = duplicate_task["result"]["checkpoint"]["receipt"]
    duplicate_receipt_id = parsed["source_receipt_id"]
    parsed.update(returned_count=1, accepted_count=1)
    parsed["metadata"]["coverage_status"] = "success_nonempty"
    next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == duplicate_receipt_id
    ).update(returned_count=1, accepted_count=1)
    next(
        row for row in rows["source_receipts"] if row["id"] == duplicate_receipt_id
    ).update(returned_count=1, accepted_count=1)
    rows["packets"][0]["packet"]["coverage"]["duplicate_references"] = [{
        "item_id": rows["source_items"][0]["id"],
        "receipt_id": duplicate_receipt_id,
        "reason": "same_content_hash",
    }]
    _rebind_packet_completion(rows)
    rows["completions"][0]["payload"]["coverage"]["collector_drops"] = [{
        "candidate_key": "",
        "item_id": rows["source_items"][0]["id"],
        "kind": "source_item",
        "reason": "same_content_hash",
        "stage": "deduplication",
    }]

    assert _verify_capability(rows).ok is True


def test_discovery_capability_accepts_pre_refresh_unresolved_plan_bound_to_fresh_manifest():
    rows = _capability_rows()
    source_plan = rows["packets"][0]["packet"]["coverage"]["source_plan"]
    source_plan["reference_version"] = "sec:unresolved"
    source_plan["plan_hash"] = digest({
        key: value for key, value in source_plan.items() if key != "plan_hash"
    })
    _rebind_packet_completion(rows)

    assert _verify_capability(rows).ok is True


def test_discovery_capability_accepts_distinct_persisted_task_request_windows():
    from lib.intelligence.cursors import SourceCursor
    from lib.intelligence.planner import _query_for, load_source_capabilities

    rows = _capability_rows()
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    window = {
        "start": "2026-09-05T17:00:00.000Z",
        "end": "2026-09-05T20:00:00.000Z",
    }
    request_cursor = SourceCursor(
        provider="gdelt", capability_id="gdelt_theme_search",
        completed_through="2026-09-05T19:00:00Z",
    ).to_mapping()
    task["requested_window"] = window
    task["result"]["request_cursor"] = request_cursor
    checkpoint = task["result"]["checkpoint"]["receipt"]
    checkpoint["requested_window"] = window
    query = _query_for(
        load_source_capabilities()["gdelt_theme_search"], task["result"]["theme_id"],
    )
    task["query_hash"] = digest({
        "capability_id": "gdelt_theme_search", "query": dict(query),
        "cursor": request_cursor, "requested_window": window,
        "theme_id": task["result"]["theme_id"],
    })

    assert _verify_capability(rows).ok is True

    forged = copy.deepcopy(rows)
    receipt_id = checkpoint["source_receipt_id"]
    next(row for row in forged["source_receipts"] if row["id"] == receipt_id)[
        "requested_window"
    ] = window
    next(
        row for row in forged["completions"][0]["payload"]["receipts"]
        if row["id"] == receipt_id
    )["requested_window"] = window
    with pytest.raises(RuntimeError, match="receipt-backed success"):
        _verify_capability(forged)

    self_attested = copy.deepcopy(rows)
    self_attested_task = next(
        row for row in self_attested["discovery_stage_tasks"]
        if row["id"] == task["id"]
    )
    self_attested_window = {
        "start": "2099-01-01T00:00:00Z",
        "end": "2099-01-02T00:00:00Z",
    }
    self_attested_task["requested_window"] = self_attested_window
    self_attested_task["result"]["checkpoint"]["receipt"][
        "requested_window"
    ] = self_attested_window
    with pytest.raises(RuntimeError, match="receipt-backed success"):
        _verify_capability(self_attested)


def test_discovery_capability_accepts_cross_run_content_addressed_source_item_reuse():
    rows = _capability_rows()
    _add_unresolved_research_candidate(rows)
    current_receipt_id = rows["intelligence_run_items"][0]["source_receipt_id"]
    current_receipt = next(
        row for row in rows["source_receipts"] if row["id"] == current_receipt_id
    )
    prior_run_id = "99999999-9999-4999-8999-999999999999"
    prior_receipt_id = "88888888-8888-4888-8888-888888888888"
    prior_reservation_id = "77777777-7777-4777-8777-777777777777"
    rows["source_receipts"].append({
        **copy.deepcopy(current_receipt), "id": prior_receipt_id,
        "run_id": prior_run_id, "reservation_id": prior_reservation_id,
    })
    rows["source_quota_reservations"].append({
        "id": prior_reservation_id, "run_id": prior_run_id, "provider": "gdelt",
        "market_date": "2026-09-04", "phase": "post-market",
        "reserved_requests": 1, "cache_keys": [current_receipt["cache_key"]],
        "created_at": "2026-09-04T19:30:00Z",
    })
    rows["source_items"][0]["source_receipt_id"] = prior_receipt_id

    assert _verify_capability(rows).ok is True


def test_discovery_capability_accepts_truthful_near_duplicate_disposition():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    rows["intelligence_run_items"][0].update(
        disposition="near_duplicate", drop_reason="same_story_different_source",
    )

    assert _verify_capability(rows).ok is True


def _add_unresolved_research_candidate(rows):
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    item = rows["source_items"][0]
    receipt_id = rows["intelligence_run_items"][0]["source_receipt_id"]
    item_id = item["id"]
    evidence = {
        "authority": "radar", "canonical_url": item["canonical_url"],
        "claim_type": "event", "content_hash": item["content_hash"],
        "effective_at": None, "item_id": item_id,
        "normalized_text": item["normalized_text"],
        "published_at": item["published_at"], "reporting_at": None,
        "retrieved_at": "2026-09-05T19:40:00Z",
        "source_identity": {
            "provider": "gdelt", "receipt_id": receipt_id,
            "upstream_item_id": item["upstream_item_id"],
        },
    }
    lineage = {
        "cash_revision": None, "evidence_receipt_ids": {item_id: receipt_id},
        "observed_at": "2026-09-05T19:40:00.000Z", "policy_version": 1,
        "portfolio_revision": None, "quote_as_of": None, "quote_expires_at": None,
        "quote_receipt_id": None, "reference_expires_at": None,
        "reference_manifest_id": None, "reference_revision": None,
        "run_id": RUN, "security_revision_id": None,
    }
    suitability_body = {
        "component_scores": {
            "concentration_penalty": "0.000000", "duplication_penalty": "0.000000",
            "liquidity": "0.000000", "portfolio_relevance": "0.000000",
        },
        "lineage": lineage, "missing_reasons": ["analysis_not_ready"],
        "state": "unknown", "veto_reasons": [],
    }
    suitability = {
        **suitability_body, "evaluation_hash": digest(suitability_body),
    }
    candidate_body = {
        "adverse_paths": [], "candidate_key": "unresolved:event-one",
        "entity_id": "unresolved:event-one", "event_ids": ["event-one"],
        "evidence": [{
            "claim_type": "event", "item_id": item_id,
            "relationship_eligible": False, "role": "supporting",
        }],
        "exposure_fact_ids": [], "limitations": ["security_identity_unresolved"],
        "priority_components": {
            "authority_corroboration": "0.000000", "exposure": "0.000000",
            "materiality": "0.500000", "recency": "1.000000",
        },
        "priority_score": "1.500000", "research_state": "unresolved",
        "roles": [], "security_id": None, "suitability": suitability,
        "theme_ids": ["critical_minerals_magnets"], "ticker": None,
    }
    candidate = {**candidate_body, "candidate_hash": digest(candidate_body)}
    packet = rows["packets"][0]["packet"]
    packet["evidence"] = [evidence]
    packet["research_candidates"] = [candidate]
    _rebind_packet_completion(rows)
    return candidate


def test_discovery_capability_accepts_unresolved_candidate_with_null_reference_lineage():
    rows = _capability_rows()
    _add_unresolved_research_candidate(rows)

    assert _verify_capability(rows).ok is True


@pytest.mark.parametrize(("provider", "capability_id", "query_kind", "stage", "lineage", "tamper"), [
    ("white_house", "white_house_fact_sheets", "feed", "signals", "planned", False),
    ("yahoo", "yahoo_security_quote", "quote", "quote", "enrichment", False),
    ("yahoo", "yahoo_security_quote", "quote", "quote", "enrichment", True),
])
def test_discovery_capability_accepts_research_from_authorized_additional_source(
    provider, capability_id, query_kind, stage, lineage, tamper,
):
    rows = _capability_rows()
    candidate = _add_unresolved_research_candidate(rows)
    packet = rows["packets"][0]["packet"]
    item = rows["source_items"][0]
    item_id = item["id"]
    run_item = rows["intelligence_run_items"][0]
    required_receipt_id = run_item["source_receipt_id"]
    required_task = next(
        row for row in rows["discovery_stage_tasks"]
        if row.get("result", {}).get("checkpoint", {}).get("receipt", {}).get(
            "source_receipt_id"
        ) == required_receipt_id
    )
    required_parsed = required_task["result"]["checkpoint"]["receipt"]
    required_stored = next(
        row for row in rows["source_receipts"] if row["id"] == required_receipt_id
    )
    required_completion = next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == required_receipt_id
    )
    required_reservation = next(
        row for row in rows["source_quota_reservations"]
        if row["id"] == required_stored["reservation_id"]
    )

    reverse_descriptor = None
    reverse_theme_id = None
    if lineage == "reverse":
        item["metadata"] = {"theme_id": "critical_minerals_magnets"}
        reverse_descriptor, optional_task_id, reverse_theme_id = \
            _reverse_descriptor_for_source(rows, item_id)
    else:
        optional_task_id = str(uuid.uuid5(
            uuid.UUID(RUN), f"additional:{provider}:{capability_id}:{stage}",
        ))
    optional_receipt_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"market-intelligence:receipt:{RUN}:{optional_task_id}",
    ))
    optional_reservation_id = str(uuid.uuid5(
        uuid.UUID(RUN), f"reservation:{provider}:{capability_id}:{stage}",
    ))
    optional_task = copy.deepcopy(required_task)
    optional_task.update(
        id=optional_task_id, provider=provider, stage=stage,
        capability_id=capability_id, query_kind=query_kind,
        query_hash=hashlib.sha256(f"{provider}:{capability_id}:{stage}".encode()).hexdigest(),
        dependency_ids=[required_task["id"]] if lineage == "reverse" else [],
    )
    optional_parsed = optional_task["result"]["checkpoint"]["receipt"]
    optional_parsed.update(
        provider=provider, reservation_id=optional_reservation_id,
        source_receipt_id=optional_receipt_id,
    )
    optional_parsed["metadata"]["capability_id"] = capability_id
    optional_task["result"]["theme_id"] = (
        reverse_theme_id if lineage == "reverse"
        else None if lineage == "planned"
        else "macro_and_policy"
    )
    if lineage == "reverse":
        from lib.intelligence.cursors import SourceCursor

        optional_task["result"]["hypothesis"] = reverse_descriptor["hypothesis"]
        optional_task["result"]["reverse_descriptor"] = reverse_descriptor
        optional_task["result"]["request_cursor"] = SourceCursor(
            provider="gdelt", capability_id="gdelt_theme_search",
        ).to_mapping()
        optional_task["query_hash"] = digest({
            "capability_id": capability_id,
            "query": reverse_descriptor,
            "cursor": optional_task["result"]["request_cursor"],
            "requested_window": optional_task["requested_window"],
            "theme_id": reverse_theme_id,
        })
    elif lineage == "planned":
        from lib.intelligence.cursors import SourceCursor
        from lib.intelligence.planner import _query_for, load_source_capabilities

        capability = load_source_capabilities()[capability_id]
        request_cursor = SourceCursor(
            provider=provider, capability_id=capability_id,
        ).to_mapping()
        planned_query = _query_for(capability, None)
        optional_task["result"]["request_cursor"] = request_cursor
        optional_task["query_hash"] = digest({
            "capability_id": capability_id, "query": dict(planned_query),
            "cursor": request_cursor,
            "requested_window": optional_task["requested_window"],
            "theme_id": None,
        })
    optional_stored = copy.deepcopy(required_stored)
    optional_stored.update(
        id=optional_receipt_id, provider=provider,
        reservation_id=optional_reservation_id,
    )
    optional_completion = copy.deepcopy(required_completion)
    optional_completion.update(
        id=optional_receipt_id, reservation_id=optional_reservation_id,
    )
    optional_reservation = copy.deepcopy(required_reservation)
    optional_reservation.update(
        id=optional_reservation_id, provider=provider,
        reserved_requests=1, cache_keys=[optional_parsed["cache_key"]],
    )
    rows["discovery_stage_tasks"].append(optional_task)
    rows["source_receipts"].append(optional_stored)
    rows["completions"][0]["payload"]["receipts"].append(optional_completion)
    rows["source_quota_reservations"].append(optional_reservation)
    rows["intelligence_runs"][0]["reservation_plan"]["reservations"].append({
        "id": optional_reservation_id, "provider": provider,
        "requests": 1, "cache_keys": [],
    })

    empty_hash = hashlib.sha256(b"required-empty").hexdigest()
    for receipt in (required_parsed, required_stored, required_completion):
        receipt.update(
            returned_count=0, accepted_count=0, duplicate_count=0,
            dropped_count=0, response_hash=empty_hash,
        )
    required_parsed["metadata"]["coverage_status"] = "success_empty"

    item.update(source_receipt_id=optional_receipt_id, provider=provider)
    run_item["source_receipt_id"] = optional_receipt_id
    request_url = (
        "https://www.whitehouse.gov/fact-sheets/" if provider == "white_house"
        else "https://api.gdeltproject.org/api/v2/doc/doc"
    )
    rows["source_item_provenance"][0].update(provider=provider, request_url=request_url)
    rows["run_source_item_provenance"][0].update(
        provider=provider, source_receipt_id=optional_receipt_id, request_url=request_url,
    )
    packet["evidence"][0]["source_identity"].update(
        provider=provider, receipt_id=optional_receipt_id,
    )
    candidate["suitability"]["lineage"]["evidence_receipt_ids"][item_id] = optional_receipt_id
    suitability_body = {
        key: value for key, value in candidate["suitability"].items()
        if key != "evaluation_hash"
    }
    candidate["suitability"]["evaluation_hash"] = digest(suitability_body)
    candidate_body = {key: value for key, value in candidate.items() if key != "candidate_hash"}
    candidate["candidate_hash"] = digest(candidate_body)
    if lineage == "planned":
        source_plan = packet["coverage"]["source_plan"]
        source_plan["planned_task_ids"].append(optional_task_id)
        source_plan["plan_hash"] = digest({
            key: value for key, value in source_plan.items() if key != "plan_hash"
        })
    elif lineage == "enrichment":
        security = rows["security_reference_revisions"][0]
        descriptor = {
            "adverse_path": False, "cache_key": required_parsed["cache_key"],
            "cik": "0000000001", "dependency_task_ids": [],
            "entity_id": security["entity_id"], "event_ids": ["event-one"],
            "hypothesis_ids": ["hypothesis-one"],
            "instrument_type": security["instrument_type"], "priority": 50,
            "reference_manifest_id": security["manifest_id"],
            "reservation_id": optional_reservation_id, "role": "holding_quote",
            "security_id": security["security_id"],
            "security_revision_id": security["id"], "source_item_ids": [item_id],
            "source_receipt_id": required_receipt_id,
            "theme_id": "critical_minerals_magnets", "ticker": security["ticker"],
        }
        request_document = {
            "request_id": optional_task_id, "task_id": optional_task_id,
            "stage": "quote", "provider": provider, "capability_id": capability_id,
            "descriptor": descriptor, "query_kind": query_kind,
            "dependency_ids": [], "requested_window": optional_task["requested_window"],
            "request_budget": optional_task["request_budget"], "execution_allowed": False,
        }
        descriptor_hash = digest(request_document)
        optional_task["query_hash"] = descriptor_hash
        envelope = {
            "sec_issuer_submissions": 2, "sec_filing_document": 2,
            "yahoo_security_quote": 2, "gdelt_reverse": 2,
        }
        manifest_body = {
            "deferred_reasons": {}, "execution_allowed": False,
            "phase": "post-market", "provider_reservations": envelope,
            "request_descriptors": [{
                "request_id": optional_task_id, "descriptor_hash": descriptor_hash,
            }],
            "run_id": RUN, "schema_version": 1,
            "selection_stage": "holding_quotes",
        }
        manifest_hash = digest(manifest_body)
        manifest_id = str(uuid.uuid5(
            uuid.UUID(RUN), f"enrichment-selection:{manifest_hash}",
        ))
        rows["enrichment_request_descriptors"] = [{
            "id": optional_task_id, "manifest_id": manifest_id, "run_id": RUN,
            "task_id": optional_task_id, "provider": provider,
            "capability_id": capability_id, "query_kind": query_kind,
            "descriptor": descriptor, "content_hash": descriptor_hash,
            "created_at": "2026-09-05T19:39:00Z",
        }]
        rows["enrichment_selection_manifests"] = [{
            "id": manifest_id, "run_id": RUN, "selection_stage": "holding_quotes",
            "phase": "post-market", "request_count": 1,
            "provider_reservations": envelope, "deferred_reasons": {},
            "manifest": {
                **manifest_body, "manifest_id": manifest_id,
                "semantic_hash": manifest_hash,
            },
            "content_hash": manifest_hash, "created_at": "2026-09-05T19:39:00Z",
        }]
        if tamper:
            rows["enrichment_request_descriptors"][0]["descriptor"]["ticker"] = "TAMPER"
    _rebind_packet_completion(rows)

    if tamper:
        with pytest.raises(RuntimeError, match="adaptive request"):
            _verify_capability(rows)
    else:
        assert _verify_capability(rows).ok is True


def test_discovery_capability_rejects_unplanned_task_without_dynamic_lineage():
    rows = _capability_rows()
    forged = copy.deepcopy(rows["discovery_stage_tasks"][0])
    forged.update(
        id=str(uuid.uuid4()), stage="signals", provider="white_house",
        capability_id="white_house_fact_sheets", query_kind="feed",
        query_hash="9" * 64, dependency_ids=[],
    )
    rows["discovery_stage_tasks"].append(forged)

    with pytest.raises(RuntimeError, match="reverse (selection descriptor|task lineage)"):
        _verify_capability(rows)


def test_discovery_capability_accepts_pipeline_generated_dynamic_theme_task():
    from types import MappingProxyType

    from lib.intelligence.pipeline import IntelligencePipeline
    from lib.intelligence.providers import CollectionResult, RequestReceipt, SourceItem
    from lib.intelligence.types import DiscoveryTask

    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    source = rows["source_items"][0]
    current_receipt_id = source["source_receipt_id"]
    source.update(
        published_at=None,
        title="Liquid cooling loop: first deployment",
        metadata={
            "item_id": source["id"], "publisher_id": "publisher-a",
            "upstream_identity": "story-a", "organization_names": ["Test Corp"],
        },
    )
    source_task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["result"].get("checkpoint", {}).get("receipt", {}).get(
            "source_receipt_id"
        ) == source["source_receipt_id"]
    )
    parsed = source_task["result"]["checkpoint"]["receipt"]
    retrieved_at = datetime.fromisoformat(parsed["retrieved_at"].replace("Z", "+00:00"))
    prior_receipt_id = str(uuid.uuid5(uuid.UUID(RUN), "prior-source-receipt"))
    prior_receipt = copy.deepcopy(next(
        row for row in rows["source_receipts"] if row["id"] == current_receipt_id
    ))
    prior_receipt.update(
        id=prior_receipt_id,
        retrieved_at="2026-09-04T19:40:00Z",
        created_at="2026-09-04T19:40:00Z",
    )
    rows["source_receipts"].append(prior_receipt)
    source["source_receipt_id"] = prior_receipt_id
    rows["source_item_provenance"][0]["retrieved_at"] = "2026-09-04T19:40:00Z"
    rows["source_item_provenance"][0]["security_ids"] = ["NASDAQ:TEST"]
    rows["run_source_item_provenance"][0]["security_ids"] = ["NASDAQ:TEST"]
    second_content = "independent liquid cooling evidence"
    second_hash = hashlib.sha256(second_content.encode()).hexdigest()
    second_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-source:{second_hash}"))
    second_run_item_id = str(uuid.uuid5(uuid.UUID(RUN), f"run-item:{second_id}"))
    second_source = {
        "id": second_id, "source_receipt_id": prior_receipt_id,
        "provider": "gdelt", "upstream_item_id": "nonempty-2",
        "canonical_url": "https://second-publisher.example/nonempty-2",
        "published_at": None, "effective_at": None,
        "title": "Liquid cooling loop: second deployment",
        "normalized_text": "Independent liquid cooling evidence",
        "canonical_content": second_content, "content_hash": second_hash,
        "metadata": {
            "item_id": second_id, "publisher_id": "publisher-b",
            "upstream_identity": "story-b", "organization_names": ["Test Corp"],
        },
        "created_at": "2026-09-05T19:40:00Z",
    }
    rows["source_items"].append(second_source)
    rows["intelligence_run_items"].append({
        "id": second_run_item_id, "run_id": RUN, "source_item_id": second_id,
        "source_receipt_id": current_receipt_id, "disposition": "accepted",
        "drop_reason": None, "created_at": "2026-09-05T19:40:00Z",
    })
    rows["source_item_provenance"].append({
        "source_item_id": second_id, "provider": "gdelt",
        "canonical_item_url": second_source["canonical_url"],
        "request_url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "retrieved_at": "2026-09-04T19:40:00Z", "reporting_at": None,
        "entity_ids": [], "security_ids": ["NASDAQ:TEST"],
        "discovery_status": "qualified",
        "created_at": "2026-09-05T19:40:00Z",
    })
    rows["run_source_item_provenance"].append({
        "run_item_id": second_run_item_id, "run_id": RUN,
        "source_item_id": second_id, "source_receipt_id": current_receipt_id,
        "provider": "gdelt",
        "request_url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "retrieved_at": parsed["retrieved_at"], "reporting_at": None,
        "entity_ids": [], "security_ids": ["NASDAQ:TEST"],
        "discovery_status": "qualified",
        "created_at": "2026-09-05T19:40:00Z",
    })
    for receipt_row in (
        parsed,
        next(row for row in rows["source_receipts"]
             if row["id"] == current_receipt_id),
        next(row for row in rows["completions"][0]["payload"]["receipts"]
             if row["id"] == current_receipt_id),
    ):
        receipt_row.update(returned_count=2, accepted_count=2)
    item = SourceItem(
        provider="gdelt", upstream_item_id=source["upstream_item_id"],
        source_url=source["canonical_url"],
        title="Liquid cooling loop: first deployment",
        normalized_text=source["normalized_text"],
        canonical_content=source["canonical_content"], content_hash=source["content_hash"],
        published_at=None,
        effective_at=None, retrieved_at=retrieved_at, authority="radar",
        metadata=MappingProxyType({
            "item_id": source["id"], "publisher_id": "publisher-a",
            "upstream_identity": "story-a", "organization_names": ["Test Corp"],
        }),
        security_ids=("NASDAQ:TEST",),
    )
    second_item = SourceItem(
        provider="gdelt", upstream_item_id=second_source["upstream_item_id"],
        source_url=second_source["canonical_url"], title=second_source["title"],
        normalized_text=second_source["normalized_text"],
        canonical_content=second_content, content_hash=second_hash,
        published_at=None,
        effective_at=None, retrieved_at=retrieved_at, authority="radar",
        metadata=MappingProxyType(second_source["metadata"]),
        security_ids=("NASDAQ:TEST",),
    )
    receipt = RequestReceipt(
        provider="gdelt", reservation_id=parsed["reservation_id"], status="succeeded",
        cache_key=parsed["cache_key"], requested_window=parsed["requested_window"],
        requested_limit=20, retrieved_at=retrieved_at, observed_at=retrieved_at,
        expires_at=datetime.fromisoformat(parsed["expires_at"].replace("Z", "+00:00")),
        request_cost=1, upstream_remaining=None, returned_count=2, accepted_count=2,
        duplicate_count=0, dropped_count=0, response_hash=parsed["response_hash"],
        source_receipt_id=parsed["source_receipt_id"],
    )
    task = DiscoveryTask(
        task_id=source_task["id"], stage="signals", provider="gdelt",
        capability_id="gdelt_theme_search", query_kind="theme_search",
        theme_id=source_task["result"]["theme_id"],
        query=MappingProxyType({
            "query": "economic policy",
        }),
        window=MappingProxyType(source_task["requested_window"]), dependencies=(),
        max_attempts=1, requires_credential=False,
    )

    class Recorder(IntelligencePipeline):
        def _checkpoint_discovery_task(
            self, run_id, row, *, theme_episode_revisions=(), exposure_facts=(),
        ):
            rows["theme_episode_revisions"] = [
                {**value, "run_id": run_id, "task_id": row["id"]}
                for value in theme_episode_revisions
            ]
            return {**row, "run_id": run_id}

    recorder = Recorder.__new__(Recorder)
    persisted = {}
    recorder._persist_dynamic_theme_evaluation(
        RUN, source_task["requested_window"],
        [(task, CollectionResult((item, second_item), receipt, 20))], persisted,
    )
    rows["discovery_stage_tasks"].extend(persisted.values())

    assert _verify_capability(rows).ok is True
    missing_task = copy.deepcopy(rows)
    missing_task["discovery_stage_tasks"] = [
        row for row in missing_task["discovery_stage_tasks"]
        if row["capability_id"] != "dynamic_theme_evaluation"
    ]
    missing_task["theme_episode_revisions"] = []
    with pytest.raises(RuntimeError, match="dynamic theme evidence selection"):
        _verify_capability(missing_task)

    omitted = copy.deepcopy(rows)
    omitted_task = next(
        row for row in omitted["discovery_stage_tasks"]
        if row["capability_id"] == "dynamic_theme_evaluation"
    )
    omitted_result = omitted_task["result"]
    omitted_proposal = omitted_result["proposals"][0]
    omitted_proposal.update(
        eligible=False,
        missing_reasons=[
            "requires_two_accepted_items",
            "publisher_independent_corroboration_required",
        ],
        research_state="unresolved",
        source_ids=omitted_proposal["source_ids"][:1],
    )
    omitted_result.update(
        episode_count=0,
        research_state="unresolved",
        source_ids_truncated=0,
    )
    omitted["theme_episode_revisions"] = []
    selector_hash = digest({
        "labels": [omitted_proposal["label"]],
        "requested_labels": omitted_result["requested_labels"],
        "source_ids": omitted_proposal["source_ids"],
    })
    omitted_task["id"] = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"market-intelligence:dynamic-theme-evaluation:{RUN}:{selector_hash}",
    ))
    with pytest.raises(RuntimeError, match="dynamic theme evidence selection"):
        _verify_capability(omitted)

    forged = copy.deepcopy(rows)
    episode = forged["theme_episode_revisions"][0]
    episode["valid_from"] = "2099-01-01T00:00:00Z"
    episode["episode"]["coverage_label"] = "invented coverage"
    episode["content_hash"] = "f" * 64
    episode["id"] = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"market-intelligence:theme-episode:{episode['content_hash']}",
    ))
    with pytest.raises(RuntimeError, match="dynamic theme episode semantic lineage"):
        _verify_capability(forged)


@pytest.mark.parametrize(
    ("item_count", "batch_size", "truncated", "retained", "mixed_last"),
    [
        (64, 20, 0, 64, False),
        (65, 20, 1, 64, False),
        (33, 1, 1, 32, False),
        (65, 16, 1, 64, True),
    ],
)
def test_dynamic_theme_generator_and_verifier_share_the_64_source_boundary(
    item_count, batch_size, truncated, retained, mixed_last,
):
    from types import MappingProxyType

    from lib.intelligence.pipeline import IntelligencePipeline
    from lib.intelligence.providers import CollectionResult, RequestReceipt, SourceItem
    from lib.intelligence.types import DiscoveryTask

    window = {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"}
    items = []
    for index in range(item_count):
        canonical = json.dumps({"index": index}, separators=(",", ":"))
        content_hash = hashlib.sha256(canonical.encode()).hexdigest()
        item_id = (
            "ffffffff-ffff-4fff-bfff-ffffffffffff"
            if mixed_last and index == item_count - 1
            else str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-source:{content_hash}"))
        )
        items.append(SourceItem(
            provider="gdelt", upstream_item_id=f"cooling-{index:03d}",
            source_url=f"https://publisher-{index % 2}.example/cooling-{index:03d}",
            title=f"Liquid cooling loop capacity: report {index:03d}",
            normalized_text=f"Liquid cooling evidence {index:03d}",
            canonical_content=canonical, content_hash=content_hash,
            published_at=NOW, effective_at=None, retrieved_at=NOW, authority="radar",
            metadata=MappingProxyType({
                "item_id": item_id,
                "publisher_id": f"publisher-{index % 2}",
                "upstream_identity": f"story-{index:03d}",
            }),
        ))

    task_results = []
    source_rows = {}
    for batch, start in enumerate(range(0, item_count, batch_size)):
        task_id = str(uuid.uuid5(uuid.UUID(RUN), f"dynamic-boundary-source:{batch}"))
        is_mixed_tail = mixed_last and start + batch_size >= item_count
        task = DiscoveryTask(
            task_id=task_id, stage="signals", provider="gdelt",
            capability_id="gdelt_theme_search", query_kind="theme_search",
            theme_id=("macro_and_policy" if is_mixed_tail
                      else "critical_minerals_and_magnets"),
            query=MappingProxyType({
                "query": "economic policy" if is_mixed_tail else "critical minerals",
            }),
            window=MappingProxyType(window), dependencies=(), max_attempts=1,
            requires_credential=False,
        )
        chunk = tuple(items[start:start + batch_size])
        receipt = RequestReceipt(
            provider="gdelt", reservation_id=str(uuid.uuid4()), status="succeeded",
            cache_key=hashlib.sha256(task_id.encode()).hexdigest(),
            requested_window=window, requested_limit=20, retrieved_at=NOW,
            observed_at=NOW, expires_at=None, request_cost=1,
            upstream_remaining=None, returned_count=len(chunk),
            accepted_count=len(chunk), duplicate_count=0, dropped_count=0,
            response_hash=hashlib.sha256(str(batch).encode()).hexdigest(),
            source_receipt_id=str(uuid.uuid4()),
        )
        task_results.append((task, CollectionResult(chunk, receipt, 20)))
        source_rows[task_id] = {
            **IntelligencePipeline._task_row(
                task, state="succeeded", attempt_count=1,
                result={"theme_id": task.theme_id},
            ),
            "run_id": RUN,
        }

    episode_rows = []

    class Recorder(IntelligencePipeline):
        def _checkpoint_discovery_task(
            self, run_id, row, *, theme_episode_revisions=(), exposure_facts=(),
        ):
            episode_rows[:] = [
                {**value, "run_id": run_id, "task_id": row["id"]}
                for value in theme_episode_revisions
            ]
            return {**row, "run_id": run_id}

    recorder = Recorder.__new__(Recorder)
    recorder._persist_dynamic_theme_evaluation(
        RUN, window, task_results, source_rows,
    )
    dynamic = next(
        row for row in source_rows.values()
        if row["capability_id"] == "dynamic_theme_evaluation"
    )
    assert dynamic["result"]["source_ids_truncated"] == truncated
    assert len(dynamic["result"]["proposals"][0]["source_ids"]) == retained
    assert len(dynamic["dependency_ids"]) <= 32
    if mixed_last:
        assert dynamic["result"]["requested_labels"] == [
            "critical minerals", "critical_minerals_and_magnets",
        ]
    assert release_verifier._authorized_dynamic_task_ids(
        {
            "enrichment_selection_manifests": [],
            "enrichment_request_descriptors": [],
            "theme_episode_revisions": episode_rows,
        },
        run_id=RUN, phase="pre-market", tasks=source_rows,
        planned_ids=set(source_rows) - {dynamic["id"]},
    ) == {dynamic["id"]}


def test_discovery_capability_accepts_honest_uncertain_reverse_task():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    dependency = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    item_id = rows["source_items"][0]["id"]
    rows["source_items"][0]["metadata"] = {
        "theme_id": "critical_minerals_magnets",
    }
    from lib.intelligence.cursors import SourceCursor
    selected = _reverse_descriptors_for_source(rows, item_id, max_tasks=2)
    for descriptor, task_id, theme_id in selected:
        uncertain = copy.deepcopy(dependency)
        uncertain.update(
            id=task_id, stage="resolve", state="uncertain",
            dependency_ids=[dependency["id"]],
        )
        uncertain["result"]["theme_id"] = theme_id
        uncertain["result"]["hypothesis"] = descriptor["hypothesis"]
        uncertain["result"]["reverse_descriptor"] = descriptor
        uncertain["result"]["request_cursor"] = SourceCursor(
            provider="gdelt", capability_id="gdelt_theme_search",
            completed_through="2026-09-05T18:00:00Z",
        ).to_mapping()
        uncertain["requested_window"] = {
            "start": "2026-09-05T16:00:00.000Z",
            "end": "2026-09-05T20:00:00.000Z",
        }
        uncertain["query_hash"] = digest({
            "capability_id": "gdelt_theme_search",
            "query": descriptor,
            "cursor": uncertain["result"]["request_cursor"],
            "requested_window": uncertain["requested_window"],
            "theme_id": theme_id,
        })
        rows["discovery_stage_tasks"].append(uncertain)

    result = _verify_capability(rows)
    assert result.ok is True
    assert "gdelt_theme_search:uncertain" in result.optional_failures

    forged = copy.deepcopy(rows)
    reverse = next(row for row in forged["discovery_stage_tasks"]
                   if row["id"] == selected[0][1])
    reverse["result"]["reverse_descriptor"]["event_id"] = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="reverse selection descriptor"):
        _verify_capability(forged)

    omitted = copy.deepcopy(rows)
    omitted["discovery_stage_tasks"] = [
        row for row in omitted["discovery_stage_tasks"] if row["stage"] != "resolve"
    ]
    with pytest.raises(RuntimeError, match="reverse evidence selection"):
        _verify_capability(omitted)

    reduced = copy.deepcopy(omitted)
    reduced_plan = reduced["intelligence_runs"][0]["reservation_plan"]["reservations"][0]
    reduced_plan["requests"] -= 2
    reduced["source_quota_reservations"][0]["reserved_requests"] -= 2
    with pytest.raises(RuntimeError, match="reverse selection inputs"):
        _verify_capability(reduced)

    inflated = copy.deepcopy(omitted)
    source_plan = inflated["packets"][0]["packet"]["coverage"]["source_plan"]
    template = next(
        row for row in inflated["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    for index in range(11):
        forged_id = str(uuid.uuid5(uuid.UUID(RUN), f"forged-static-gdelt:{index}"))
        forged = copy.deepcopy(template)
        forged.update(id=forged_id, state="failed", result={})
        inflated["discovery_stage_tasks"].append(forged)
        source_plan["planned_task_ids"].append(forged_id)
    source_plan["plan_hash"] = digest({
        key: value for key, value in source_plan.items() if key != "plan_hash"
    })
    _rebind_packet_completion(inflated)
    inflated["intelligence_runs"][0]["reservation_plan"]["reservations"][0][
        "requests"
    ] += 11
    inflated["source_quota_reservations"][0]["reserved_requests"] += 11
    with pytest.raises(RuntimeError, match="reverse selection inputs"):
        _verify_capability(inflated)

    non_gdelt_inflated = copy.deepcopy(omitted)
    source_plan = non_gdelt_inflated["packets"][0]["packet"]["coverage"][
        "source_plan"
    ]
    template = next(
        row for row in non_gdelt_inflated["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    for index in range(89):
        forged_id = str(uuid.uuid5(
            uuid.UUID(RUN), f"forged-static-white-house:{index}",
        ))
        forged = copy.deepcopy(template)
        forged.update(
            id=forged_id, state="failed", provider="white_house",
            capability_id="white_house_fact_sheets", query_kind="feed",
            result={"coverage_status": "source_failed"},
        )
        non_gdelt_inflated["discovery_stage_tasks"].append(forged)
        source_plan["planned_task_ids"].append(forged_id)
    source_plan["plan_hash"] = digest({
        key: value for key, value in source_plan.items() if key != "plan_hash"
    })
    _rebind_packet_completion(non_gdelt_inflated)
    with pytest.raises(RuntimeError, match="reverse selection inputs"):
        _verify_capability(non_gdelt_inflated)

    partial = copy.deepcopy(rows)
    partial["discovery_stage_tasks"] = [
        row for row in partial["discovery_stage_tasks"]
        if row["id"] != selected[0][1]
    ]
    with pytest.raises(RuntimeError, match="reverse evidence selection"):
        _verify_capability(partial)


def test_discovery_capability_accepts_success_empty_when_all_returned_rows_are_dropped():
    rows = _capability_rows()
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    parsed = task["result"]["checkpoint"]["receipt"]
    receipt_id = parsed["source_receipt_id"]
    completion = next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == receipt_id
    )
    stored = next(row for row in rows["source_receipts"] if row["id"] == receipt_id)
    for row in (parsed, completion, stored):
        row.update(returned_count=1, accepted_count=0, dropped_count=1)

    assert _verify_capability(rows).ok is True


def test_discovery_capability_rejects_nonempty_label_when_accepted_count_is_zero():
    rows = _capability_rows()
    _make_first_required_receipt_nonempty(rows, persist_lineage=True)
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    parsed = task["result"]["checkpoint"]["receipt"]
    receipt_id = parsed["source_receipt_id"]
    completion = next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == receipt_id
    )
    stored = next(row for row in rows["source_receipts"] if row["id"] == receipt_id)
    for row in (parsed, completion, stored):
        row["accepted_count"] = 0

    with pytest.raises(RuntimeError, match="empty/nonempty"):
        _verify_capability(rows)


def test_discovery_capability_keeps_optional_failures_visible_without_blocking_closure():
    rows = _capability_rows()
    optional_task_id = str(uuid.uuid5(uuid.UUID(RUN), "optional:white-house"))
    rows["discovery_stage_tasks"].append({
        "id": optional_task_id, "run_id": RUN, "stage": "signals",
        "provider": "white_house", "capability_id": "white_house_fact_sheets",
        "query_kind": "feed", "query_hash": "f" * 64,
        "dependency_ids": [],
        "requested_window": {
            "start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z",
        },
        "state": "failed", "attempt_count": 1, "request_budget": 1,
        "result": {"coverage_status": "source_failed"},
        "created_at": "2026-09-05T19:35:00Z",
        "updated_at": "2026-09-05T19:36:00Z",
    })
    source_plan = rows["packets"][0]["packet"]["coverage"]["source_plan"]
    source_plan["planned_task_ids"].append(optional_task_id)
    plan_body = {key: value for key, value in source_plan.items() if key != "plan_hash"}
    source_plan["plan_hash"] = digest(plan_body)
    _rebind_packet_completion(rows)

    result = _verify_capability(rows)

    assert result.ok is True
    assert result.optional_failures == ("white_house_fact_sheets:failed",)


@pytest.mark.parametrize("terminal_state", [
    "failed", "disabled", "unsupported", "deferred", "uncertain",
    "quota_blocked", "configuration_missing",
])
def test_discovery_capability_rejects_non_success_required_states(terminal_state):
    rows = _capability_rows()
    task = next(row for row in rows["discovery_stage_tasks"] if row["capability_id"] == "gdelt_theme_search")
    task["state"] = "failed" if terminal_state == "failed" else "uncertain" if terminal_state == "uncertain" else "deferred"
    task["result"] = {"theme_id": task["result"]["theme_id"], "coverage_status": terminal_state}

    with pytest.raises(RuntimeError, match="required.*capability|capability.*required"):
        _verify_capability(rows)


def test_discovery_capability_derives_due_tasks_even_if_task_and_plan_are_rehashed_away():
    rows = _capability_rows()
    removed = next(row for row in rows["discovery_stage_tasks"] if row["result"].get("theme_id") == "healthcare")
    rows["discovery_stage_tasks"].remove(removed)
    source_plan = rows["packets"][0]["packet"]["coverage"]["source_plan"]
    source_plan["planned_task_ids"].remove(removed["id"])
    source_plan["required_tasks"] = [row for row in source_plan["required_tasks"] if row["task_id"] != removed["id"]]
    body = {key: value for key, value in source_plan.items() if key != "plan_hash"}
    source_plan["plan_hash"] = digest(body)
    _rebind_packet_completion(rows)

    with pytest.raises(RuntimeError, match="required.*task|healthcare"):
        _verify_capability(rows)


def test_discovery_capability_rejects_reusing_one_receipt_for_every_due_theme():
    rows = _capability_rows()
    theme_tasks = [
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    ]
    reused = copy.deepcopy(theme_tasks[0]["result"]["checkpoint"]["receipt"])
    for task in theme_tasks[1:]:
        task["result"]["checkpoint"]["receipt"] = copy.deepcopy(reused)

    with pytest.raises(RuntimeError, match="receipt|task"):
        _verify_capability(rows)


def test_discovery_capability_binds_required_task_shape_to_reviewed_registry():
    rows = _capability_rows()
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    parsed = task["result"]["checkpoint"]["receipt"]
    stored = next(
        row for row in rows["source_receipts"]
        if row["id"] == parsed["source_receipt_id"]
    )
    reservation = next(
        row for row in rows["source_quota_reservations"]
        if row["id"] == parsed["reservation_id"]
    )
    task.update(provider="white_house", query_kind="feed")
    parsed["provider"] = "white_house"
    stored["provider"] = "white_house"
    reservation["provider"] = "white_house"

    with pytest.raises(RuntimeError, match="registry|task|capability"):
        _verify_capability(rows)


@pytest.mark.parametrize("reference_status", ["reference_stale", "reference_unavailable"])
def test_discovery_capability_rejects_fallback_reference_even_when_task_says_succeeded(reference_status):
    rows = _capability_rows()
    rows["reference_run_bindings"][0]["reference_status"] = reference_status
    rows["packets"][0]["packet"]["coverage"]["reference_status"] = reference_status
    _rebind_packet_completion(rows)

    with pytest.raises(RuntimeError, match="reference"):
        _verify_capability(rows)


def test_discovery_capability_rejects_research_to_action_promotion():
    rows = _capability_rows()
    rows["packets"][0]["packet"]["action_candidates"] = [{
        "candidate_key": "sec:FORGED", "candidate_hash": "a" * 64,
        "suitability_hash": "b" * 64,
    }]
    _rebind_packet_completion(rows)

    with pytest.raises(RuntimeError, match="action|promotion"):
        _verify_capability(rows)


def test_discovery_capability_rejects_rehashed_evidence_free_analysis_ready_promotion():
    rows = _capability_rows()
    candidate = _add_unresolved_research_candidate(rows)
    packet = rows["packets"][0]["packet"]
    candidate["research_state"] = "analysis_ready"
    candidate["evidence"] = []
    candidate["limitations"] = []
    suitability = candidate["suitability"]
    suitability.update(state="eligible", missing_reasons=[], veto_reasons=[])
    suitability["evaluation_hash"] = digest({
        key: value for key, value in suitability.items() if key != "evaluation_hash"
    })
    candidate["candidate_hash"] = digest({
        key: value for key, value in candidate.items() if key != "candidate_hash"
    })
    packet["evidence"] = []
    packet["action_candidates"] = [{
        "candidate_key": candidate["candidate_key"],
        "candidate_hash": candidate["candidate_hash"],
        "suitability_hash": suitability["evaluation_hash"],
    }]
    _rebind_packet_completion(rows)

    with pytest.raises(RuntimeError, match="action|promotion|semantics|research|lineage"):
        _verify_capability(rows)


class FakeReleaseSource:
    def scheduled_run(self, deployed_at):
        return RUN

    def deployment(self, deployment_id):
        assert deployment_id == 42
        return copy.deepcopy(self.record)

    def ci(self, run_id):
        assert run_id in {41, 43}
        return copy.deepcopy(self.pr_ci_record if run_id == 41 else self.ci_record)

    def merge(self, number):
        assert number == 44
        return copy.deepcopy(self.merge_record)

    def reviews(self, number):
        assert number == 44
        return copy.deepcopy(self.review_records)

    def authorization_comments(self, number):
        assert number == 44
        return copy.deepcopy(self.authorization_comment_records)

    def repository_owner_id(self):
        return self.owner_id

    def release_rows(self, run_id):
        assert run_id == RUN
        return copy.deepcopy(self.rows)

    def artifact(self, artifact_id):
        return copy.deepcopy(self.artifacts[artifact_id])


@pytest.fixture
def release(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    raw = {"sql/migrations/20260926_suppression_reasons.sql": b"SELECT 1;\n",
           "supabase/functions/market-briefing-gateway/index.ts": b"gateway\n",
           "supabase/functions/owner-dashboard-api/index.ts": b"dashboard\n",
           "supabase/functions/telegram-portfolio/index.ts": b"telegram\n",
           "supabase/config.toml": b'''[functions."telegram-portfolio"]\nenabled = true\nverify_jwt = false\nentrypoint = "./functions/telegram-portfolio/index.ts"\n\n[functions."market-briefing-gateway"]\nenabled = true\nverify_jwt = false\nentrypoint = "./functions/market-briefing-gateway/index.ts"\n\n[functions."owner-dashboard-api"]\nenabled = true\nverify_jwt = false\nentrypoint = "./functions/owner-dashboard-api/index.ts"\n''',
           ".openai/hosting.json": b'{"project_id":"appgprj_fixture","static":{"directory":"dist","not_found_handling":"single-page-application"}}',
           "package.json": b'{"private":true}\n',
           "package-lock.json": b'{"lockfileVersion":3}\n',
           "apps/web/src/main.tsx": b"web source\n",
           "packages/dashboard-contracts/src/index.ts": b"contract source\n",
           "scripts/deploy_owner_dashboard_api.py": b"deploy verifier\n"}
    for path, content in raw.items():
        destination = repo / path; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(content)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-09-05T17:00:00Z", "GIT_COMMITTER_DATE": "2026-09-05T17:00:00Z"}
    (repo / "supabase/functions/market-briefing-gateway/index.ts").write_bytes(b"prior gateway\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "prior"], cwd=repo, env=env, check=True)
    prior = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "supabase/functions/market-briefing-gateway/index.ts").write_bytes(b"gateway\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    candidate_env = {**env, "GIT_AUTHOR_DATE": "2026-09-05T17:30:00Z", "GIT_COMMITTER_DATE": "2026-09-05T17:30:00Z"}
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate"], cwd=repo, env=candidate_env, check=True)
    reviewed_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    merge_env = {**env, "GIT_AUTHOR_DATE": "2026-09-05T18:30:00Z", "GIT_COMMITTER_DATE": "2026-09-05T18:30:00Z"}
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "merge candidate"], cwd=repo, env=merge_env, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    project_ref = "p" * 20
    api_url = f"https://{project_ref}.supabase.co/functions/v1/owner-dashboard-api"
    site_url = "https://example.chatgpt.site"
    static_files = {
        "index.html": b'<script src="/assets/index.js"></script>',
        "assets/index.js": f'const project="{project_ref}";const api="{api_url}";'.encode(),
        "_headers": b"/*\n  X-Content-Type-Options: nosniff\n",
    }
    static = tmp_path / "static"; static.mkdir()
    for path, content in static_files.items():
        target = static / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    snapshot_tables = {
        name: {"count": 0, "rows_sha256": hashlib.sha256(b"").hexdigest()}
        for name in expected_snapshot_tables(pre_migration_omissions()) or ()
    }
    source = FakeReleaseSource()
    source.ci_record = {"id": 43, "head_sha": sha, "head_branch": "main", "event": "push",
        "name": "Owner dashboard verification", "repository": {"full_name": "owner/stocks-agent"},
        "conclusion": "success", "status": "completed", "path": ".github/workflows/owner-dashboard-ci.yml",
        "updated_at": "2026-09-05T18:40:00Z"}
    source.pr_ci_record = {"id": 41, "head_sha": reviewed_sha,
        "head_branch": "candidate", "event": "pull_request",
        "head_repository": {"full_name": "owner/stocks-agent"},
        "name": "Owner dashboard verification", "repository": {"full_name": "owner/stocks-agent"},
        "conclusion": "success", "status": "completed", "path": ".github/workflows/owner-dashboard-ci.yml",
        "pull_requests": [], "updated_at": "2026-09-05T17:50:00Z"}
    source.merge_record = {"number": 44, "merged": True, "merge_commit_sha": sha,
        "merged_at": "2026-09-05T18:00:00Z", "head": {"sha": reviewed_sha,
            "ref": "candidate", "repo": {"full_name": "owner/stocks-agent"}},
        "base": {"ref": "main", "repo": {"full_name": "owner/stocks-agent"}}}
    source.review_records = [{"id": 45, "state": "APPROVED", "commit_id": reviewed_sha, "submitted_at": "2026-09-05T17:45:00Z"}]
    source.owner_id = 7
    source.authorization_comment_records = []
    evidence_id, release_run_id, release_run_attempt = 100, 47, 1
    source.artifacts = {evidence_id: {}}
    source.record = {
        "id": 42, "sha": sha, "environment": "production", "project_ref": "p" * 20,
        "repository": "owner/stocks-agent", "deployed_at": "2026-09-05T19:00:00Z",
        "workflow_run_id": 43, "release_workflow_run_id": release_run_id,
        "release_workflow_run_attempt": release_run_attempt, "pull_request_number": 44,
        "run_id": RUN, "candidate_sha": sha, "reviewed_sha": reviewed_sha,
        "release_authorization": {"kind": "github_review", "id": 45,
            "pr_ci_workflow_run_id": 41},
        "migrations": [{"path": "sql/migrations/20260926_suppression_reasons.sql", "version": "20260926", "sha256": migration_statements_sha256(normalize_migration_statements(raw["sql/migrations/20260926_suppression_reasons.sql"].decode()))}],
        "functions": [{"function": name, "deployment_id": name + "-deployment", "git_sha": sha, "function_version": 5, "source_sha256": tree_hash({"index.ts": raw[f"supabase/functions/{name}/index.ts"]})} for name in ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")],
        "static_assets": {"status": "verified", "candidate_sha": sha,
            "source_sha256": tree_hash({"src/main.tsx": b"web source\n"}),
            "build_sha256": tree_hash(static_files),
            "asset_hashes": [{"file": path, "sha256": hashlib.sha256(content).hexdigest()}
                for path, content in static_files.items()],
            "files": {path: hashlib.sha256(content).hexdigest()
                for path, content in static_files.items()}},
        "dry_run": False,
        "dry_run_evidence": {"before": {"source": {"project_ref": "p" * 20}, "tables": copy.deepcopy(snapshot_tables), "pre_migration_omissions": pre_migration_omissions()}, "after": {"source": {"project_ref": "p" * 20}, "tables": copy.deepcopy(snapshot_tables), "pre_migration_omissions": pre_migration_omissions()}, "table_deltas": {name: 0 for name in snapshot_tables}, "safe_command_argv": ["python", "scripts/deploy_owner_dashboard_api.py", "--dry-run", "--candidate-sha", sha], "candidate_script_sha256": hashlib.sha256(raw["scripts/deploy_owner_dashboard_api.py"]).hexdigest(), "safe_command_sha256": hashlib.sha256(json.dumps({"argv": ["python", "scripts/deploy_owner_dashboard_api.py", "--dry-run", "--candidate-sha", sha], "candidate_sha": sha, "candidate_script_sha256": hashlib.sha256(raw["scripts/deploy_owner_dashboard_api.py"]).hexdigest()}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "safe_command_exit_code": 0},
        "canaries": {"owner": 200, "anonymous": 401, "non_owner": 403},
        "deployment_outcome": "succeeded",
        "release_artifact": {"artifact_id": 101, "name": "release-record-42",
            "digest": "sha256:" + "c" * 64, "workflow_run_id": release_run_id,
            "workflow_run_attempt": release_run_attempt, "repository": "owner/stocks-agent",
            "workflow_name": "Protected owner dashboard release",
            "workflow_path": ".github/workflows/owner-dashboard-release.yml",
            "event": "workflow_dispatch", "head_branch": "main", "head_sha": sha},
        "recovery_journal": {"sequence": 9, "run_id": release_run_id,
            "run_attempt": release_run_attempt, "captured_at": "2026-09-05T18:15:00Z",
            "ciphertext_sha256": "d" * 64},
        "evidence_classes": {
            "protected_backend": {"status": "verified", "candidate_sha": sha},
            "owner_site": {"status": "pending", "required_evidence": "current_authenticated_native_connector_observation"},
            "operational_scheduled": {"status": "pending", "required_evidence": "normal_post_release_scheduled_receipt"},
            "discovery_capability": {"status": "pending", "checkpoint": "V1-C3",
                "required_evidence": "normal_post_release_scheduled_capability_receipt"},
        },
    }
    source.record["migration_application"] = {"candidate": copy.deepcopy(source.record["migrations"]),
        "applied": copy.deepcopy(source.record["migrations"]), "skipped": []}
    source.record["component_readbacks"] = []
    manifest_components = []
    for name in ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio"):
        files = {"index.ts": raw[f"supabase/functions/{name}/index.ts"]}
        prior_files = {"index.ts": ("prior " + name + "\n").encode()}
        deployed_prefix, prior_prefix = f"deployed/{name}", f"prior/{name}"
        source.artifacts[evidence_id][f"{deployed_prefix}/index.ts"] = files["index.ts"]
        source.artifacts[evidence_id][f"{prior_prefix}/index.ts"] = prior_files["index.ts"]
        source.record["component_readbacks"].append({
            "component": name, "candidate_sha": sha, "deployment_id": name + "-deployment", "version": "5",
            "origin": "management_plane_download", "artifact_id": evidence_id,
            "artifact_prefix": deployed_prefix, "deployed_sha256": tree_hash(files),
            "configuration": {"verify_jwt": False, "entrypoint": "index.ts", "import_map": None},
            "prior": {"exists": True, "deployment_id": name + "-prior", "version": "4", "configuration": {},
                      "captured_at": "2026-09-05T18:15:00Z",
                      "artifact_id": evidence_id, "artifact_prefix": prior_prefix,
                      "source_sha256": tree_hash(prior_files)},
        })
        manifest_components.append({"component": name, "deployed_prefix": deployed_prefix,
            "deployed_sha256": tree_hash(files), "prior_prefix": prior_prefix,
            "prior_sha256": tree_hash(prior_files)})
    manifest = {"format": "stocks-protected-backend-evidence-v1", "candidate_sha": sha,
        "project_ref": "p" * 20, "release_run_id": release_run_id,
        "release_run_attempt": release_run_attempt, "components": manifest_components}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    recovery_raw = json.dumps(source.record["recovery_journal"], sort_keys=True, separators=(",", ":")).encode()
    source.artifacts[evidence_id]["manifest.json"] = manifest_raw
    source.artifacts[evidence_id]["recovery-metadata.json"] = recovery_raw
    source.record["backend_evidence_artifact"] = {
        "artifact_id": evidence_id,
        "name": f"backend-component-evidence-{release_run_id}-{release_run_attempt}",
        "digest": "sha256:" + "e" * 64,
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "recovery_metadata_sha256": hashlib.sha256(recovery_raw).hexdigest(),
    }
    recovery = recovery_records(); packet = recovery["packets"][0]; report = recovery["reports"][0]
    source.rows = {
        "run": [{"id": RUN, "kind": "post-market", "scheduled_phase": "post-market", "scheduled_market_date": "2026-09-05", "status": "completed", "started_at": "2026-09-05T19:10:00Z", "finished_at": "2026-09-05T20:00:00Z", "gateway_request_id": START, "telegram_message_ids": [7]}],
        "intelligence_runs": [{
            "id": RUN, "phase": "post-market", "market_date": "2026-09-05",
            "request_window": {
                "start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z",
                "timezone": "America/Chicago", "market_date": "2026-09-05",
                "phase": "post-market",
            },
        }],
        "completions": [{"completion_id": COLLECTION, "run_id": RUN, "receipt": {"packet_id": PACKET, "packet_hash": packet["packet_hash"]}}],
        "run_events": [{"id": COLLECTION, "run_id": RUN, "status": "completed"}],
        "checkpoints": [{"run_id": RUN, "cache_key": "b" * 64}],
        "packets": [{**packet, "id": PACKET, "run_id": RUN}], "events": [], "rankings": [],
        "reports": [{**report, "id": REPORT, "run_id": RUN, "packet_id": PACKET, "idempotency_key": REPORT_KEY, "market_date": "2026-09-05", "kind": "weekly"}],
        "publications": [{**recovery["publications"][0], "report_id": REPORT, "idempotency_key": REPORT_KEY}],
        "evaluation_publications": [{"id": PUBLICATION, "run_id": RUN, "status": "suppressed", "phase": "post-market", "market_date": "2026-09-05"}],
        "run_outcomes": [],
        "requests": [
            {"request_id": START, "run_id": RUN, "operation": "start_run", "status": "completed", "response": {"run_id": RUN, "duplicate": False}},
            {"request_id": EVALUATION, "run_id": RUN, "operation": "evaluate_and_publish", "status": "completed", "response": {"run_id": RUN, "publication_id": PUBLICATION}},
            {"request_id": REQUEST, "run_id": None, "operation": "record_report", "status": "completed", "response": {"report_id": REPORT, "report_hash": report["report_hash"], "rendered_hash": report["rendered_hash"]}},
        ],
        "origins": [{"request_id": REQUEST, "run_id": RUN, "requested_packet_id": PACKET, "scheduled_phase": "post-market", "market_date": "2026-09-05", "requested_kind": "weekly", "requested_report_id": REPORT, "requested_idempotency_key": REPORT_KEY, "requested_report_hash": report["report_hash"]}],
        "quota": [{"id": "99999999-1111-4111-8111-111111111111", "run_id": RUN, "provider": "gdelt", "reserved_requests": 2, "actual_requests": 1}],
    }
    capability = _capability_rows()
    for key in (
        "intelligence_runs",
        "reference_manifests", "security_reference_revisions", "reference_chunk_receipts",
        "reference_snapshot_memberships", "reference_finalization_seals",
        "reference_run_bindings", "reference_predecessor_pins", "discovery_stage_tasks",
        "source_quota_reservations", "source_receipts", "source_items",
        "intelligence_run_items", "source_item_provenance", "run_source_item_provenance",
        "packets", "completions",
    ):
        source.rows[key] = copy.deepcopy(capability[key])
    capability_packet = source.rows["packets"][0]
    report["report"]["packet_hash"] = capability_packet["packet_hash"]
    report["report_hash"] = digest(report["report"])
    report_key = hashlib.sha256(
        f"v2:weekly:2026-09-05:{capability_packet['packet_hash']}:{report['report_hash']}".encode()
    ).hexdigest()
    report_id = f"{report_key[:8]}-{report_key[8:12]}-5{report_key[13:16]}-8{report_key[17:20]}-{report_key[20:32]}"
    source.expected_report_id = report_id
    source.expected_report_key = report_key
    source.rows["reports"][0].update({
        **report, "id": report_id, "run_id": RUN, "packet_id": PACKET,
        "idempotency_key": report_key, "market_date": "2026-09-05", "kind": "weekly",
    })
    source.rows["publications"][0].update(report_id=report_id, idempotency_key=report_key)
    source.rows["requests"][2]["response"].update(
        report_id=report_id, report_hash=report["report_hash"],
    )
    source.rows["origins"][0].update(
        requested_report_id=report_id, requested_idempotency_key=report_key,
        requested_report_hash=report["report_hash"],
    )
    site_archive = tar_bytes({
        "dist/.openai/hosting.json": raw[".openai/hosting.json"],
        **{f"dist/{path}": content for path, content in static_files.items()},
    })
    return source, {"deployment_id": 42, "native_site_archive": site_archive,
        "repo_root": repo, "static_root": static, "clock": lambda: NOW}


def test_release_queries_sources_and_binds_exact_receipts(release):
    source, args = release
    result = verify_release(source, **args)
    assert result["candidate_sha"] == source.record["sha"]
    assert result["stage_ids"] == {"collection": COLLECTION, "packet": PACKET, "evaluation": EVALUATION, "report": REQUEST, "publication": source.expected_report_id}
    assert result["publication_key"] == source.expected_report_key
    assert result["discovery_capability"]["ok"] is True
    assert result["publication_receipt"]["telegram_message_ids"] == [7]
    assert result["operational_receipt"] == {
        "status": "verified",
        "run_id": RUN,
        "packet_id": PACKET,
        "report_id": source.expected_report_id,
        "publication": "accepted_by_telegram",
    }
    assert result["capability_receipt"] == {
        "status": "verified",
        "checkpoint": "V1-C3",
        "run_id": RUN,
        "required_capability_ids": [
            "sec_company_tickers_universe", "gdelt_theme_search",
        ],
        "optional_failures": [],
    }
    assert result["evidence_classes"]["owner_site"] == {
        "status": "pending",
        "required_evidence": "current_authenticated_native_connector_observation",
    }
    assert result["native_site_comparison"]["status"] == "content_consistent"
    assert "verified" not in str(result["native_site_comparison"])
    source.record.pop("run_id")
    assert verify_release(source, **args)["run_id"] == RUN


def test_release_accepts_receipt_backed_quiet_intraday_without_a_report(release):
    source, args = release
    packet = source.rows["packets"][0]
    source_ids = sorted({
        evidence["item_id"]
        for candidate in packet["packet"]["research_candidates"]
        for evidence in candidate["evidence"]
    })
    source.rows["run"][0].update(
        kind="intraday", scheduled_phase="intraday", status="suppressed",
        telegram_message_ids=[],
    )
    source.rows["intelligence_runs"][0]["phase"] = "intraday"
    source.rows["intelligence_runs"][0]["request_window"]["phase"] = "intraday"
    source.rows["intelligence_runs"][0]["reservation_plan"]["reservations"][0][
        "requests"
    ] -= 1
    for reservation in source.rows["source_quota_reservations"]:
        reservation["phase"] = "intraday"
        reservation["reserved_requests"] -= 1
    source.rows["evaluation_publications"][0]["phase"] = "intraday"
    source.rows["reports"] = []
    source.rows["publications"] = []
    source.rows["origins"] = []
    source.rows["requests"] = source.rows["requests"][:2]
    source.rows["requests"][1]["response"].update({
        "publication_status": "suppressed", "telegram_message_ids": [],
        "evaluation_count": 0, "policy_decision_ids": [],
        "source_ids": source_ids,
        "intelligence_packet": {"id": packet["id"], "content_hash": packet["packet_hash"]},
        "run_outcome": {"run_id": RUN, "outcome": "no_trigger", "duplicate": False},
    })
    source.rows["run_outcomes"] = [{
        "run_id": RUN, "evaluation_request_id": EVALUATION,
        "outcome": "no_trigger", "created_at": "2026-09-05T19:50:00Z",
    }]

    result = verify_release(source, **args)

    assert result["report_id"] is None
    assert result["publication_receipt"] == {
        "status": "no_trigger", "telegram_message_ids": [],
    }
    assert result["operational_receipt"]["publication"] == "no_trigger"
    assert result["capability_receipt"]["checkpoint"] == "V1-C3"


def test_release_rejects_unbound_quiet_terminal_outcome(release):
    source, args = release
    source.rows["run_outcomes"] = [{
        "run_id": RUN, "evaluation_request_id": EVALUATION,
        "outcome": "no_trigger", "created_at": "2026-09-05T19:50:00Z",
    }]

    with pytest.raises(RuntimeError, match="quiet intraday"):
        verify_release(source, **args)


def test_release_blocks_when_required_discovery_evidence_is_missing(release):
    source, args = release
    source.rows["source_receipts"] = []
    with pytest.raises(RuntimeError, match="required capability"):
        verify_release(source, **args)


def test_release_never_promotes_caller_site_json_to_authoritative_evidence(release):
    source, args = release
    result = verify_release(source, **args)
    assert result["evidence_classes"]["owner_site"]["status"] == "pending"

    args["native_site_archive"] = {"forged": "json"}
    with pytest.raises(RuntimeError, match="archive bytes"):
        verify_release(source, **args)


def test_release_requires_readback_of_all_three_protected_backend_functions(release):
    source, args = release
    source.record.pop("component_readbacks", None)
    with pytest.raises(RuntimeError, match="component|readback"):
        verify_release(source, **args)


def test_release_rejects_backend_prior_capture_after_deployment(release):
    source, args = release
    source.record["component_readbacks"][2]["prior"]["captured_at"] = "2026-09-05T20:00:00Z"
    with pytest.raises(RuntimeError, match="predeployment"):
        verify_release(source, **args)


@pytest.mark.parametrize("index", range(3))
def test_release_rejects_each_component_deployed_byte_drift(release, index):
    source, args = release
    row = source.record["component_readbacks"][index]
    source.artifacts[100][row["artifact_prefix"] + "/index.ts"] = b"unreviewed deployed bytes"
    with pytest.raises(RuntimeError, match="component.*bytes"):
        verify_release(source, **args)


@pytest.mark.parametrize("index", range(3))
def test_release_rejects_each_component_missing_prior_identity(release, index):
    source, args = release
    source.record["component_readbacks"][index]["prior"]["deployment_id"] = None
    with pytest.raises(RuntimeError, match="rollback.*identity"):
        verify_release(source, **args)


def test_release_rejects_an_approval_after_merge_even_when_the_candidate_matches(release):
    source, args = release
    source.review_records[0]["submitted_at"] = "2026-09-05T18:01:00Z"
    with pytest.raises(RuntimeError, match="independent review"):
        verify_release(source, **args)


def test_release_rejects_a_current_changes_requested_review(release):
    source, args = release
    source.review_records.append({"id": 46, "state": "CHANGES_REQUESTED",
        "commit_id": source.record["reviewed_sha"], "submitted_at": "2026-09-05T17:50:00Z"})
    with pytest.raises(RuntimeError, match="authorization"):
        verify_release(source, **args)


@pytest.mark.parametrize("terminal_newline", ["", "\n"])
def test_release_accepts_exact_pr_ci_bound_owner_comment_for_solo_repository(
    release, terminal_newline
):
    source, args = release
    reviewed = source.record["reviewed_sha"]
    source.review_records = []
    source.record["release_authorization"] = {"kind": "owner_comment", "id": 46,
        "pr_ci_workflow_run_id": 41}
    source.authorization_comment_records = [{"id": 46, "user": {"id": 7},
        "author_association": "OWNER", "created_at": "2026-09-05T17:55:00Z",
        "updated_at": "2026-09-05T17:55:00Z",
        "body": "OWNER_RELEASE_APPROVAL_V1\n"
            f"reviewed_sha={reviewed}\npr_ci_workflow_run_id=41{terminal_newline}"}]

    result = verify_release(source, **args)
    assert result["candidate_sha"] == source.record["candidate_sha"]


def test_release_rejects_owner_comment_with_two_terminal_newlines(release):
    source, args = release
    reviewed = source.record["reviewed_sha"]
    source.review_records = []
    source.record["release_authorization"] = {"kind": "owner_comment", "id": 46,
        "pr_ci_workflow_run_id": 41}
    source.authorization_comment_records = [{"id": 46, "user": {"id": 7},
        "author_association": "OWNER", "created_at": "2026-09-05T17:55:00Z",
        "updated_at": "2026-09-05T17:55:00Z",
        "body": "OWNER_RELEASE_APPROVAL_V1\n"
            f"reviewed_sha={reviewed}\npr_ci_workflow_run_id=41\n\n"}]

    with pytest.raises(RuntimeError, match="authorization"):
        verify_release(source, **args)


@pytest.mark.parametrize("mutation", [
    lambda source: source.pr_ci_record.update(head_branch="different-branch"),
    lambda source: source.pr_ci_record["head_repository"].update(
        full_name="other/stocks-agent"
    ),
    lambda source: source.pr_ci_record.update(pull_requests=[{"number": 99}]),
    lambda source: source.pr_ci_record.update(
        pull_requests=[{"number": source.record["pull_request_number"]}, {"number": 1.5}]
    ),
    lambda source: source.pr_ci_record.update(pull_requests={}),
])
def test_release_rejects_pr_ci_without_durable_merged_pr_binding(release, mutation):
    source, args = release
    mutation(source)
    with pytest.raises(RuntimeError, match="PR CI identity"):
        verify_release(source, **args)


@pytest.mark.parametrize("value", [None, 7, ""])
def test_release_rejects_malformed_matching_pr_head_coordinates(release, value):
    source, args = release
    source.merge_record["head"]["ref"] = value
    source.merge_record["head"]["repo"]["full_name"] = value
    source.pr_ci_record["head_branch"] = value
    source.pr_ci_record["head_repository"]["full_name"] = value

    with pytest.raises(RuntimeError, match="PR CI identity"):
        verify_release(source, **args)


def test_release_rejects_missing_matching_pr_head_coordinates(release):
    source, args = release
    source.merge_record["head"].pop("ref")
    source.merge_record["head"]["repo"].pop("full_name")
    source.pr_ci_record.pop("head_branch")
    source.pr_ci_record["head_repository"].pop("full_name")

    with pytest.raises(RuntimeError, match="PR CI identity"):
        verify_release(source, **args)


@pytest.mark.parametrize("field,value", [
    ("author_association", "NONE"),
    ("created_at", "2026-09-05T17:49:59Z"),
    ("updated_at", "2026-09-05T18:00:01Z"),
    ("body", "OWNER_RELEASE_APPROVAL_V1\nreviewed_sha=wrong\npr_ci_workflow_run_id=41"),
])
def test_release_rejects_invalid_owner_comment_authorization(release, field, value):
    source, args = release
    reviewed = source.record["reviewed_sha"]
    source.review_records = []
    source.record["release_authorization"] = {"kind": "owner_comment", "id": 46,
        "pr_ci_workflow_run_id": 41}
    comment = {"id": 46, "user": {"id": 7}, "author_association": "OWNER",
        "created_at": "2026-09-05T17:55:00Z",
        "updated_at": "2026-09-05T17:55:00Z",
        "body": "OWNER_RELEASE_APPROVAL_V1\n"
            f"reviewed_sha={reviewed}\npr_ci_workflow_run_id=41"}
    comment[field] = value
    source.authorization_comment_records = [comment]

    with pytest.raises(RuntimeError, match="authorization"):
        verify_release(source, **args)


def test_release_record_must_name_the_exact_approved_pr_head(release):
    source, args = release
    source.record["reviewed_sha"] = "f" * 40
    with pytest.raises(RuntimeError, match="reviewed"):
        verify_release(source, **args)


def test_release_rejects_in_place_dry_run_row_mutation_with_unchanged_count(release):
    source, args = release
    source.record["dry_run_evidence"]["after"]["tables"]["analysis_runs"]["rows_sha256"] = "f" * 64
    with pytest.raises(RuntimeError, match="side-effect"):
        verify_release(source, **args)


def test_release_rejects_an_unapproved_pre_migration_omission(release):
    source, args = release
    source.record["dry_run_evidence"]["before"]["pre_migration_omissions"][
        "absent_tables"
    ].append("holdings")
    source.record["dry_run_evidence"]["after"]["pre_migration_omissions"][
        "absent_tables"
    ].append("holdings")
    with pytest.raises(RuntimeError, match="side-effect"):
        verify_release(source, **args)


def test_release_rejects_a_table_removed_from_both_dry_run_snapshots(release):
    source, args = release
    for phase in ("before", "after"):
        del source.record["dry_run_evidence"][phase]["tables"]["holdings"]
    del source.record["dry_run_evidence"]["table_deltas"]["holdings"]
    with pytest.raises(RuntimeError, match="side-effect"):
        verify_release(source, **args)


def test_release_rejects_caller_json_even_when_labeled_authoritative(release):
    source, args = release
    with pytest.raises(RuntimeError, match="source"):
        verify_release({"authoritative_records": source.record}, **args)


@pytest.mark.parametrize("mutation", [
    lambda s: s.record.update(candidate_sha="a" * 40),
    lambda s: s.ci_record.update(head_sha="a" * 40),
    lambda s: s.merge_record.update(merge_commit_sha="a" * 40),
    lambda s: s.record["functions"][0].update(source_sha256="b" * 64),
    lambda s: s.record["component_readbacks"][0]["configuration"].update(verify_jwt=True),
    lambda s: s.record["migrations"][0].update(sha256="b" * 64),
    lambda s: s.record["migration_application"].update(applied=[], skipped=[]),
    lambda s: s.artifacts[100].update({"unexpected.txt": b"unbound"}),
    lambda s: s.record["static_assets"]["files"].update({"index.html": "b" * 64}),
    lambda s: s.rows["run"][0].update(started_at="2020-01-01T20:00:00Z", finished_at="2020-01-01T21:00:00Z"),
    lambda s: s.rows["run"][0].update(finished_at="2027-01-01T20:00:00Z"),
    lambda s: s.record.update(deployed_at="2027-01-01T20:00:00Z"),
    lambda s: s.rows["run"][0].update(kind="on-demand"),
    lambda s: s.rows["requests"][0]["response"].update(duplicate=True),
    lambda s: s.rows["requests"][0]["response"].update(dry_run=True),
    lambda s: s.rows["completions"][0].update(completion_id="collection-1"),
    lambda s: s.rows["run_events"][0].update(id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["origins"][0].update(request_id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["origins"][0].update(requested_report_hash="f" * 64),
    lambda s: s.rows["reports"][0].update(packet_id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["packets"][0]["packet"].update(policy_version=999),
    lambda s: s.rows["publications"][0].update(idempotency_key="b" * 64),
    lambda s: s.rows["publications"][0].update(telegram_message_ids=[]),
    lambda s: s.rows["publications"][0].update(telegram_accepted_at=None),
    lambda s: s.rows["publications"][0].update(status="suppressed", telegram_message_ids=[], telegram_accepted_at=None, error="not an explicit suppression reason"),
    lambda s: s.record["recovery_journal"].update(ciphertext_sha256="b" * 64),
    lambda s: s.artifacts[100].update({"recovery-metadata.json": b"{}"}),
])
def test_release_rejects_relabeling_invented_stages_and_hash_mismatches(release, mutation):
    source, args = release; mutation(source)
    with pytest.raises(RuntimeError):
        verify_release(source, **args)


def test_release_rejects_ancient_records_even_when_every_claimed_time_is_relabelled(release):
    source, args = release
    args["clock"] = lambda: datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="stale|scheduled|fresh"):
        verify_release(source, **args)


def test_release_requires_a_real_nonempty_suppression_reason(release):
    source, args = release
    source.rows["publications"][0].update(status="suppressed", telegram_message_ids=[], telegram_accepted_at=None, suppression_reason="NO_TRIGGER")
    source.rows["run"][0].update(status="suppressed", telegram_message_ids=[])
    assert verify_release(source, **args)["publication_receipt"]["suppression_reason"] == "NO_TRIGGER"


def test_release_recomputes_local_git_objects_and_static_bytes(release):
    source, args = release
    (args["static_root"] / "index.html").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="static"):
        verify_release(source, **args)


@pytest.mark.parametrize("value", ["missing", None, True, "false", 0, 1])
def test_release_requires_authoritative_non_dry_run_boolean(release, value):
    source, args = release
    if value == "missing":
        source.record.pop("dry_run")
    else:
        source.record["dry_run"] = value
    with pytest.raises(RuntimeError, match="dry-run"):
        verify_release(source, **args)


def test_production_source_reads_deployment_and_protected_artifact_instead_of_caller_json(monkeypatch):
    import io
    import zipfile
    from scripts import protected_evidence as evidence
    database = object()
    source = evidence.GitHubProductionDataSource("owner/stocks-agent", "p" * 20, database)
    calls = []
    record = {"candidate_sha": "a" * 40, "project_ref": "p" * 20,
        "repository": "owner/stocks-agent", "deployment_id": 42,
        "release_workflow_run_id": 50, "release_workflow_run_attempt": 1,
        "backend_evidence_artifact": {"artifact_id": 47,
            "name": "backend-component-evidence-50-1", "digest": None}}
    def archive(files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            for name, raw in files.items():
                zipped.writestr(name, raw)
        return buffer.getvalue()
    archives = {
        46: archive({"release-record.json": json.dumps(record).encode()}),
        47: archive({"manifest.json": b"{}"}),
    }
    digests = {key: "sha256:" + hashlib.sha256(raw).hexdigest() for key, raw in archives.items()}
    record["backend_evidence_artifact"]["digest"] = digests[47]
    archives[46] = archive({"release-record.json": json.dumps(record).encode()})
    digests[46] = "sha256:" + hashlib.sha256(archives[46]).hexdigest()
    def get(path, *, binary=False):
        calls.append(path)
        if path.endswith("/deployments/42"):
            return {"id": 42, "sha": "a" * 40, "environment": "production", "production_environment": True,
                    "payload": {"candidate_sha": "a" * 40, "release_workflow_run_id": 50,
                                "release_workflow_run_attempt": "1"},
                    "created_at": "2026-09-05T18:00:00Z"}
        if path.endswith("/deployments/42/statuses"):
            return [{"state": "success", "created_at": "2026-09-05T19:00:00Z", "description": "release-artifact:46"}]
        if path.endswith("/actions/runs/50"):
            return {"id": 50, "head_sha": "a" * 40, "head_branch": "main",
                "status": "completed", "conclusion": "success", "run_attempt": 1,
                "event": "workflow_dispatch", "name": "Protected owner dashboard release",
                "path": ".github/workflows/owner-dashboard-release.yml",
                "repository": {"full_name": "owner/stocks-agent"}}
        for artifact_id, name in ((46, "release-record-42"), (47, "backend-component-evidence-50-1")):
            if path.endswith(f"/actions/artifacts/{artifact_id}"):
                return {"id": artifact_id, "name": name, "digest": digests[artifact_id],
                    "expired": False, "workflow_run": {"id": 50, "head_sha": "a" * 40}}
            if path.endswith(f"/actions/artifacts/{artifact_id}/zip"):
                assert binary is True
                return archives[artifact_id]
        raise AssertionError(path)
    monkeypatch.setattr(source, "_get", get)
    result = source.deployment(42)
    assert result["deployed_at"] == "2026-09-05T19:00:00Z"
    assert result["id"] == 42
    assert result["release_artifact"]["name"] == "release-record-42"
    assert len(calls) == 8


def test_production_source_keeps_only_each_reviewers_latest_decision(monkeypatch):
    from scripts.protected_evidence import GitHubProductionDataSource

    source = GitHubProductionDataSource("owner/stocks-agent", "p" * 20, object())
    rows = [
        {"id": 3, "state": "CHANGES_REQUESTED", "submitted_at": "2026-09-05T17:50:00Z", "user": {"id": 7}},
        {"id": 1, "state": "APPROVED", "submitted_at": "2026-09-05T17:40:00Z", "user": {"id": 7}},
        {"id": 2, "state": "APPROVED", "submitted_at": "2026-09-05T17:45:00Z", "user": {"id": 8}},
    ]
    monkeypatch.setattr(source, "_get", lambda _path: rows)

    assert {row["id"] for row in source.reviews(44)} == {2, 3}


def test_production_source_uses_review_id_to_break_same_second_ties(monkeypatch):
    from scripts.protected_evidence import GitHubProductionDataSource

    source = GitHubProductionDataSource("owner/stocks-agent", "p" * 20, object())
    rows = [
        {"id": 8, "state": "APPROVED", "submitted_at": "2026-09-05T17:50:00Z", "user": {"id": 7}},
        {"id": 9, "state": "CHANGES_REQUESTED", "submitted_at": "2026-09-05T17:50:00Z", "user": {"id": 7}},
    ]
    monkeypatch.setattr(source, "_get", lambda _path: rows)

    assert source.reviews(44) == [rows[1]]


def test_protected_release_extraction_reads_reused_reference_and_full_source_lineage(monkeypatch):
    from scripts.protected_evidence import PostgresReadOnlySource

    source = PostgresReadOnlySource.__new__(PostgresReadOnlySource)
    queries = []

    def query(sql, parameters=()):
        queries.append((sql, parameters))
        return []

    monkeypatch.setattr(source, "query", query)
    rows = source.release_rows(RUN)
    sql_by_key = {key: queries[index][0] for index, key in enumerate(rows) if key != "requests"}

    assert {
        "source_quota_reservations", "source_receipts", "source_items",
        "intelligence_run_items", "source_item_provenance",
        "run_source_item_provenance", "run_outcomes",
    } <= set(rows)
    assert "payload" in sql_by_key["completions"]
    assert "reservation_plan" in sql_by_key["intelligence_runs"]
    assert "request_window" in sql_by_key["intelligence_runs"]
    assert "market_reference_run_bindings" in sql_by_key["reference_manifests"]
    assert "market_reference_run_bindings" in sql_by_key["reference_chunk_receipts"]
    assert "market_reference_snapshot_memberships" in sql_by_key["security_reference_revisions"]
    assert "market_intelligence_run_items" in sql_by_key["source_items"]
    assert "market_intelligence_run_items" in sql_by_key["source_receipts"]
    assert "market_source_items" in sql_by_key["source_receipts"]
    assert "market_source_receipts" in sql_by_key["source_quota_reservations"]
    assert "market_run_terminal_outcomes" in sql_by_key["run_outcomes"]
    source_receipt_query = queries[list(rows).index("source_receipts")]
    reservation_query = queries[list(rows).index("source_quota_reservations")]
    assert source_receipt_query[1] == (RUN, RUN)
    assert reservation_query[1] == (RUN, RUN)


def test_production_source_refuses_unprotected_deployment(monkeypatch):
    from scripts.protected_evidence import GitHubProductionDataSource
    source = GitHubProductionDataSource("owner/stocks-agent", "p" * 20, object())
    monkeypatch.setattr(source, "_get", lambda path: {"id": 42, "environment": "staging", "production_environment": False})
    with pytest.raises(RuntimeError, match="production"):
        source.deployment(42)
