"""Evidence-bearing event-to-security relationship proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from lib.intelligence.normalize import SourceItem
from lib.intelligence.themes import MarketEvent, evidence_key


_EXPOSURE_KINDS = frozenset(
    {"filing", "contract", "backlog", "revenue", "capacity", "official_fund"}
)
_DIRECT_ROLES = frozenset({"direct", "issuer", "beneficiary", "exposure"})


def exposure_kind(item: SourceItem) -> str | None:
    direct = getattr(item, "exposure_kind", None)
    value = direct if direct is not None else item.metadata.get("exposure_kind")
    return str(value).strip().casefold() if value else None


def qualifies_exposure(items: Sequence[SourceItem]) -> bool:
    return any(
        item.authority == "official" and exposure_kind(item) in _EXPOSURE_KINDS
        for item in items
    )


def _evidence_priority(item: SourceItem) -> tuple[int, str]:
    authority = {"official": 0, "corroborating": 1, "market_data": 2, "radar": 3}.get(
        item.authority, 4
    )
    return authority, evidence_key(item)


@dataclass(frozen=True, slots=True)
class EventRelationship:
    event_id: str
    source_kind: str
    source_key: str
    target_kind: str
    target_key: str
    ticker: str
    role: str
    relationship_type: str
    evidence: tuple[SourceItem, ...]
    exposure_evidence: tuple[SourceItem, ...]
    exposure_status: str
    eligible_for_ranking: bool
    hypothesis: bool
    missing_reasons: tuple[str, ...]
    dropped_evidence_keys: tuple[str, ...]


def propose_relation(
    event: MarketEvent,
    *,
    ticker: str,
    role: str,
    evidence: Sequence[SourceItem],
) -> EventRelationship:
    ticker_value = str(ticker).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker_value):
        raise ValueError("ticker must be canonical")
    role_value = "_".join(str(role).strip().casefold().split())[:80]
    if not role_value:
        raise ValueError("relationship role is required")
    unique = {evidence_key(item): item for item in evidence}
    ordered = sorted(unique.values(), key=_evidence_priority)
    retained = tuple(ordered[:8])
    dropped = tuple(evidence_key(item) for item in reversed(ordered[8:]))
    exposures = tuple(item for item in retained if qualifies_exposure((item,)))
    eligible = bool(retained) and bool(exposures)
    missing: list[str] = []
    if not retained:
        missing.append("relationship_evidence_required")
    if not exposures:
        missing.append("authoritative_exposure_required")
    relationship_type = "direct" if role_value in _DIRECT_ROLES else "second_order"
    return EventRelationship(
        event_id=event.event_id,
        source_kind="event",
        source_key=event.event_id,
        target_kind="security",
        target_key=ticker_value,
        ticker=ticker_value,
        role=role_value,
        relationship_type=relationship_type,
        evidence=retained,
        exposure_evidence=exposures,
        exposure_status="qualified" if eligible else "insufficient",
        eligible_for_ranking=eligible,
        hypothesis=not eligible,
        missing_reasons=tuple(missing),
        dropped_evidence_keys=dropped,
    )


__all__ = ["EventRelationship", "exposure_kind", "propose_relation", "qualifies_exposure"]
