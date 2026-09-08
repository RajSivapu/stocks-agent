import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

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


def test_release_exposes_a_separate_discovery_capability_verifier():
    assert callable(getattr(release_verifier, "verify_discovery_capability", None))


def _capability_rows():
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
    reservations = []
    required_tasks = [{
        "task_id": reference_task_id,
        "capability_id": "sec_company_tickers_universe",
        "theme_id": None,
    }]
    for index, theme in enumerate(themes, 1):
        task_id = str(uuid.uuid5(uuid.UUID(RUN), f"required:gdelt:{theme}"))
        receipt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-intelligence:receipt:{RUN}:{task_id}",
        ))
        reservation_id = str(uuid.uuid5(uuid.UUID(RUN), f"reservation:{theme}"))
        cache_key = hashlib.sha256(theme.encode()).hexdigest()
        window = {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"}
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
            "query_hash": hashlib.sha256(f"query:{theme}".encode()).hexdigest(),
            "dependency_ids": [], "requested_window": window, "state": "succeeded",
            "attempt_count": 1, "request_budget": 1,
            "result": {"theme_id": theme, "checkpoint": {"cache_key": cache_key, "receipt": receipt}},
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
        reservations.append({
            "id": reservation_id, "run_id": RUN, "provider": "gdelt",
            "market_date": "2026-09-05", "phase": "post-market",
            "reserved_requests": 1, "cache_keys": [cache_key],
            "created_at": "2026-09-05T19:30:00Z",
        })
        required_tasks.append({
            "task_id": task_id, "capability_id": "gdelt_theme_search", "theme_id": theme,
        })
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
        "intelligence_runs": [{"id": RUN, "phase": "post-market", "market_date": "2026-09-05"}],
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


def test_discovery_capability_accepts_receipt_backed_success_empty_for_every_due_task():
    result = _verify_capability(_capability_rows())

    assert result.ok is True
    assert result.required_capability_ids == (
        "sec_company_tickers_universe", "gdelt_theme_search",
    )


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
        "title": "Parsed market event", "normalized_text": "Parsed market evidence",
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
    rows = _capability_rows()
    task = next(
        row for row in rows["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    window = {"start": "2026-09-05T11:00:00Z", "end": "2026-09-05T19:00:00Z"}
    task["requested_window"] = window
    checkpoint = task["result"]["checkpoint"]["receipt"]
    checkpoint["requested_window"] = window
    receipt_id = checkpoint["source_receipt_id"]
    next(row for row in rows["source_receipts"] if row["id"] == receipt_id)[
        "requested_window"
    ] = window
    next(
        row for row in rows["completions"][0]["payload"]["receipts"]
        if row["id"] == receipt_id
    )["requested_window"] = window

    assert _verify_capability(rows).ok is True


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
        "provider": "white_house", "capability_id": "white_house_news",
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
    assert result.optional_failures == ("white_house_news:failed",)


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
        assert run_id == 43
        return copy.deepcopy(self.ci_record)

    def merge(self, number):
        assert number == 44
        return copy.deepcopy(self.merge_record)

    def reviews(self, number):
        assert number == 44
        return copy.deepcopy(self.review_records)

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
           ".openai/hosting.json": b'{"project_id":"appgprj_fixture","static":{"directory":"dist"}}',
           "apps/web/src/main.tsx": b"web source\n",
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
    static = tmp_path / "static"; static.mkdir(); (static / "index.html").write_bytes(b"<main>Private</main>")
    source = FakeReleaseSource()
    source.ci_record = {"id": 43, "head_sha": sha, "conclusion": "success", "status": "completed", "path": ".github/workflows/owner-dashboard-ci.yml", "updated_at": "2026-09-05T18:40:00Z"}
    source.merge_record = {"number": 44, "merged": True, "merge_commit_sha": sha, "merged_at": "2026-09-05T18:00:00Z", "head": {"sha": reviewed_sha}}
    source.review_records = [{"id": 45, "state": "APPROVED", "commit_id": reviewed_sha, "submitted_at": "2026-09-05T17:45:00Z"}]
    source.artifacts = {46: {"index.ts": b"prior gateway\n"}}
    source.record = {
        "id": 42, "sha": sha, "environment": "production", "project_ref": "p" * 20,
        "deployed_at": "2026-09-05T19:00:00Z", "workflow_run_id": 43, "pull_request_number": 44,
        "run_id": RUN, "candidate_sha": sha, "reviewed_sha": reviewed_sha,
        "migrations": [{"path": "sql/migrations/20260926_suppression_reasons.sql", "version": "20260926", "sha256": migration_statements_sha256(normalize_migration_statements(raw["sql/migrations/20260926_suppression_reasons.sql"].decode()))}],
        "functions": [{"function": name, "deployment_id": name + "-deployment", "git_sha": sha, "function_version": 5, "source_sha256": tree_hash({"index.ts": raw[f"supabase/functions/{name}/index.ts"]})} for name in ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")],
        "static_assets": {"candidate_sha": sha, "source_sha256": tree_hash({"src/main.tsx": b"web source\n"}), "files": {"index.html": hashlib.sha256(b"<main>Private</main>").hexdigest()}},
        "dry_run": False,
        "dry_run_evidence": {"before": {"source": {"project_ref": "p" * 20}, "tables": {"scheduled_runs": {"count": 1, "rows_sha256": "a" * 64}, "transactions": {"count": 0, "rows_sha256": "b" * 64}}}, "after": {"source": {"project_ref": "p" * 20}, "tables": {"scheduled_runs": {"count": 1, "rows_sha256": "a" * 64}, "transactions": {"count": 0, "rows_sha256": "b" * 64}}}, "table_deltas": {"scheduled_runs": 0, "transactions": 0}, "safe_command_argv": ["python", "scripts/deploy_owner_dashboard_api.py", "--dry-run", "--candidate-sha", sha], "candidate_script_sha256": hashlib.sha256(raw["scripts/deploy_owner_dashboard_api.py"]).hexdigest(), "safe_command_sha256": hashlib.sha256(json.dumps({"argv": ["python", "scripts/deploy_owner_dashboard_api.py", "--dry-run", "--candidate-sha", sha], "candidate_sha": sha, "candidate_script_sha256": hashlib.sha256(raw["scripts/deploy_owner_dashboard_api.py"]).hexdigest()}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "safe_command_exit_code": 0},
        "canaries": {"owner": 200, "anonymous": 401, "non_owner": 403},
        "rollback_capture": {"artifact_id": 46, "commit_sha": prior, "captured_at": "2026-09-05T18:15:00Z", "source_sha256": tree_hash(source.artifacts[46])},
        "deployment_outcome": "succeeded",
        "rollback_readiness": {"status": "ready", "function_version": 4, "source_sha256": tree_hash(source.artifacts[46]), "isolated_drill": {"status": "verified", "isolated": True, "source_sha256": tree_hash(source.artifacts[46]), "started_at": "2026-09-05T18:16:00Z", "completed_at": "2026-09-05T18:17:00Z"}},
    }
    source.record["component_readbacks"] = []
    for index, name in enumerate(("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio", "owner-web-site")):
        files = {"index.ts": raw[f"supabase/functions/{name}/index.ts"]} if name != "owner-web-site" else {"index.html": b"<main>Private</main>"}
        source.artifacts[100 + index] = files
        source.record["component_readbacks"].append({
            "component": name, "candidate_sha": sha, "deployment_id": name + "-deployment", "version": "5",
            "project_id": "appgprj_fixture", "origin": "management_plane_download", "artifact_id": 100 + index,
            "deployed_sha256": tree_hash(files),
            "prior": {"exists": True, "deployment_id": name + "-prior", "version": "4", "configuration": {},
                      "captured_at": "2026-09-05T18:15:00Z",
                      "artifact_id": 46, "source_sha256": tree_hash(source.artifacts[46])},
        })
    recovery = recovery_records(); packet = recovery["packets"][0]; report = recovery["reports"][0]
    source.rows = {
        "run": [{"id": RUN, "kind": "post-market", "scheduled_phase": "post-market", "scheduled_market_date": "2026-09-05", "status": "completed", "started_at": "2026-09-05T19:10:00Z", "finished_at": "2026-09-05T20:00:00Z", "gateway_request_id": START, "telegram_message_ids": [7]}],
        "intelligence_runs": [{"id": RUN, "phase": "post-market", "market_date": "2026-09-05"}],
        "completions": [{"completion_id": COLLECTION, "run_id": RUN, "receipt": {"packet_id": PACKET, "packet_hash": packet["packet_hash"]}}],
        "run_events": [{"id": COLLECTION, "run_id": RUN, "status": "completed"}],
        "checkpoints": [{"run_id": RUN, "cache_key": "b" * 64}],
        "packets": [{**packet, "id": PACKET, "run_id": RUN}], "events": [], "rankings": [],
        "reports": [{**report, "id": REPORT, "run_id": RUN, "packet_id": PACKET, "idempotency_key": REPORT_KEY, "market_date": "2026-09-05", "kind": "weekly"}],
        "publications": [{**recovery["publications"][0], "report_id": REPORT, "idempotency_key": REPORT_KEY}],
        "evaluation_publications": [{"id": PUBLICATION, "run_id": RUN, "status": "suppressed", "phase": "post-market", "market_date": "2026-09-05"}],
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
    return source, {"deployment_id": 42, "repo_root": repo, "static_root": static, "clock": lambda: NOW}


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
    source.record.pop("run_id")
    assert verify_release(source, **args)["run_id"] == RUN


def test_release_blocks_when_required_discovery_evidence_is_missing(release):
    source, args = release
    source.rows["source_receipts"] = []
    with pytest.raises(RuntimeError, match="required capability"):
        verify_release(source, **args)


def test_release_requires_readback_of_all_four_deployed_artifacts(release):
    source, args = release
    source.record.pop("component_readbacks", None)
    with pytest.raises(RuntimeError, match="component|readback"):
        verify_release(source, **args)


def test_release_rejects_site_prior_capture_after_deployment(release):
    source, args = release
    source.record["component_readbacks"][3]["prior"]["captured_at"] = "2026-09-05T20:00:00Z"
    with pytest.raises(RuntimeError, match="predeployment"):
        verify_release(source, **args)


@pytest.mark.parametrize("index", range(4))
def test_release_rejects_each_component_deployed_byte_drift(release, index):
    source, args = release
    source.artifacts[100 + index] = {"index": b"unreviewed deployed bytes"}
    with pytest.raises(RuntimeError, match="component.*bytes"):
        verify_release(source, **args)


@pytest.mark.parametrize("index", range(4))
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


def test_release_record_must_name_the_exact_approved_pr_head(release):
    source, args = release
    source.record["reviewed_sha"] = "f" * 40
    with pytest.raises(RuntimeError, match="reviewed"):
        verify_release(source, **args)


def test_release_rejects_in_place_dry_run_row_mutation_with_unchanged_count(release):
    source, args = release
    source.record["dry_run_evidence"]["after"]["tables"]["scheduled_runs"]["rows_sha256"] = "f" * 64
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
    lambda s: s.record["migrations"][0].update(sha256="b" * 64),
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
    lambda s: s.record["rollback_readiness"]["isolated_drill"].update(source_sha256="b" * 64),
    lambda s: s.artifacts[46].update({"index.ts": b"changed rollback"}),
])
def test_release_rejects_relabeling_invented_stages_and_hash_mismatches(release, mutation):
    source, args = release; mutation(source)
    with pytest.raises(RuntimeError):
        verify_release(source, **args)


def test_release_rejects_ancient_records_even_when_every_claimed_time_is_relabelled(release):
    source, args = release
    args["clock"] = lambda: datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="stale|scheduled"):
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
    from scripts import protected_evidence as evidence
    database = object()
    source = evidence.GitHubProductionDataSource("owner/stocks-agent", "p" * 20, database)
    calls = []
    def get(path):
        calls.append(path)
        if path.endswith("/deployments/42"):
            return {"id": 42, "sha": "a" * 40, "environment": "production", "production_environment": True,
                    "payload": {"release_artifact_id": 46}, "created_at": "2026-09-05T18:00:00Z"}
        if path.endswith("/deployments/42/statuses"):
            return [{"state": "success", "created_at": "2026-09-05T19:00:00Z", "description": "release-artifact:46"}]
        raise AssertionError(path)
    monkeypatch.setattr(source, "_get", get)
    monkeypatch.setattr(source, "artifact", lambda artifact_id: {"release-record.json": b'{"candidate_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","project_ref":"pppppppppppppppppppp","deployment_id":42}'})
    result = source.deployment(42)
    assert result["deployed_at"] == "2026-09-05T19:00:00Z"
    assert result["id"] == 42
    assert len(calls) == 2


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
        "run_source_item_provenance",
    } <= set(rows)
    assert "payload" in sql_by_key["completions"]
    assert "market_reference_run_bindings" in sql_by_key["reference_manifests"]
    assert "market_reference_run_bindings" in sql_by_key["reference_chunk_receipts"]
    assert "market_reference_snapshot_memberships" in sql_by_key["security_reference_revisions"]
    assert "market_intelligence_run_items" in sql_by_key["source_items"]
    assert "market_intelligence_run_items" in sql_by_key["source_receipts"]
    assert "market_source_items" in sql_by_key["source_receipts"]
    assert "market_source_receipts" in sql_by_key["source_quota_reservations"]
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
