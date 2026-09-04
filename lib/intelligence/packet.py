"""Deterministic bounded evidence packets for suggestion-only analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from lib.intelligence.normalize import SourceItem
from lib.intelligence.ranking import RankedCandidate, candidate_sort_key
from lib.intelligence.themes import evidence_key
from lib.intelligence.types import PacketLimits


@dataclass(frozen=True, slots=True)
class PacketEvidence:
    item_id: str
    normalized_text: str
    authority: str


@dataclass(frozen=True, slots=True)
class PacketCandidate:
    candidate_key: str
    evidence: tuple[PacketEvidence, ...]


@dataclass(frozen=True, slots=True)
class PacketDrop:
    kind: str
    candidate_key: str
    item_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    candidates: tuple[PacketCandidate, ...]
    drops: tuple[PacketDrop, ...]
    coverage: dict[str, object]
    policy_version: int

    def to_dict(self) -> dict[str, object]:
        evidence_by_id: dict[str, PacketEvidence] = {}
        candidate_rows: list[dict[str, object]] = []
        for candidate in self.candidates:
            ids: list[str] = []
            for item in candidate.evidence:
                evidence_by_id.setdefault(item.item_id, item)
                ids.append(item.item_id)
            candidate_rows.append(
                {"candidate_key": candidate.candidate_key, "evidence_ids": ids}
            )
        drop_counts: dict[str, int] = {}
        for drop in self.drops:
            key = f"{drop.kind}:{drop.reason}"
            drop_counts[key] = drop_counts.get(key, 0) + 1
        limitations = [f"{key}={drop_counts[key]}" for key in sorted(drop_counts)]
        return {
            "candidates": candidate_rows,
            "evidence": [
                {
                    "item_id": item_id,
                    "normalized_text": evidence_by_id[item_id].normalized_text,
                }
                for item_id in sorted(evidence_by_id)
            ],
            "coverage": self.coverage,
            "limitations": limitations,
            "policy_version": self.policy_version,
        }

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")


def _priority(item: SourceItem) -> tuple[int, float, str]:
    authority = {"official": 0, "corroborating": 1, "market_data": 2, "radar": 3}.get(
        item.authority, 4
    )
    published = item.published_at.timestamp() if item.published_at else float("-inf")
    return authority, -published, evidence_key(item)


def _packet_evidence(item: SourceItem, character_limit: int) -> PacketEvidence:
    return PacketEvidence(
        item_id=evidence_key(item),
        normalized_text=item.summary[:character_limit],
        authority=item.authority,
    )


def _validate_limits(limits: PacketLimits) -> None:
    values = (
        limits.max_candidates,
        limits.max_evidence_per_candidate,
        limits.max_item_characters,
        limits.max_serialized_bytes,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise ValueError("packet limits must be positive integers")
    if (
        limits.max_candidates > 12
        or limits.max_evidence_per_candidate > 8
        or limits.max_item_characters > 2_000
        or limits.max_serialized_bytes > 98_304
    ):
        raise ValueError("packet limits exceed policy bounds")


def build_evidence_packet(
    candidates: Sequence[RankedCandidate],
    limits: PacketLimits = PacketLimits(),
    *,
    coverage: dict[str, object] | None = None,
    policy_version: int = 1,
) -> EvidencePacket:
    _validate_limits(limits)
    if isinstance(policy_version, bool) or not isinstance(policy_version, int) or policy_version < 1:
        raise ValueError("policy_version must be a positive integer")
    ordered = sorted(candidates, key=candidate_sort_key)
    drops: list[PacketDrop] = []
    eligible: list[RankedCandidate] = []
    for candidate in ordered:
        if candidate.qualified:
            eligible.append(candidate)
        else:
            drops.append(PacketDrop("candidate", candidate.candidate_key, None, "not_qualified"))
    retained_rankings = eligible[: limits.max_candidates]
    for candidate in reversed(eligible[limits.max_candidates :]):
        drops.append(PacketDrop("candidate", candidate.candidate_key, None, "candidate_limit"))

    mutable: list[tuple[str, list[PacketEvidence]]] = []
    for candidate in retained_rankings:
        unique = {evidence_key(item): item for item in candidate.evidence}
        evidence = sorted(unique.values(), key=_priority)
        kept = [
            _packet_evidence(item, limits.max_item_characters)
            for item in evidence[: limits.max_evidence_per_candidate]
        ]
        for item in reversed(evidence[limits.max_evidence_per_candidate :]):
            drops.append(
                PacketDrop(
                    "evidence",
                    candidate.candidate_key,
                    evidence_key(item),
                    "evidence_limit",
                )
            )
        mutable.append((candidate.candidate_key, kept))

    coverage_value = dict(coverage or {})
    coverage_value.setdefault("mode", "bounded")
    coverage_value.setdefault("complete_market_coverage", False)

    def materialize() -> EvidencePacket:
        return EvidencePacket(
            candidates=tuple(
                PacketCandidate(key, tuple(evidence)) for key, evidence in mutable
            ),
            drops=tuple(drops),
            coverage=coverage_value,
            policy_version=policy_version,
        )

    packet = materialize()
    while len(packet.to_json_bytes()) > limits.max_serialized_bytes:
        if mutable and len(mutable[-1][1]) > 1:
            key, evidence = mutable[-1]
            removed = evidence.pop()
            drops.append(PacketDrop("evidence", key, removed.item_id, "serialized_byte_limit"))
        elif mutable:
            key, evidence = mutable.pop()
            for removed in reversed(evidence):
                drops.append(
                    PacketDrop("evidence", key, removed.item_id, "serialized_byte_limit")
                )
            drops.append(PacketDrop("candidate", key, None, "serialized_byte_limit"))
        else:
            raise ValueError("serialized byte limit cannot hold packet envelope")
        packet = materialize()
    return packet


__all__ = [
    "EvidencePacket",
    "PacketCandidate",
    "PacketDrop",
    "PacketEvidence",
    "build_evidence_packet",
]
