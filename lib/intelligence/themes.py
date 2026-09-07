"""Deterministic seed and evidence-gated dynamic theme construction."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from urllib.parse import urlsplit
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


def source_dynamic_theme_label(title: object) -> str | None:
    """Derive one bounded proposal only from a retained source headline."""
    if not isinstance(title, str):
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", title).split())
    if not normalized:
        return None
    candidate = re.split(r"\s*(?::|\||—|–)\s*", normalized, maxsplit=1)[0]
    tokens = re.findall(r"[a-z0-9]+", candidate.casefold())
    if not 3 <= len(tokens) <= 12 or not any(token.isalpha() for token in tokens):
        return None
    label = " ".join(tokens)
    return label if len(label) <= 120 else None


def source_syndication_fingerprint(title: object, claim: object = None) -> str | None:
    """Identify equivalent source wording without publisher or mirror URL data."""
    if not isinstance(title, str):
        return None
    headline = " ".join(re.findall(
        r"[a-z0-9]+", unicodedata.normalize("NFKC", title).casefold()
    ))[:500]
    if not headline:
        return None
    claim_text = ""
    if isinstance(claim, str):
        claim_text = " ".join(re.findall(
            r"[a-z0-9]+", unicodedata.normalize("NFKC", claim).casefold()
        ))[:2_000]
    canonical = json.dumps(
        {"claim": claim_text, "headline": headline},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def publisher_identity(item: SourceItem) -> str:
    """Return a stable publisher identity rather than an adapter identity."""
    supplied = (
        item.metadata.get("publisher_id")
        or item.metadata.get("publisher_domain")
        or item.metadata.get("domain")
    ) if hasattr(item.metadata, "get") else None
    if supplied:
        return str(supplied).strip().casefold()[:200]
    if item.canonical_url:
        try:
            host = urlsplit(item.canonical_url).hostname
        except ValueError:
            host = None
        if host:
            return host.casefold().rstrip(".")
    return item.provider


def upstream_identity(item: SourceItem) -> str:
    """Return an original-story identity so syndicated copies count once."""
    supplied = (
        item.metadata.get("upstream_identity")
        or item.metadata.get("syndication_id")
        or item.metadata.get("canonical_article_id")
    ) if hasattr(item.metadata, "get") else None
    return str(
        supplied or item.upstream_item_id or item.canonical_url or item.content_hash
    ).strip().casefold()[:512]


def syndication_fingerprint(item: SourceItem) -> str:
    """Return a mirror-independent content identity for corroboration."""
    if not isinstance(item, SourceItem):
        raise TypeError("syndication evidence must be a canonical SourceItem")
    claim = item.metadata.get("claim_key") if hasattr(item.metadata, "get") else None
    fingerprint = source_syndication_fingerprint(item.title, claim)
    if fingerprint is None:
        raise ValueError("syndication evidence requires a headline")
    return fingerprint


def _syndication_attribution(item: SourceItem) -> str | None:
    if not hasattr(item.metadata, "get"):
        return None
    supplied = (
        item.metadata.get("syndication_id")
        or item.metadata.get("canonical_article_id")
        or item.metadata.get("wire_story_id")
        or item.metadata.get("original_story_id")
    )
    if not isinstance(supplied, str):
        return None
    normalized = " ".join(re.findall(
        r"[a-z0-9]+", unicodedata.normalize("NFKC", supplied).casefold()
    ))[:512]
    return normalized or None


def _syndication_groups(items: Sequence[SourceItem]) -> tuple[tuple[SourceItem, ...], ...]:
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    key_owner: dict[str, int] = {}
    for index, item in enumerate(items):
        keys = {f"content:{syndication_fingerprint(item)}"}
        attribution = _syndication_attribution(item)
        if attribution is not None:
            keys.add(f"attribution:{attribution}")
        for key in sorted(keys):
            owner = key_owner.setdefault(key, index)
            union(index, owner)
    grouped: dict[int, list[SourceItem]] = {}
    for index, item in enumerate(items):
        grouped.setdefault(find(index), []).append(item)
    return tuple(tuple(grouped[key]) for key in sorted(grouped))


def _independent_story_corroboration(
    groups: Sequence[Sequence[SourceItem]],
) -> bool:
    for left_index, left in enumerate(groups):
        for right in groups[left_index + 1:]:
            if any(
                publisher_identity(left_item) != publisher_identity(right_item)
                and upstream_identity(left_item) != upstream_identity(right_item)
                for left_item in left for right_item in right
            ):
                return True
    return False


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
    requested_labels: Iterable[str] = (),
) -> ThemeProposal:
    """Return a stable proposal while making every failed gate explicit."""
    fingerprint = theme_fingerprint(label)
    accepted = _accepted_items(evidence)
    coverage = " ".join(str(coverage_label or "").split())[:500]
    non_hypothesis = tuple(item for item in accepted if item.authority != "hypothesis")
    story_groups = _syndication_groups(non_hypothesis)
    corroborated = _independent_story_corroboration(story_groups)
    missing: list[str] = []
    if len(accepted) < 2:
        missing.append("requires_two_accepted_items")
    if len(non_hypothesis) >= 2 and len(story_groups) < 2:
        missing.append("syndicated_evidence_not_independent")
    if not corroborated:
        missing.append("publisher_independent_corroboration_required")
    requested_fingerprints = {
        theme_fingerprint(value) for value in requested_labels
        if isinstance(value, str) and value.strip()
    }
    if fingerprint in requested_fingerprints:
        missing.append("requested_taxonomy_label_not_evidence")
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
    "publisher_identity",
    "propose_dynamic_theme",
    "source_dynamic_theme_label",
    "source_syndication_fingerprint",
    "syndication_fingerprint",
    "theme_fingerprint",
    "upstream_identity",
]
