"""Deterministic seed and evidence-gated dynamic theme construction."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from lib.intelligence.dedupe import RunItemDisposition
from lib.intelligence.normalize import SourceItem


SEED_THEMES = (
    "macro_policy",
    "technology_ai_semiconductors",
    "energy_nuclear_grid",
    "industrial_infrastructure",
    "critical_minerals_magnets",
    "healthcare",
    "consumer",
    "defense_trade_geopolitics",
    "earnings_ma",
)

_SCORE_QUANTUM = Decimal("0.000001")


def _fixed_score(value: Decimal | int | str, field: str) -> Decimal:
    try:
        score = Decimal(str(value)).quantize(_SCORE_QUANTUM)
    except Exception as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not score.is_finite() or score < 0 or score > 1:
        raise ValueError(f"{field} must be between zero and one")
    return score


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def theme_fingerprint(label: str) -> str:
    if not isinstance(label, str):
        raise ValueError("theme label must be text")
    normalized = unicodedata.normalize("NFKC", label).casefold()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        raise ValueError("theme label is required")
    return "_".join(tokens)


def evidence_key(item: SourceItem) -> str:
    supplied = item.metadata.get("item_id") if hasattr(item.metadata, "get") else None
    if supplied:
        return str(supplied)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-source:{item.content_hash}"))


def _accepted_items(
    items: Iterable[SourceItem | RunItemDisposition],
) -> tuple[SourceItem, ...]:
    accepted: dict[str, SourceItem] = {}
    for value in items:
        if isinstance(value, RunItemDisposition):
            if value.disposition != "accepted":
                continue
            item = value.item
        else:
            item = value
        if not isinstance(item, SourceItem):
            raise TypeError("theme evidence must contain canonical SourceItem values")
        accepted.setdefault(evidence_key(item), item)
    return tuple(accepted[key] for key in sorted(accepted))


@dataclass(frozen=True, slots=True)
class ThemeProposal:
    theme_id: str
    label: str
    fingerprint: str
    evidence: tuple[SourceItem, ...]
    coverage_label: str
    eligible: bool
    missing_reasons: tuple[str, ...]


def propose_dynamic_theme(
    label: str,
    evidence: Iterable[SourceItem | RunItemDisposition],
    *,
    coverage_label: str,
) -> ThemeProposal:
    """Return a stable proposal while making every failed gate explicit."""
    fingerprint = theme_fingerprint(label)
    accepted = _accepted_items(evidence)
    coverage = " ".join(str(coverage_label or "").split())[:500]
    non_hypothesis_providers = {
        item.provider for item in accepted if item.authority != "hypothesis"
    }
    corroborated = any(item.authority == "official" for item in accepted) or len(
        non_hypothesis_providers
    ) >= 2
    missing: list[str] = []
    if len(accepted) < 2:
        missing.append("requires_two_accepted_items")
    if not corroborated:
        missing.append("authoritative_or_corroborating_source_required")
    if fingerprint in {theme_fingerprint(seed) for seed in SEED_THEMES}:
        missing.append("not_novel_from_seed_taxonomy")
    if not coverage:
        missing.append("coverage_label_required")
    return ThemeProposal(
        theme_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-theme:{fingerprint}")),
        label=" ".join(label.split())[:200],
        fingerprint=fingerprint,
        evidence=accepted,
        coverage_label=coverage,
        eligible=not missing,
        missing_reasons=tuple(missing),
    )


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    event_type: str
    title: str
    summary: str
    occurred_at: datetime | None
    effective_at: datetime | None
    materiality: Decimal
    confidence: Decimal
    evidence: tuple[SourceItem, ...]
    theme_ids: tuple[str, ...]
    content_hash: str


def build_market_event(
    *,
    event_type: str,
    title: str,
    summary: str,
    materiality: Decimal | int | str,
    confidence: Decimal | int | str,
    evidence: Sequence[SourceItem],
    theme_ids: Sequence[str],
    occurred_at: datetime | None = None,
    effective_at: datetime | None = None,
) -> MarketEvent:
    event_type_value = " ".join(str(event_type).split())[:80]
    title_value = " ".join(str(title).split())[:500]
    summary_value = " ".join(str(summary).split())[:4000]
    if not event_type_value or not title_value:
        raise ValueError("event type and title are required")
    accepted = _accepted_items(evidence)
    if not accepted:
        raise ValueError("market event requires accepted evidence")
    themes = tuple(sorted(dict.fromkeys(str(theme) for theme in theme_ids if str(theme))))
    if not themes:
        raise ValueError("market event requires a theme")
    materiality_value = _fixed_score(materiality, "materiality")
    confidence_value = _fixed_score(confidence, "confidence")
    occurred = _utc(occurred_at)
    effective = _utc(effective_at)
    canonical = json.dumps(
        {
            "confidence": str(confidence_value),
            "effective_at": effective.isoformat() if effective else None,
            "event_type": event_type_value,
            "evidence": [evidence_key(item) for item in accepted],
            "materiality": str(materiality_value),
            "occurred_at": occurred.isoformat() if occurred else None,
            "summary": summary_value,
            "theme_ids": themes,
            "title": title_value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return MarketEvent(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-event:{digest}")),
        event_type=event_type_value,
        title=title_value,
        summary=summary_value,
        occurred_at=occurred,
        effective_at=effective,
        materiality=materiality_value,
        confidence=confidence_value,
        evidence=accepted,
        theme_ids=themes,
        content_hash=digest,
    )


__all__ = [
    "SEED_THEMES",
    "MarketEvent",
    "ThemeProposal",
    "build_market_event",
    "evidence_key",
    "propose_dynamic_theme",
    "theme_fingerprint",
]
