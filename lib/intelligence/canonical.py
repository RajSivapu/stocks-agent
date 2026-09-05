"""Lossless canonical event/ranking bodies shared by producers and SQL readers."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


EVENT_FIELDS = (
    "event_type", "title", "summary", "occurred_at", "effective_at",
    "materiality", "confidence", "evidence_item_ids",
)
RANKING_FIELDS = (
    "event_id", "candidate_key", "ticker", "rank", "component_scores",
    "total_score", "qualified", "veto_reasons", "exposure_item_ids",
)


def _six_places(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def canonical_event(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != set(EVENT_FIELDS):
        raise ValueError("event canonical fields are invalid")
    return {
        **{field: value[field] for field in EVENT_FIELDS},
        "materiality": _six_places(value["materiality"]),
        "confidence": _six_places(value["confidence"]),
    }


def canonical_ranking(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != set(RANKING_FIELDS):
        raise ValueError("ranking canonical fields are invalid")
    components = value["component_scores"]
    if not isinstance(components, Mapping):
        raise ValueError("ranking component scores are invalid")
    return {
        **{field: value[field] for field in RANKING_FIELDS},
        "component_scores": {str(key): _six_places(score) for key, score in components.items()},
        "total_score": _six_places(value["total_score"]),
    }


EVENT_CANONICAL_SQL = """jsonb_build_object(
    'event_type',event_type,'title',title,'summary',summary,
    'occurred_at',CASE WHEN occurred_at IS NULL THEN NULL ELSE to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') END,
    'effective_at',CASE WHEN effective_at IS NULL THEN NULL ELSE to_char(effective_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') END,
    'materiality',to_char(materiality,'FM999999999990.000000'),
    'confidence',to_char(confidence,'FM999999999990.000000'),
    'evidence_item_ids',evidence_item_ids)"""

RANKING_CANONICAL_SQL = """jsonb_build_object(
    'event_id',event_id::text,'candidate_key',candidate_key,'ticker',ticker,'rank',rank,
    'component_scores',component_scores,'total_score',to_char(total_score,'FM999999999990.000000'),
    'qualified',qualified,'veto_reasons',veto_reasons,'exposure_item_ids',exposure_item_ids)"""


__all__ = [
    "EVENT_CANONICAL_SQL", "RANKING_CANONICAL_SQL", "canonical_event", "canonical_ranking",
]
