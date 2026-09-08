"""Ticker-independent market events and bounded thematic reverse discovery."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
import unicodedata
import uuid

from lib.intelligence.normalize import SourceItem
from lib.intelligence.themes import evidence_key


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TAXONOMY = _ROOT / "config" / "theme_taxonomy.json"
_EVENT_TYPES = frozenset({
    "statement", "forecast", "proposed_policy", "effective_policy",
    "announced_funding", "awarded_funding", "contract", "capacity",
    "demand", "supply", "earnings", "transaction",
})
_QUERY_KINDS = frozenset({"theme_search"})


def _text(value: object, *, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} is invalid")
    return normalized


def _identifier(value: object, label: str) -> str:
    result = _text(value, maximum=80, label=label)
    if re.fullmatch(r"[a-z][a-z0-9_]{2,79}", result) is None:
        raise ValueError(f"{label} is invalid")
    return result


def _timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TaxonomyEdge:
    edge_id: str
    direction: str
    role: str
    query_terms: tuple[str, ...]
    geography: str
    horizon: str
    evidence_requirement: str
    adverse_path: bool
    invalidation_rule: str


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    theme_id: str
    label: str
    synonyms: tuple[str, ...]
    geography: str
    horizon: str
    value_chain: tuple[TaxonomyEdge, ...]


@dataclass(frozen=True, slots=True)
class ThemeTaxonomy:
    version: int
    themes: Mapping[str, ThemeDefinition]
    canonical_json: str
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ThemeMatch:
    theme_id: str
    matched_terms: tuple[str, ...]
    confidence: str


@dataclass(frozen=True, slots=True)
class EventDraft:
    event_id: str
    event_type: str
    title: str
    summary: str
    occurred_at: datetime | None
    effective_at: datetime | None
    theme_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    security_ids: tuple[str, ...]
    evidence: tuple[SourceItem, ...]
    research_state: str
    limitations: tuple[str, ...]
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ValueChainHypothesis:
    hypothesis_id: str
    event_id: str
    theme_id: str
    direction: str
    role: str
    query_terms: tuple[str, ...]
    theme_terms: tuple[str, ...]
    geography: str
    horizon: str
    evidence_requirement: str
    adverse_path: bool
    invalidation_rule: str
    status: str = "hypothesis"
    exposure_supported: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ReverseDiscoveryTask:
    task_id: str
    hypothesis_id: str
    event_id: str
    theme_id: str
    role: str
    direction: str
    provider: str
    capability_id: str
    query_kind: str
    query_text: str
    geography: str
    horizon: str
    adverse_path: bool
    evidence_requirement: str
    invalidation_rule: str
    dependency_ids: tuple[str, ...]
    max_attempts: int = 1
    execution_allowed: bool = False


def load_theme_taxonomy(path: str | Path | None = None) -> ThemeTaxonomy:
    """Load the checked-in taxonomy and reject action or company-list authority."""
    source = Path(path) if path is not None else _DEFAULT_TAXONOMY
    raw = source.read_bytes()
    if not raw or len(raw) > 256 * 1024:
        raise ValueError("theme taxonomy exceeds byte bound")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("theme taxonomy is invalid") from None
    if not isinstance(document, dict) or set(document) != {
        "version", "execution_allowed", "themes",
    } or document.get("version") != 1 or document.get("execution_allowed") is not False:
        raise ValueError("theme taxonomy contract is invalid")
    rows = document.get("themes")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("theme taxonomy rows are invalid")
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    forbidden = re.compile(r'"(?:buy|sell|portfolio_mutation|execution_allowed)"\s*:\s*(?:true|"[^"]+")', re.I)
    if forbidden.search(encoded):
        raise ValueError("theme taxonomy contains action authority")
    themes: dict[str, ThemeDefinition] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "theme_id", "label", "synonyms", "geography", "horizon", "value_chain",
        }:
            raise ValueError("theme taxonomy row is invalid")
        theme_id = _identifier(row["theme_id"], "theme ID")
        if theme_id in themes:
            raise ValueError("theme taxonomy ID is duplicated")
        synonyms_raw = row["synonyms"]
        edges_raw = row["value_chain"]
        if not isinstance(synonyms_raw, list) or not 1 <= len(synonyms_raw) <= 32 \
                or not isinstance(edges_raw, list) or not 1 <= len(edges_raw) <= 32:
            raise ValueError("theme taxonomy row is invalid")
        synonyms = tuple(dict.fromkeys(
            _text(term, maximum=80, label="theme synonym").casefold()
            for term in synonyms_raw
        ))
        edges: list[TaxonomyEdge] = []
        for edge in edges_raw:
            if not isinstance(edge, dict) or set(edge) != {
                "edge_id", "direction", "role", "query_terms", "geography",
                "horizon", "evidence_requirement", "adverse_path", "invalidation_rule",
            } or not isinstance(edge["adverse_path"], bool):
                raise ValueError("taxonomy edge is invalid")
            terms = edge["query_terms"]
            if not isinstance(terms, list) or not 1 <= len(terms) <= 16:
                raise ValueError("taxonomy edge query terms are invalid")
            edges.append(TaxonomyEdge(
                edge_id=_identifier(edge["edge_id"], "edge ID"),
                direction=_identifier(edge["direction"], "edge direction"),
                role=_identifier(edge["role"], "edge role"),
                query_terms=tuple(dict.fromkeys(
                    _text(term, maximum=80, label="edge query term").casefold()
                    for term in terms
                )),
                geography=_text(edge["geography"], maximum=80, label="edge geography"),
                horizon=_text(edge["horizon"], maximum=80, label="edge horizon"),
                evidence_requirement=_text(
                    edge["evidence_requirement"], maximum=240, label="edge evidence requirement"
                ),
                adverse_path=edge["adverse_path"],
                invalidation_rule=_text(
                    edge["invalidation_rule"], maximum=240, label="edge invalidation rule"
                ),
            ))
        if len({edge.edge_id for edge in edges}) != len(edges):
            raise ValueError("taxonomy edge ID is duplicated")
        themes[theme_id] = ThemeDefinition(
            theme_id=theme_id,
            label=_text(row["label"], maximum=120, label="theme label"),
            synonyms=synonyms,
            geography=_text(row["geography"], maximum=80, label="theme geography"),
            horizon=_text(row["horizon"], maximum=80, label="theme horizon"),
            value_chain=tuple(edges),
        )
    return ThemeTaxonomy(1, MappingProxyType(themes), encoded, False)


def _normalized_claim(item: SourceItem) -> str:
    supplied = item.metadata.get("claim_key") if isinstance(item.metadata, Mapping) else None
    source = supplied if isinstance(supplied, str) and supplied.strip() else item.summary or item.title
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKC", source).casefold()).strip()[:2_000]


def _contains_phrase(haystack: str, phrase: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", phrase.casefold())
    if not tokens:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s\-/]+".join(map(re.escape, tokens)) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def match_themes(item: SourceItem, taxonomy: ThemeTaxonomy) -> tuple[ThemeMatch, ...]:
    if not isinstance(item, SourceItem) or not isinstance(taxonomy, ThemeTaxonomy):
        raise TypeError("theme matching requires canonical source items and a taxonomy")
    text = unicodedata.normalize("NFKC", f"{item.title} {item.summary}").casefold()
    matches: list[ThemeMatch] = []
    for theme_id, theme in taxonomy.themes.items():
        terms = tuple(term for term in theme.synonyms if _contains_phrase(text, term))
        if terms:
            matches.append(ThemeMatch(
                theme_id=theme_id,
                matched_terms=terms,
                confidence="multiple_terms" if len(terms) > 1 else "single_term",
            ))
    return tuple(sorted(matches, key=lambda row: (-len(row.matched_terms), row.theme_id)))


def _event_type(item: SourceItem) -> str:
    declared = item.metadata.get("event_type") if isinstance(item.metadata, Mapping) else None
    if isinstance(declared, str) and declared in _EVENT_TYPES:
        return declared
    text = f"{item.title} {item.summary}".casefold()
    if re.search(r"\b(proposed|proposes|proposal|open for comment)\b", text):
        return "proposed_policy"
    if item.effective_at is not None and re.search(r"\b(rule|policy|effective|regulation)\b", text):
        return "effective_policy"
    if re.search(r"\b(award|awarded|grant|granted)\b", text) and re.search(r"\b(fund|funding|grant|award)\b", text):
        return "awarded_funding"
    if re.search(r"\b(announce|announces|announced|plans?)\b", text) and "fund" in text:
        return "announced_funding"
    if re.search(r"\b(forecast|outlook|expects?|projected?)\b", text):
        return "forecast"
    if re.search(r"\b(contract|purchase order|agreement)\b", text):
        return "contract"
    if re.search(r"\b(capacity|factory|plant|facility|production line)\b", text):
        return "capacity"
    if re.search(r"\bdemand\b", text):
        return "demand"
    if re.search(r"\bsupply\b", text):
        return "supply"
    if re.search(r"\b(earnings|revenue|quarterly results?|eps)\b", text):
        return "earnings"
    if re.search(r"\b(acquisition|merger|transaction|acquires?|divestiture)\b", text):
        return "transaction"
    return "statement"


def _publisher_identity(item: SourceItem) -> str:
    value = item.metadata.get("publisher_id") or item.metadata.get("publisher_domain") \
        or item.metadata.get("domain")
    return str(value or item.provider).strip().casefold()[:200]


def _canonical_event_identity(
    event_type: str,
    claim: str,
    claim_polarity: str,
    occurred_at: datetime | None,
    effective_at: datetime | None,
    evidence: Sequence[SourceItem],
) -> str:
    canonical = json.dumps({
        "claim": claim,
        "claim_polarity": claim_polarity,
        "effective_at": effective_at.isoformat() if effective_at else None,
        "event_type": event_type,
        "evidence": [evidence_key(item) for item in evidence],
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
        "publishers": sorted({_publisher_identity(item) for item in evidence}),
        "upstream": sorted({str(item.upstream_item_id or item.canonical_url or item.content_hash) for item in evidence}),
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-event-draft:{hashlib.sha256(canonical.encode()).hexdigest()}"))


def detect_events(
    items: Sequence[SourceItem], taxonomy: ThemeTaxonomy
) -> tuple[EventDraft, ...]:
    """Build events from claims first; ticker and entity resolution happens later."""
    if not isinstance(taxonomy, ThemeTaxonomy):
        raise TypeError("event detection requires a theme taxonomy")
    grouped: dict[
        tuple[str, str, str, datetime | None, datetime | None], list[SourceItem]
    ] = defaultdict(list)
    for item in items:
        if not isinstance(item, SourceItem):
            raise TypeError("event evidence must contain canonical SourceItem values")
        event_type = _event_type(item)
        occurred = _timestamp(item.published_at)
        effective = _timestamp(item.effective_at)
        declared_polarity = item.metadata.get("polarity")
        polarity = str(declared_polarity or item.claim_polarity or "unknown").casefold()
        grouped[(event_type, _normalized_claim(item), polarity, occurred, effective)].append(item)
    events: list[EventDraft] = []
    for (event_type, claim, polarity, occurred, effective), values in grouped.items():
        evidence = tuple(sorted(values, key=evidence_key))
        declared = {
            str(item.metadata.get("theme_id"))
            for item in evidence
            if isinstance(item.metadata.get("theme_id"), str) and item.metadata.get("theme_id")
        }
        matched = {
            match.theme_id for item in evidence for match in match_themes(item, taxonomy)
        }
        themes = tuple(sorted(declared | matched))
        limitations: list[str] = []
        if not themes:
            themes = ("unclassified_market_event",)
            limitations.append("theme_unresolved")
        entity_ids = tuple(sorted({identity for item in evidence for identity in item.entity_ids}))
        security_ids = tuple(sorted({identity for item in evidence for identity in item.security_ids}))
        if not security_ids:
            limitations.append("security_unresolved")
        title = evidence[0].title
        summary = evidence[0].summary
        events.append(EventDraft(
            event_id=_canonical_event_identity(
                event_type, claim, polarity, occurred, effective, evidence
            ),
            event_type=event_type,
            title=title,
            summary=summary,
            occurred_at=occurred,
            effective_at=effective,
            theme_ids=themes,
            entity_ids=entity_ids,
            security_ids=security_ids,
            evidence=evidence,
            research_state="observed" if "theme_unresolved" not in limitations else "unresolved",
            limitations=tuple(limitations),
            execution_allowed=False,
        ))
    return tuple(sorted(events, key=lambda row: row.event_id))


def expand_value_chain(
    event: EventDraft, taxonomy: ThemeTaxonomy
) -> tuple[ValueChainHypothesis, ...]:
    rows: list[ValueChainHypothesis] = []
    for theme_id in event.theme_ids:
        theme = taxonomy.themes.get(theme_id)
        if theme is None:
            continue
        for edge in theme.value_chain:
            identity = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"value-chain:{event.event_id}:{theme_id}:{edge.edge_id}",
            ))
            rows.append(ValueChainHypothesis(
                hypothesis_id=identity,
                event_id=event.event_id,
                theme_id=theme_id,
                direction=edge.direction,
                role=edge.role,
                query_terms=edge.query_terms,
                theme_terms=theme.synonyms,
                geography=edge.geography,
                horizon=edge.horizon,
                evidence_requirement=edge.evidence_requirement,
                adverse_path=edge.adverse_path,
                invalidation_rule=edge.invalidation_rule,
            ))
    return tuple(sorted(rows, key=lambda row: (row.theme_id, row.adverse_path, row.role)))


def build_reverse_discovery_tasks(
    event: EventDraft,
    hypotheses: Sequence[ValueChainHypothesis],
    *,
    max_tasks: int,
) -> tuple[ReverseDiscoveryTask, ...]:
    """Allocate a finite keyless search across distinct roles and adverse paths."""
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not 0 <= max_tasks <= 12:
        raise ValueError("reverse discovery task bound is invalid")
    applicable = [row for row in hypotheses if row.event_id == event.event_id]
    normal = sorted((row for row in applicable if not row.adverse_path), key=lambda row: (row.theme_id, row.role))
    adverse = sorted((row for row in applicable if row.adverse_path), key=lambda row: (row.theme_id, row.role))
    ordered: list[ValueChainHypothesis] = []
    if normal:
        ordered.append(normal.pop(0))
    if adverse and len(ordered) < max_tasks:
        ordered.append(adverse.pop(0))
    while len(ordered) < max_tasks and (normal or adverse):
        pool = normal if normal else adverse
        ordered.append(pool.pop(0))
        if adverse and pool is normal and len(ordered) < max_tasks:
            ordered.append(adverse.pop(0))
    dependencies = tuple(sorted({evidence_key(item) for item in event.evidence}))
    tasks: list[ReverseDiscoveryTask] = []
    for row in ordered[:max_tasks]:
        search_terms = tuple(dict.fromkeys((*row.query_terms[:3], *row.theme_terms[:1])))
        terms = " OR ".join(
            f'"{term}"' if " " in term else term for term in search_terms
        )
        query = f"({terms})"
        identity = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"reverse-discovery:{event.event_id}:{row.hypothesis_id}:{query}",
        ))
        tasks.append(ReverseDiscoveryTask(
            task_id=identity,
            hypothesis_id=row.hypothesis_id,
            event_id=event.event_id,
            theme_id=row.theme_id,
            role=row.role,
            direction=row.direction,
            provider="gdelt",
            capability_id="gdelt_theme_search",
            query_kind="theme_search",
            query_text=query,
            geography=row.geography,
            horizon=row.horizon,
            adverse_path=row.adverse_path,
            evidence_requirement=row.evidence_requirement,
            invalidation_rule=row.invalidation_rule,
            dependency_ids=dependencies,
        ))
    return tuple(tasks)


__all__ = [
    "EventDraft", "ReverseDiscoveryTask", "TaxonomyEdge", "ThemeDefinition",
    "ThemeMatch", "ThemeTaxonomy", "ValueChainHypothesis",
    "build_reverse_discovery_tasks", "detect_events", "expand_value_chain",
    "load_theme_taxonomy", "match_themes",
]
