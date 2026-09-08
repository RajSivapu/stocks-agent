"""Deterministic bounded evidence packets for suggestion-only analysis."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from lib.intelligence.normalize import SourceItem
from lib.intelligence.ranking import CandidateLineage, RankedCandidate, candidate_sort_key
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
    contract_version: int = 1
    v2_value: dict[str, object] | None = None

    @property
    def research_candidates(self) -> tuple[dict[str, object], ...]:
        if self.v2_value is None:
            return ()
        return tuple(self.v2_value["research_candidates"])  # type: ignore[arg-type]

    @property
    def action_candidates(self) -> tuple[dict[str, object], ...]:
        if self.v2_value is None:
            return ()
        return tuple(self.v2_value["action_candidates"])  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        if self.contract_version == 2:
            if self.v2_value is None:
                raise ValueError("v2 packet value is unavailable")
            return json.loads(json.dumps(self.v2_value, ensure_ascii=False))
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
    contract_version: int = 1,
    run_id: str | None = None,
    observed_at: str | None = None,
    omissions: Sequence[dict[str, object]] = (),
) -> EvidencePacket:
    _validate_limits(limits)
    if isinstance(policy_version, bool) or not isinstance(policy_version, int) or policy_version < 1:
        raise ValueError("policy_version must be a positive integer")
    if contract_version == 2:
        return _build_evidence_packet_v2(
            candidates, limits, coverage=dict(coverage or {}), policy_version=policy_version,
            run_id=run_id, observed_at=observed_at, prior_omissions=omissions,
        )
    if contract_version != 1:
        raise ValueError("packet contract version is unsupported")
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


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _fixed(value: Decimal) -> str:
    result = format(value.quantize(Decimal("0.000001")), "f")
    return "0.000000" if result == "-0.000000" else result


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _lineage(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "cash_revision": value.cash_revision,
        "evidence_receipt_ids": {
            key: value.evidence_receipt_ids[key] for key in sorted(value.evidence_receipt_ids)
        },
        "observed_at": value.observed_at,
        "policy_version": value.policy_version,
        "portfolio_revision": value.portfolio_revision,
        "quote_as_of": value.quote_as_of,
        "quote_expires_at": value.quote_expires_at,
        "quote_receipt_id": value.quote_receipt_id,
        "reference_expires_at": value.reference_expires_at,
        "reference_manifest_id": value.reference_manifest_id,
        "reference_revision": value.reference_revision,
        "run_id": value.run_id,
        "security_revision_id": value.security_revision_id,
    }


def _build_evidence_packet_v2(
    candidates: Sequence[RankedCandidate],
    limits: PacketLimits,
    *,
    coverage: dict[str, object],
    policy_version: int,
    run_id: str | None,
    observed_at: str | None,
    prior_omissions: Sequence[dict[str, object]],
) -> EvidencePacket:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("v2 packet requires persisted run identity")
    if not isinstance(observed_at, str) or not observed_at.endswith("Z"):
        raise ValueError("v2 packet requires canonical observation time")
    if any(candidate.research is None or candidate.suitability is None for candidate in candidates):
        raise ValueError("v2 packet requires v2 ranked candidates")
    coverage.setdefault("mode", "bounded")
    coverage.setdefault("complete_market_coverage", False)
    omitted: list[dict[str, object]] = []
    for value in prior_omissions:
        if not isinstance(value, dict) or not {"kind", "reason"} <= set(value):
            raise ValueError("pre-packet omission is invalid")
        omitted.append(dict(value))

    groups: dict[str, list[RankedCandidate]] = {}
    ticker_identities: dict[str, str] = {}
    for candidate in sorted(candidates, key=candidate_sort_key):
        if candidate.ticker is not None:
            prior = ticker_identities.setdefault(candidate.ticker, candidate.candidate_key)
            if prior != candidate.candidate_key:
                raise ValueError("conflicting duplicate ticker identity")
        groups.setdefault(candidate.candidate_key, []).append(candidate)
    ordered_groups = sorted(
        groups.values(),
        key=lambda rows: (
            -max(row.research.priority_score for row in rows if row.research),
            rows[0].candidate_key,
        ),
    )
    research_groups: list[list[RankedCandidate]] = []
    for rows in ordered_groups:
        if any(
            row.research is None or row.research.research_state == "research_rejected"
            for row in rows
        ):
            omitted.append({
                "candidate_key": rows[0].candidate_key,
                "item_id": None,
                "kind": "candidate",
                "reason": "research_requirements_failed",
                "stage": "packet",
            })
        else:
            research_groups.append(rows)
    ordered_groups = research_groups
    retained_groups = ordered_groups[: limits.max_candidates]
    for rows in ordered_groups[limits.max_candidates :]:
        omitted.append({
            "candidate_key": rows[0].candidate_key,
            "item_id": None,
            "kind": "candidate",
            "reason": "candidate_limit",
            "stage": "packet",
        })

    evidence_objects: dict[str, dict[str, object]] = {}
    research_rows: list[dict[str, object]] = []
    action_rows: list[dict[str, object]] = []
    required_by_candidate: dict[str, set[str]] = {}
    for rows in retained_groups:
        research_values = [row.research for row in rows if row.research is not None]
        suitability_values = [row.suitability for row in rows if row.suitability is not None]
        identities = {
            (value.security_id, value.ticker, value.entity_id)
            for value in research_values
        }
        if len(identities) != 1:
            raise ValueError("conflicting duplicate candidate identity")
        suitability_components = {
            tuple(value.component_scores.items()) for value in suitability_values
        }
        if len(suitability_components) != 1:
            raise ValueError("conflicting duplicate candidate suitability")
        lineages = [value.lineage for value in suitability_values]
        if any(
            value is not None and (
                value.run_id != run_id or value.observed_at != observed_at
                or value.policy_version != policy_version
            )
            for value in lineages
        ):
            raise ValueError("candidate lineage does not match packet")
        lineage_bodies = {
            (
                value.run_id, value.observed_at, value.policy_version,
                value.reference_manifest_id, value.reference_revision,
                value.reference_expires_at, value.security_revision_id,
                value.quote_receipt_id, value.quote_as_of, value.quote_expires_at,
                value.portfolio_revision, value.cash_revision,
            )
            for value in lineages if value is not None
        }
        if (lineages and any(value is None for value in lineages) and any(value is not None for value in lineages)) \
                or len(lineage_bodies) > 1:
            raise ValueError("conflicting duplicate candidate lineage")
        evidence_by_id: dict[str, SourceItem] = {}
        exposure_ids: set[str] = set()
        supporting_ids: set[str] = set()
        opposing_ids: set[str] = set()
        receipts: dict[str, str] = {}
        for row, research in zip(rows, research_values, strict=True):
            evidence_by_id.update({evidence_key(item): item for item in research.evidence})
            exposure_ids.update(evidence_key(item) for item in row.exposure_evidence)
            supporting_ids.update(research.supporting_evidence_ids)
            opposing_ids.update(research.opposing_evidence_ids)
            for item_id, receipt_id in research.evidence_receipt_ids.items():
                if item_id in receipts and receipts[item_id] != receipt_id:
                    raise ValueError("conflicting duplicate evidence receipt")
                receipts[item_id] = receipt_id
        required = exposure_ids | supporting_ids | opposing_ids
        required_by_candidate[rows[0].candidate_key] = required
        priority = lambda item: (
            0 if evidence_key(item) in exposure_ids else
            1 if evidence_key(item) in opposing_ids else
            2 if evidence_key(item) in supporting_ids else
            3,
            *_priority(item),
        )
        ordered_evidence = sorted(evidence_by_id.values(), key=priority)
        kept = ordered_evidence[: limits.max_evidence_per_candidate]
        kept_ids = {evidence_key(item) for item in kept}
        for item in reversed(ordered_evidence[limits.max_evidence_per_candidate :]):
            omitted.append({
                "candidate_key": rows[0].candidate_key,
                "item_id": evidence_key(item),
                "kind": "evidence",
                "reason": "evidence_limit",
                "stage": "packet",
            })
        required_missing = required - kept_ids
        limitations = set(reason for value in research_values for reason in value.limitations)
        if required_missing:
            limitations.add("required_evidence_did_not_fit")
        evidence_refs: list[dict[str, object]] = []
        for item in kept:
            item_id = evidence_key(item)
            if item_id not in receipts:
                raise ValueError("v2 evidence requires source receipt lineage")
            role = "opposing" if item_id in opposing_ids else "supporting"
            relationship_claim_type = "issuer_exposure" if item_id in exposure_ids else str(
                item.metadata.get("claim_type") or "event"
            )[:80]
            evidence_refs.append({
                "claim_type": relationship_claim_type,
                "item_id": item_id,
                "relationship_eligible": item_id in exposure_ids,
                "role": role,
            })
            evidence_objects.setdefault(item_id, {
                "authority": item.authority,
                "canonical_url": item.canonical_url,
                "claim_type": str(item.metadata.get("claim_type") or "event")[:80],
                "content_hash": item.content_hash,
                "effective_at": _timestamp(item.effective_at),
                "item_id": item_id,
                "normalized_text": item.summary[:limits.max_item_characters],
                "published_at": _timestamp(item.published_at),
                "reporting_at": _timestamp(item.reporting_at),
                "retrieved_at": _timestamp(item.retrieved_at),
                "source_identity": {
                    "provider": item.provider,
                    "receipt_id": receipts.get(item_id),
                    "upstream_item_id": item.upstream_item_id,
                },
            })
        suitability_state = (
            "vetoed" if any(value.state == "vetoed" for value in suitability_values)
            else "eligible" if all(value.state == "eligible" for value in suitability_values)
            else "unknown"
        )
        merged_lineage = None
        if lineages and lineages[0] is not None:
            first_lineage = lineages[0]
            merged_lineage = CandidateLineage(
                run_id=first_lineage.run_id,
                observed_at=first_lineage.observed_at,
                policy_version=first_lineage.policy_version,
                reference_manifest_id=first_lineage.reference_manifest_id,
                reference_revision=first_lineage.reference_revision,
                reference_expires_at=first_lineage.reference_expires_at,
                security_revision_id=first_lineage.security_revision_id,
                quote_receipt_id=first_lineage.quote_receipt_id,
                quote_as_of=first_lineage.quote_as_of,
                quote_expires_at=first_lineage.quote_expires_at,
                evidence_receipt_ids=receipts,
                portfolio_revision=first_lineage.portfolio_revision,
                cash_revision=first_lineage.cash_revision,
            )
        suitability_body = {
            "component_scores": {
                key: _fixed(value)
                for key, value in sorted(suitability_values[0].component_scores.items())
            },
            "lineage": _lineage(merged_lineage),
            "missing_reasons": sorted({reason for value in suitability_values for reason in value.missing_reasons}),
            "state": suitability_state,
            "veto_reasons": sorted({reason for value in suitability_values for reason in value.veto_reasons}),
        }
        if required_missing and suitability_body["state"] == "eligible":
            suitability_body["state"] = "unknown"
            suitability_body["missing_reasons"].append("required_evidence_did_not_fit")
        suitability = {**suitability_body, "evaluation_hash": _hash(suitability_body)}
        state_order = {
            "research_rejected": 0, "observed": 1, "unresolved": 2,
            "resolved": 3, "exposure_supported": 4, "analysis_ready": 5,
        }
        best = max(research_values, key=lambda value: (
            value.priority_score, value.research_state, value.event_ids,
        ))
        aggregate_state = min(
            (value.research_state for value in research_values),
            key=state_order.__getitem__,
        )
        candidate_body = {
            "adverse_paths": sorted({path for value in research_values for path in value.adverse_paths}),
            "candidate_key": rows[0].candidate_key,
            "entity_id": best.entity_id,
            "event_ids": sorted({event_id for value in research_values for event_id in value.event_ids}),
            "evidence": evidence_refs,
            "exposure_fact_ids": sorted({fact_id for value in research_values for fact_id in value.exposure_fact_ids}),
            "limitations": sorted(limitations),
            "priority_components": {
                key: _fixed(value) for key, value in sorted(best.priority_components.items())
            },
            "priority_score": _fixed(max(value.priority_score for value in research_values)),
            "research_state": aggregate_state,
            "roles": sorted({role for value in research_values for role in value.roles}),
            "security_id": best.security_id,
            "suitability": suitability,
            "theme_ids": sorted({theme for value in research_values for theme in value.theme_ids}),
            "ticker": best.ticker,
        }
        candidate_row = {**candidate_body, "candidate_hash": _hash(candidate_body)}
        research_rows.append(candidate_row)
        if suitability["state"] == "eligible" and aggregate_state == "analysis_ready":
            action_rows.append({
                "candidate_hash": candidate_row["candidate_hash"],
                "candidate_key": rows[0].candidate_key,
                "suitability_hash": suitability["evaluation_hash"],
            })

    def materialize() -> dict[str, object]:
        action_by_key = {
            str(row["candidate_key"]): row for row in action_rows
        }
        for row in research_rows:
            suitability = row["suitability"]
            if not isinstance(suitability, dict):
                raise ValueError("v2 suitability is invalid")
            suitability_body = {
                key: value for key, value in suitability.items()
                if key != "evaluation_hash"
            }
            suitability["evaluation_hash"] = _hash(suitability_body)
            candidate_body = {
                key: value for key, value in row.items()
                if key != "candidate_hash"
            }
            row["candidate_hash"] = _hash(candidate_body)
            action = action_by_key.get(str(row["candidate_key"]))
            if action is not None:
                action["candidate_hash"] = row["candidate_hash"]
                action["suitability_hash"] = suitability["evaluation_hash"]
        referenced = {
            ref["item_id"]
            for row in research_rows
            for ref in row["evidence"]  # type: ignore[union-attr]
        }
        return {
            "action_candidates": action_rows,
            "contract_version": 2,
            "coverage": coverage,
            "evidence": [evidence_objects[item_id] for item_id in sorted(referenced)],
            "execution_allowed": False,
            "limitations": sorted({f"{row['kind']}:{row['reason']}" for row in omitted}),
            "observed_at": observed_at,
            "omissions": omitted,
            "policy_version": policy_version,
            "research_candidates": research_rows,
            "run_id": run_id,
        }

    value = materialize()
    while len(_canonical(value).encode()) > limits.max_serialized_bytes:
        removed = False
        for row in reversed(research_rows):
            refs = row["evidence"]
            if not isinstance(refs, list):
                continue
            required = required_by_candidate[str(row["candidate_key"])]
            for index in range(len(refs) - 1, -1, -1):
                item_id = str(refs[index]["item_id"])
                if item_id in required:
                    continue
                refs.pop(index)
                omitted.append({
                    "candidate_key": row["candidate_key"], "item_id": item_id,
                    "kind": "evidence", "reason": "serialized_byte_limit", "stage": "packet",
                })
                removed = True
                break
            if removed:
                break
        if not removed and action_rows:
            action = action_rows.pop()
            omitted.append({
                "candidate_key": action["candidate_key"], "item_id": None,
                "kind": "action_candidate", "reason": "required_evidence_byte_limit", "stage": "packet",
            })
            removed = True
        if not removed and research_rows:
            row = research_rows.pop()
            action_rows[:] = [value for value in action_rows if value["candidate_key"] != row["candidate_key"]]
            omitted.append({
                "candidate_key": row["candidate_key"], "item_id": None,
                "kind": "candidate", "reason": "serialized_byte_limit", "stage": "packet",
            })
            removed = True
        if not removed:
            raise ValueError("serialized byte limit cannot hold v2 packet envelope")
        value = materialize()
    drops = tuple(PacketDrop(
        str(row["kind"]), str(row.get("candidate_key") or ""),
        str(row["item_id"]) if row.get("item_id") is not None else None,
        str(row["reason"]),
    ) for row in omitted)
    return EvidencePacket((), drops, coverage, policy_version, 2, value)


__all__ = [
    "EvidencePacket",
    "PacketCandidate",
    "PacketDrop",
    "PacketEvidence",
    "build_evidence_packet",
]
