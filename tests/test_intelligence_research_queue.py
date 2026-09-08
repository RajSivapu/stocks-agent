from dataclasses import replace
from types import MappingProxyType

import pytest

from lib.intelligence.discovery import ValueChainHypothesis
from lib.intelligence.research_queue import (
    EnrichmentCandidate,
    adaptive_provider_reservations,
    build_selection_manifest,
    selection_manifest_from_payload,
    select_enrichment_queue,
    validate_selection_manifest,
)


def hypothesis(event, theme, role, *, adverse=False):
    return ValueChainHypothesis(
        hypothesis_id=f"h-{event}-{theme}-{role}-{adverse}", event_id=event,
        theme_id=theme, direction="downstream", role=role,
        query_terms=(role.replace("_", " "),), theme_terms=(theme,),
        geography="US", horizon="near", evidence_requirement="issuer_filing",
        adverse_path=adverse, invalidation_rule="issuer says role is absent",
    )


def candidate(event, theme, role, ticker, *, security=None, adverse=False, official=False, novelty=1):
    return EnrichmentCandidate(
        hypothesis=hypothesis(event, theme, role, adverse=adverse),
        entity_id=f"sec-cik:{int(sum(map(ord, ticker))):010d}",
        security_id=security or f"NASDAQ:{ticker}",
        security_revision_id=f"rev:{security or ticker}",
        reference_manifest_id="manifest-current", cik=f"{int(sum(map(ord, ticker))):010d}",
        ticker=ticker, instrument_type="COMMON_STOCK",
        source_item_ids=(f"source:{event}",), dependency_task_ids=(f"task:{event}",),
        official_support=official, novelty=novelty,
    )


def test_adaptive_provider_envelopes_are_exact_and_sum_to_phase_budget():
    expected = {
        "pre-market": {"sec_issuer_submissions": 3, "sec_filing_document": 3,
                       "yahoo_security_quote": 4, "gdelt_reverse": 2},
        "intraday": {"sec_issuer_submissions": 1, "sec_filing_document": 1,
                     "yahoo_security_quote": 1, "gdelt_reverse": 1},
        "post-market": {"sec_issuer_submissions": 2, "sec_filing_document": 2,
                        "yahoo_security_quote": 2, "gdelt_reverse": 2},
        "on-demand": {"sec_issuer_submissions": 1, "sec_filing_document": 1,
                      "yahoo_security_quote": 1, "gdelt_reverse": 1},
    }
    for phase, want in expected.items():
        got = adaptive_provider_reservations(phase)
        assert dict(got) == want
        assert sum(got.values()) in {4, 8, 12}


def test_queue_is_permutation_stable_theme_fair_and_retains_adverse_paths():
    values = [
        candidate("e1", "magnets", "manufacturing", "AAA", official=True),
        candidate("e2", "power", "grid", "BBB"),
        candidate("e3", "robotics", "actuators", "CCC"),
        candidate("e4", "power", "input_cost", "DDD", adverse=True),
    ]
    first = select_enrichment_queue(
        values, max_entities=4, max_requests=8, required_holding_quote_requests=0,
        provider_limits={"sec_edgar": 4, "yahoo": 4},
    )
    second = select_enrichment_queue(
        list(reversed(values)), max_entities=4, max_requests=8,
        required_holding_quote_requests=0,
        provider_limits={"sec_edgar": 4, "yahoo": 4},
    )
    assert first == second
    assert {row.theme_id for row in first} == {"magnets", "power", "robotics"}
    assert any(row.adverse_path for row in first)
    assert len({row.entity_id for row in first}) <= 4
    assert all(row.execution_allowed is False for row in first)


def test_multiple_share_classes_dedupe_issuer_document_work_but_keep_hypotheses():
    first = candidate("e1", "power", "generation", "BRK.A", security="NYSE:BRK.A")
    second = candidate("e2", "power", "transmission", "BRK.B", security="NYSE:BRK.B")
    second = replace(second, entity_id=first.entity_id, cik=first.cik)
    queue = select_enrichment_queue(
        (first, second), max_entities=4, max_requests=4, required_holding_quote_requests=0,
        provider_limits={"sec_edgar": 1, "yahoo": 2},
    )
    assert sum(row.query_kind == "issuer_submissions" for row in queue) == 1
    assert sum(row.query_kind == "quote" for row in queue) == 2
    submission = next(row for row in queue if row.query_kind == "issuer_submissions")
    assert submission.security_id == first.security_id
    assert set(submission.hypothesis_ids) == {first.hypothesis.hypothesis_id, second.hypothesis.hypothesis_id}


def test_queue_respects_holding_reserve_and_all_request_ceilings():
    values = [candidate(f"e{i}", f"theme{i % 3}", "supplier", f"T{i}") for i in range(10)]
    queue = select_enrichment_queue(
        values, max_entities=4, max_requests=7, required_holding_quote_requests=3,
        provider_limits={"sec_edgar": 3, "yahoo": 4},
    )
    assert len(queue) <= 4
    assert sum(row.provider == "sec_edgar" for row in queue) <= 3
    assert sum(row.provider == "yahoo" for row in queue) <= 4
    assert len(queue) + 3 <= 7


def test_selection_manifest_is_sealed_and_tampering_fails():
    queue = select_enrichment_queue(
        (candidate("e1", "magnets", "manufacturing", "AAA"),),
        max_entities=4, max_requests=4, required_holding_quote_requests=0,
        provider_limits={"sec_edgar": 1, "yahoo": 1},
    )
    manifest = build_selection_manifest(
        run_id="11111111-1111-4111-8111-111111111111", phase="on-demand",
        requests=queue, deferred_reasons=MappingProxyType({"h-later": "provider_capacity"}),
        provider_reservations=adaptive_provider_reservations("on-demand"),
        request_window={"start": "2026-09-06T00:00:00Z", "end": "2026-09-06T01:00:00Z"},
    )
    assert validate_selection_manifest(manifest) == manifest
    payload = manifest.persistence_payload()
    assert payload["manifest"]["execution_allowed"] is False
    assert payload["requests"][0]["descriptor"]["reservation_id"]
    with pytest.raises(ValueError, match="hash"):
        validate_selection_manifest(replace(manifest, deferred_reasons={"h-later": "drift"}))


def test_sealed_manifest_round_trip_uses_exact_frozen_child_descriptors():
    queue = select_enrichment_queue(
        (candidate("e1", "magnets", "manufacturing", "AAA"),),
        max_entities=4, max_requests=4, required_holding_quote_requests=0,
        provider_limits={"sec_edgar": 1, "yahoo": 1},
    )
    manifest = build_selection_manifest(
        run_id="11111111-1111-4111-8111-111111111111", phase="on-demand",
        requests=queue, deferred_reasons={},
        provider_reservations=adaptive_provider_reservations("on-demand"),
        request_window={"start": "2026-09-06T00:00:00Z", "end": "2026-09-06T01:00:00Z"},
    )
    restored = selection_manifest_from_payload(manifest.persistence_payload())
    assert restored == manifest
    tampered = manifest.persistence_payload()
    tampered["requests"][0]["descriptor"]["ticker"] = "DRIFT"
    with pytest.raises(ValueError, match="hash"):
        selection_manifest_from_payload(tampered)
