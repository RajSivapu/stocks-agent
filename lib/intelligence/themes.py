"""Deterministic seed and evidence-gated dynamic theme construction."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from collections.abc import Mapping
from typing import Iterable, Literal, Sequence

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


_EPISODE_THEME_PATTERN = re.compile(
    r"^(?:[a-z][a-z0-9_]{2,79}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_EPISODE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$")

THEME_EPISODE_V2_PERSISTENCE_FIELDS = (
    "theme_id", "episode_id", "revision", "identity_version", "anchor_hash",
    "origin_run_id", "predecessor_revision_id", "predecessor_content_hash",
    "theme_mechanism", "subject_identity", "jurisdiction", "effective_period_start",
    "effective_period_end", "authoritative_id", "source_membership", "source_ids",
    "supporting_source_ids", "opposing_source_ids", "added_source_ids",
    "investigated_entity_ids", "missing_questions", "invalidation_conditions",
    "first_seen", "last_seen", "next_review_at", "expires_at", "state",
    "closure_reason", "reopen_reason", "execution_allowed",
)


def theme_episode_v2_persistence_document(value: Mapping[str, object]) -> dict[str, object]:
    """Return the single version-two semantic document hashed by every runtime."""
    return {field: value[field] for field in THEME_EPISODE_V2_PERSISTENCE_FIELDS}


def theme_episode_v2_anchor_document(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "authoritative_id": value["authoritative_id"],
        "effective_period": {
            "end": value["effective_period_end"],
            "start": value["effective_period_start"],
        },
        "identity_version": value["identity_version"],
        "jurisdiction": value["jurisdiction"],
        "subject_identity": value["subject_identity"],
        "theme_id": value["theme_id"],
        "theme_mechanism": value["theme_mechanism"],
    }


def theme_episode_v2_episode_id(anchor_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-theme-episode-v2:{anchor_hash}"))


def theme_episode_v2_revision_id(
    episode_id: str, revision: int, content_hash: str,
) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"market-theme-episode-revision-v2:{episode_id}:{revision}:{content_hash}",
    ))


def _episode_timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"theme episode {field} is invalid") from exc
    else:
        raise ValueError(f"theme episode {field} is invalid")
    if parsed.tzinfo is None:
        raise ValueError(f"theme episode {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _episode_day(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"theme episode {field} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"theme episode {field} is invalid") from exc
    return parsed.isoformat()


def _episode_strings(value: object, field: str, *, maximum: int, length: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) \
            or len(value) > maximum:
        raise ValueError(f"theme episode {field} is invalid")
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError(f"theme episode {field} is invalid")
        item = " ".join(raw.split())
        if not item or len(item) > length:
            raise ValueError(f"theme episode {field} is invalid")
        result.append(item)
    if len(set(result)) != len(result):
        raise ValueError(f"theme episode {field} is duplicated")
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class ThemeEpisodeRevision:
    """One immutable v2 cross-run episode head.

    ``theme_id`` identifies the reviewed theme, ``episode_id`` identifies the
    real-world episode, and ``revision_id`` identifies this immutable revision.
    """

    theme_id: str
    episode_id: str
    revision_id: str
    revision: int
    identity_version: int
    anchor_hash: str
    theme_mechanism: str
    subject_identity: str
    jurisdiction: str
    effective_period_start: str
    effective_period_end: str | None
    authoritative_id: str | None
    origin_run_id: str
    predecessor_revision_id: str | None
    predecessor_content_hash: str | None
    source_membership: tuple[tuple[str, str, str], ...]
    source_ids: tuple[str, ...]
    supporting_source_ids: tuple[str, ...]
    opposing_source_ids: tuple[str, ...]
    added_source_ids: tuple[str, ...]
    investigated_entity_ids: tuple[str, ...]
    missing_questions: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    first_seen: datetime
    last_seen: datetime
    next_review_at: datetime
    expires_at: datetime
    state: Literal["open", "closed"]
    closure_reason: str | None
    reopen_reason: str | None
    content_hash: str
    execution_allowed: bool = False

    def to_persistence_row(self) -> dict[str, object]:
        return {
            "theme_id": self.theme_id,
            "episode_id": self.episode_id,
            "revision_id": self.revision_id,
            "revision": self.revision,
            "identity_version": self.identity_version,
            "anchor_hash": self.anchor_hash,
            "theme_mechanism": self.theme_mechanism,
            "subject_identity": self.subject_identity,
            "jurisdiction": self.jurisdiction,
            "effective_period_start": self.effective_period_start,
            "effective_period_end": self.effective_period_end,
            "authoritative_id": self.authoritative_id,
            "origin_run_id": self.origin_run_id,
            "predecessor_revision_id": self.predecessor_revision_id,
            "predecessor_content_hash": self.predecessor_content_hash,
            "source_membership": [
                {"evidence_id": evidence_id, "story_identity": story_id, "polarity": polarity}
                for evidence_id, story_id, polarity in self.source_membership
            ],
            "source_ids": list(self.source_ids),
            "supporting_source_ids": list(self.supporting_source_ids),
            "opposing_source_ids": list(self.opposing_source_ids),
            "added_source_ids": list(self.added_source_ids),
            "investigated_entity_ids": list(self.investigated_entity_ids),
            "missing_questions": list(self.missing_questions),
            "invalidation_conditions": list(self.invalidation_conditions),
            "first_seen": _timestamp_value(self.first_seen),
            "last_seen": _timestamp_value(self.last_seen),
            "next_review_at": _timestamp_value(self.next_review_at),
            "expires_at": _timestamp_value(self.expires_at),
            "state": self.state,
            "closure_reason": self.closure_reason,
            "reopen_reason": self.reopen_reason,
            "content_hash": self.content_hash,
            "execution_allowed": False,
        }


def _timestamp_value(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _episode_anchor(event: Mapping[str, object]) -> tuple[dict[str, object], str]:
    required = {
        "theme_id", "theme_mechanism", "subject_identity", "jurisdiction",
        "effective_period", "authoritative_id",
    }
    if not required <= set(event):
        raise ValueError("theme episode identity is incomplete")
    theme_id = event["theme_id"]
    if not isinstance(theme_id, str) or not _EPISODE_THEME_PATTERN.fullmatch(theme_id):
        raise ValueError("theme episode theme identity is invalid")
    mechanism = event["theme_mechanism"]
    subject = event["subject_identity"]
    jurisdiction = event["jurisdiction"]
    authoritative_id = event["authoritative_id"]
    for value, label in ((mechanism, "mechanism"), (subject, "subject"), (jurisdiction, "jurisdiction")):
        if not isinstance(value, str) or not _EPISODE_TOKEN_PATTERN.fullmatch(value):
            raise ValueError(f"theme episode {label} is invalid")
    if authoritative_id is not None and (
        not isinstance(authoritative_id, str) or not _EPISODE_TOKEN_PATTERN.fullmatch(authoritative_id)
    ):
        raise ValueError("theme episode authoritative identity is invalid")
    period = event["effective_period"]
    if not isinstance(period, Mapping) or set(period) != {"start", "end"}:
        raise ValueError("theme episode effective period is invalid")
    start = _episode_day(period["start"], "effective period start")
    end = None if period["end"] is None else _episode_day(
        period["end"], "effective period end",
    )
    if end is not None and end < start:
        raise ValueError("theme episode effective period is invalid")
    anchor = {
        "authoritative_id": authoritative_id,
        "effective_period": {"end": end, "start": start},
        "identity_version": 2,
        "jurisdiction": jurisdiction.upper(),
        "subject_identity": " ".join(subject.split()).casefold(),
        "theme_id": theme_id,
        "theme_mechanism": " ".join(mechanism.split()).casefold(),
    }
    digest = hashlib.sha256(json.dumps(
        anchor, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return anchor, digest


def _episode_evidence(value: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) \
            or not 1 <= len(value) <= 64:
        raise ValueError("theme episode source evidence is invalid")
    by_story: dict[str, tuple[str, str, str]] = {}
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {
            "evidence_id", "story_identity", "polarity",
        }:
            raise ValueError("theme episode source evidence is invalid")
        evidence_id = str(raw["evidence_id"])
        try:
            parsed = uuid.UUID(evidence_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("theme episode evidence identity is invalid") from exc
        story = raw["story_identity"]
        polarity = raw["polarity"]
        if not isinstance(story, str):
            raise ValueError("theme episode source evidence is invalid")
        story = " ".join(story.split())
        if str(parsed) != evidence_id or not story or len(story) > 512 \
                or polarity not in {"supporting", "opposing"}:
            raise ValueError("theme episode source evidence is invalid")
        member = (evidence_id, story, str(polarity))
        current = by_story.get(story)
        if current is None or member[0] < current[0]:
            by_story[story] = member
        elif current[2] != member[2]:
            raise ValueError("theme episode syndicated evidence contradicts itself")
    return tuple(sorted(by_story.values(), key=lambda row: (row[1], row[0])))


def revise_theme_episode(
    existing: ThemeEpisodeRevision | None,
    event: Mapping[str, object],
    *,
    origin_run_id: str,
) -> ThemeEpisodeRevision:
    """Create one episode head or append one unambiguous evidence revision."""
    if not isinstance(event, Mapping):
        raise TypeError("theme episode event must be a mapping")
    try:
        parsed_run = uuid.UUID(origin_run_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("theme episode origin run is invalid") from exc
    if str(parsed_run) != origin_run_id:
        raise ValueError("theme episode origin run is invalid")
    anchor, anchor_hash = _episode_anchor(event)
    incoming = _episode_evidence(event.get("source_evidence"))
    observed_at = _episode_timestamp(event.get("observed_at"), "observed at")
    investigated = _episode_strings(
        event.get("investigated_entity_ids", []), "investigated entities", maximum=32, length=256,
    )
    questions = _episode_strings(
        event.get("missing_questions", []), "missing questions", maximum=16, length=500,
    )
    invalidation = _episode_strings(
        event.get("invalidation_conditions", []), "invalidation conditions", maximum=16, length=500,
    )
    requested_next_review = _episode_timestamp(event.get("next_review_at"), "next review")
    requested_expiry = _episode_timestamp(event.get("expires_at"), "expiry")
    state = event.get("state", "open")
    if state not in {"open", "closed"}:
        raise ValueError("theme episode state is invalid")
    closure_reason = event.get("closure_reason")
    reopen_reason = event.get("reopen_reason")
    for value, label in ((closure_reason, "closure reason"), (reopen_reason, "reopen reason")):
        if value is not None and (not isinstance(value, str) or not 3 <= len(" ".join(value.split())) <= 500):
            raise ValueError(f"theme episode {label} is invalid")
    if (state == "closed") != (closure_reason is not None):
        raise ValueError("theme episode closure state requires an exact reason")
    if existing is None and reopen_reason is not None:
        raise ValueError("initial theme episode cannot include a reopen reason")
    if existing is not None:
        if not isinstance(existing, ThemeEpisodeRevision) or existing.anchor_hash != anchor_hash:
            raise ValueError("theme episode continuation is ambiguous")
        if existing.state == "closed" and (state != "open" or reopen_reason is None):
            raise ValueError("closed theme episode requires an explicit supported successor")
        if existing.state == "open" and reopen_reason is not None:
            raise ValueError("open theme episode cannot include a reopen reason")
        existing_by_story = {story: (evidence_id, story, polarity) for evidence_id, story, polarity in existing.source_membership}
        added = [row for row in incoming if existing_by_story.get(row[1]) != row]
        if not added and state == existing.state and closure_reason == existing.closure_reason:
            return existing
        combined_by_story = dict(existing_by_story)
        combined_by_story.update({row[1]: row for row in added})
        membership = tuple(sorted(combined_by_story.values(), key=lambda row: (row[1], row[0])))
        first_seen = existing.first_seen
        expiry = existing.expires_at
        revision = existing.revision + 1
        predecessor_id = existing.revision_id
        predecessor_hash = existing.content_hash
        investigated = tuple(sorted(set(existing.investigated_entity_ids) | set(investigated)))
        questions = tuple(sorted(set(questions)))
        invalidation = tuple(sorted(set(existing.invalidation_conditions) | set(invalidation)))
    else:
        membership = incoming
        added = list(incoming)
        first_seen = observed_at
        expiry = min(requested_expiry, first_seen + timedelta(days=30))
        revision = 1
        predecessor_id = None
        predecessor_hash = None
    if requested_next_review < first_seen or expiry <= first_seen:
        raise ValueError("theme episode review window is invalid")
    next_review = min(requested_next_review, expiry)
    last_seen = max(existing.last_seen if existing else first_seen, observed_at)
    supporting = tuple(sorted(row[0] for row in membership if row[2] == "supporting"))
    opposing = tuple(sorted(row[0] for row in membership if row[2] == "opposing"))
    closure_reason = " ".join(closure_reason.split()) if isinstance(closure_reason, str) else None
    reopen_reason = " ".join(reopen_reason.split()) if isinstance(reopen_reason, str) else None
    episode_id = existing.episode_id if existing else theme_episode_v2_episode_id(anchor_hash)
    source_ids = tuple(sorted(row[0] for row in membership))
    document = {
        "added_source_ids": sorted(row[0] for row in added),
        "anchor_hash": anchor_hash,
        "authoritative_id": anchor["authoritative_id"],
        "closure_reason": closure_reason,
        "effective_period_end": anchor["effective_period"]["end"],
        "effective_period_start": anchor["effective_period"]["start"],
        "episode_id": episode_id,
        "execution_allowed": False,
        "expires_at": _timestamp_value(expiry),
        "first_seen": _timestamp_value(first_seen),
        "identity_version": 2,
        "investigated_entity_ids": list(investigated),
        "invalidation_conditions": list(invalidation),
        "jurisdiction": anchor["jurisdiction"],
        "last_seen": _timestamp_value(last_seen),
        "missing_questions": list(questions),
        "next_review_at": _timestamp_value(next_review),
        "opposing_source_ids": list(opposing),
        "origin_run_id": origin_run_id,
        "predecessor_content_hash": predecessor_hash,
        "predecessor_revision_id": predecessor_id,
        "reopen_reason": reopen_reason,
        "revision": revision,
        "source_ids": list(source_ids),
        "source_membership": [
            {"evidence_id": row[0], "story_identity": row[1], "polarity": row[2]}
            for row in membership
        ],
        "state": state,
        "subject_identity": anchor["subject_identity"],
        "supporting_source_ids": list(supporting),
        "theme_id": anchor["theme_id"],
        "theme_mechanism": anchor["theme_mechanism"],
    }
    content_hash = hashlib.sha256(json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    revision_id = theme_episode_v2_revision_id(episode_id, revision, content_hash)
    return ThemeEpisodeRevision(
        theme_id=str(anchor["theme_id"]), episode_id=episode_id, revision_id=revision_id,
        revision=revision, identity_version=2, anchor_hash=anchor_hash,
        theme_mechanism=str(anchor["theme_mechanism"]), subject_identity=str(anchor["subject_identity"]),
        jurisdiction=str(anchor["jurisdiction"]),
        effective_period_start=str(anchor["effective_period"]["start"]),  # type: ignore[index]
        effective_period_end=anchor["effective_period"]["end"],  # type: ignore[index,arg-type]
        authoritative_id=anchor["authoritative_id"] if isinstance(anchor["authoritative_id"], str) else None,
        origin_run_id=origin_run_id, predecessor_revision_id=predecessor_id,
        predecessor_content_hash=predecessor_hash, source_membership=membership,
        source_ids=source_ids,
        supporting_source_ids=supporting, opposing_source_ids=opposing,
        added_source_ids=tuple(sorted(row[0] for row in added)),
        investigated_entity_ids=investigated, missing_questions=questions,
        invalidation_conditions=invalidation, first_seen=first_seen, last_seen=last_seen,
        next_review_at=next_review, expires_at=expiry, state=state,
        closure_reason=closure_reason, reopen_reason=reopen_reason,
        content_hash=content_hash, execution_allowed=False,
    )


def episode_is_active(
    revision: ThemeEpisodeRevision,
    *,
    as_of: datetime,
    successful_source_run_ids: set[str] | frozenset[str],
) -> bool:
    """Derive active state without treating overdue review as expiry."""
    if not isinstance(revision, ThemeEpisodeRevision) or as_of.tzinfo is None:
        raise ValueError("theme episode active-state inputs are invalid")
    current = as_of.astimezone(timezone.utc)
    return (
        revision.state == "open"
        and revision.closure_reason is None
        and revision.origin_run_id in successful_source_run_ids
        and current < revision.expires_at
    )


__all__ = [
    "SEED_THEMES",
    "MarketEvent",
    "ThemeProposal",
    "ThemeEpisodeRevision",
    "build_market_event",
    "evidence_key",
    "episode_is_active",
    "publisher_identity",
    "propose_dynamic_theme",
    "revise_theme_episode",
    "source_dynamic_theme_label",
    "source_syndication_fingerprint",
    "syndication_fingerprint",
    "theme_fingerprint",
    "upstream_identity",
]
