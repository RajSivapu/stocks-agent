from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from lib.intelligence.packet import build_evidence_packet
from lib.intelligence.ranking import rank_candidates
from lib.intelligence.themes import evidence_key
from lib.intelligence.types import PacketLimits
from tests.test_intelligence_ranking import candidate
from tests.test_intelligence_ranking import v2_candidate
from tests.test_intelligence_themes import source_item


HASH_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures/research_suitability_hash_vectors.json").read_text()
)


def candidates(count: int, evidence_count: int = 10, text_size: int = 80):
    rows = []
    for candidate_index in range(count):
        evidence = []
        for evidence_index in range(evidence_count):
            item_id = 1000 + candidate_index * evidence_count + evidence_index
            evidence.append(
                replace(
                    source_item(
                        item_id,
                        authority="official" if evidence_index == 0 else "radar",
                        provider="sec_edgar" if evidence_index == 0 else "gdelt",
                        exposure_kind="filing" if evidence_index == 0 else None,
                    ),
                    summary="x" * text_size,
                )
            )
        rows.append(
            replace(
                candidate(f"T{candidate_index:02d}", evidence=evidence),
                holding_weight=Decimal("0"),
                overlap=Decimal("0"),
                concentration=Decimal("0"),
            )
        )
    return rank_candidates(rows)


def test_packet_enforces_all_three_bounds_and_records_drops():
    packet = build_evidence_packet(candidates(20), limits=PacketLimits())

    assert len(packet.candidates) == 12
    assert all(len(row.evidence) <= 8 for row in packet.candidates)
    assert len(packet.to_json_bytes()) <= 98_304
    assert len([drop for drop in packet.drops if drop.kind == "candidate"]) == 8
    assert len([drop for drop in packet.drops if drop.kind == "evidence"]) == 24


def test_packet_byte_pressure_drops_lowest_ranked_evidence_then_candidates():
    packet = build_evidence_packet(
        candidates(4, evidence_count=8, text_size=2_000),
        limits=PacketLimits(max_serialized_bytes=12_000),
    )

    assert len(packet.to_json_bytes()) <= 12_000
    assert packet.drops
    assert packet.drops[0].candidate_key == "T03"
    assert packet.drops[0].kind == "evidence"
    assert packet.candidates[0].candidate_key == "T00"


def test_v2_packet_keeps_research_and_uses_hash_bound_action_subset():
    ranked = rank_candidates([
        v2_candidate("ACT"),
        v2_candidate(
            "WAIT",
            overlap=None,
            portfolio_state="unavailable",
        ),
    ], contract_version=2)

    packet = build_evidence_packet(
        ranked,
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    )
    value = packet.to_dict()

    assert value["contract_version"] == 2
    assert len(value["research_candidates"]) == 2
    assert [row["ticker"] for row in value["research_candidates"]] == ["ACT", "WAIT"]
    assert [row["candidate_key"] for row in value["action_candidates"]] == ["sec:ACT"]
    action = value["action_candidates"][0]
    research = value["research_candidates"][0]
    assert action["candidate_hash"] == research["candidate_hash"]
    assert action["suitability_hash"] == research["suitability"]["evaluation_hash"]
    assert value["execution_allowed"] is False
    assert "candidates" not in value


def test_v2_packet_omits_structurally_rejected_research_with_a_durable_reason():
    rejected = replace(
        v2_candidate("REJECT"),
        evidence=(), supporting_evidence_ids=(), opposing_evidence_ids=(),
    )
    value = build_evidence_packet(
        rank_candidates([rejected], contract_version=2),
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    assert value["research_candidates"] == []
    assert value["action_candidates"] == []
    assert value["omissions"] == [{
        "candidate_key": "sec:REJECT", "item_id": None, "kind": "candidate",
        "reason": "research_requirements_failed", "stage": "packet",
    }]


def test_v2_packet_aggregates_same_security_without_duplicate_ticker_replacement():
    ranked = rank_candidates([
        v2_candidate("SAME", theme_ids=("magnets",)),
        v2_candidate("SAME", theme_ids=("robotics",)),
    ], contract_version=2)

    value = build_evidence_packet(
        ranked,
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    assert len(value["research_candidates"]) == 1
    assert value["research_candidates"][0]["theme_ids"] == ["magnets", "robotics"]


def test_v2_packet_aggregation_cannot_hide_an_incomplete_event_path():
    ranked = rank_candidates([
        v2_candidate("SAME", theme_ids=("magnets",)),
        v2_candidate("SAME", theme_ids=("robotics",), opposing_evidence_ids=()),
    ], contract_version=2)

    value = build_evidence_packet(
        ranked,
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    candidate = value["research_candidates"][0]
    assert candidate["research_state"] == "exposure_supported"
    assert "opposing_evidence_missing" in candidate["limitations"]
    assert value["action_candidates"] == []


def test_v2_packet_rejects_duplicate_candidate_key_with_swapped_identity():
    with pytest.raises(ValueError, match="conflicting duplicate candidate identity"):
        build_evidence_packet(
            rank_candidates([
                v2_candidate("SAME"),
                v2_candidate("OTHER", security_id="sec:SAME"),
            ], contract_version=2),
            contract_version=2,
            run_id="11111111-1111-4111-8111-111111111111",
            observed_at="2026-09-04T12:00:00.000Z",
        )


def test_v2_packet_rejects_one_ticker_bound_to_two_security_identities():
    with pytest.raises(ValueError, match="conflicting duplicate ticker identity"):
        build_evidence_packet(
            rank_candidates([
                v2_candidate("SAME", security_id="sec:ONE"),
                v2_candidate("SAME", security_id="sec:TWO"),
            ], contract_version=2),
            contract_version=2,
            run_id="11111111-1111-4111-8111-111111111111",
            observed_at="2026-09-04T12:00:00.000Z",
        )


def test_v2_packet_bounds_distinct_identities_shared_evidence_and_records_every_omission():
    ranked = rank_candidates(
        [v2_candidate(f"X{index:02d}") for index in range(13)],
        contract_version=2,
    )
    value = build_evidence_packet(
        ranked,
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    assert len(value["research_candidates"]) == 12
    assert len(value["action_candidates"]) <= 12
    assert len(value["evidence"]) <= 96
    assert any(
        row["kind"] == "candidate" and row["reason"] == "candidate_limit"
        for row in value["omissions"]
    )
    assert all(len(row["evidence"]) <= 8 for row in value["research_candidates"])


def test_v2_packet_retains_decisive_primary_and_opposing_evidence_under_byte_pressure():
    ranked = rank_candidates([
        v2_candidate("KEEP", evidence=tuple(
            replace(item, summary=("x" * 2_000))
            for item in v2_candidate("KEEP").evidence
        ))
    ], contract_version=2)
    value = build_evidence_packet(
        ranked,
        limits=PacketLimits(max_serialized_bytes=12_000),
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    retained = value["research_candidates"][0]["evidence"]
    assert any(row["claim_type"] == "issuer_exposure" for row in retained)
    assert any(row["role"] == "opposing" for row in retained)


def test_v2_packet_rehashes_candidate_and_action_reference_after_optional_thinning():
    base = v2_candidate("HASH")
    optional = tuple(
        replace(
            source_item(100 + index, authority="radar", provider="gdelt"),
            summary="x" * 2_000,
        )
        for index in range(5)
    )
    receipts = dict(base.lineage.evidence_receipt_ids)
    receipts.update({
        evidence_key(item): f"77777777-7777-4777-8777-{index:012d}"
        for index, item in enumerate(optional, 1)
    })
    ranked = rank_candidates([
        v2_candidate(
            "HASH", evidence=(*base.evidence, *optional),
            lineage=replace(base.lineage, evidence_receipt_ids=receipts),
        )
    ], contract_version=2)
    value = build_evidence_packet(
        ranked,
        limits=PacketLimits(max_serialized_bytes=12_000),
        contract_version=2,
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
    ).to_dict()

    row = value["research_candidates"][0]
    body = {key: item for key, item in row.items() if key != "candidate_hash"}
    expected = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    assert any(
        omission["reason"] == "serialized_byte_limit"
        for omission in value["omissions"]
    )
    assert row["candidate_hash"] == expected
    assert value["action_candidates"][0]["candidate_hash"] == expected


def test_research_suitability_canonical_hash_golden_vectors():
    for vector in HASH_VECTORS["vectors"]:
        canonical = json.dumps(
            vector["value"], ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        assert canonical == vector["canonical_json"]
        assert hashlib.sha256(canonical.encode()).hexdigest() == vector["sha256"]
    assert HASH_VECTORS["vectors"][1]["sha256"] != HASH_VECTORS["vectors"][2]["sha256"]
