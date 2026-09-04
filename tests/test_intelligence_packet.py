from dataclasses import replace
from decimal import Decimal

from lib.intelligence.packet import build_evidence_packet
from lib.intelligence.ranking import rank_candidates
from lib.intelligence.types import PacketLimits
from tests.test_intelligence_ranking import candidate
from tests.test_intelligence_themes import source_item


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
        rows.append(candidate(f"T{candidate_index:02d}", evidence=evidence))
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
