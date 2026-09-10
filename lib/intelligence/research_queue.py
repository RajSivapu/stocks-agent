"""Deterministic bounded enrichment selection and immutable manifests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import MappingProxyType
from typing import Literal
import uuid

from lib.intelligence.discovery import ValueChainHypothesis
from lib.intelligence.packet import EvidencePacket


_PHASE_ENVELOPES: Mapping[str, Mapping[str, int]] = MappingProxyType({
    "pre-market": MappingProxyType({
        "sec_issuer_submissions": 3, "sec_filing_document": 3,
        "yahoo_security_quote": 4, "gdelt_reverse": 2,
    }),
    "intraday": MappingProxyType({
        "sec_issuer_submissions": 1, "sec_filing_document": 1,
        "yahoo_security_quote": 1, "gdelt_reverse": 1,
    }),
    "post-market": MappingProxyType({
        "sec_issuer_submissions": 2, "sec_filing_document": 2,
        "yahoo_security_quote": 2, "gdelt_reverse": 2,
    }),
    "on-demand": MappingProxyType({
        "sec_issuer_submissions": 1, "sec_filing_document": 1,
        "yahoo_security_quote": 1, "gdelt_reverse": 1,
    }),
})

_REQUEST_KEYS = frozenset({
    "request_id", "task_id", "stage", "provider", "capability_id", "descriptor",
    "query_kind", "dependency_ids", "requested_window", "request_budget",
    "execution_allowed", "descriptor_hash",
})
_MANIFEST_KEYS = frozenset({
    "manifest_id", "run_id", "phase", "selection_stage", "schema_version",
    "execution_allowed", "provider_reservations", "deferred_reasons",
    "request_descriptors", "semantic_hash",
})
_COMMON_DESCRIPTOR_KEYS = frozenset({
    "adverse_path", "cache_key", "cik", "dependency_task_ids", "entity_id",
    "event_ids", "hypothesis_ids", "instrument_type", "priority",
    "reference_manifest_id", "reservation_id", "role", "security_id",
    "security_revision_id", "source_item_ids", "source_receipt_id", "theme_id",
    "ticker",
})


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def adaptive_provider_reservations(phase: str) -> Mapping[str, int]:
    try:
        return _PHASE_ENVELOPES[phase]
    except KeyError:
        raise ValueError("phase is not approved") from None


@dataclass(frozen=True, slots=True)
class EnrichmentCandidate:
    hypothesis: ValueChainHypothesis
    entity_id: str
    security_id: str
    security_revision_id: str
    reference_manifest_id: str
    cik: str
    ticker: str
    instrument_type: str
    source_item_ids: tuple[str, ...]
    dependency_task_ids: tuple[str, ...]
    official_support: bool = False
    novelty: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis, ValueChainHypothesis):
            raise TypeError("enrichment candidate hypothesis must be typed")
        if not self.entity_id or not self.security_id or not self.security_revision_id \
                or not self.reference_manifest_id:
            raise ValueError("enrichment candidate reference binding is incomplete")
        if len(self.cik) != 10 or not self.cik.isdigit() or int(self.cik) == 0:
            raise ValueError("enrichment candidate CIK is invalid")
        if not self.ticker or self.ticker != self.ticker.upper():
            raise ValueError("enrichment candidate ticker is invalid")
        if self.instrument_type not in {"COMMON_STOCK", "ADR", "ETF"}:
            raise ValueError("enrichment candidate instrument is invalid")
        if not self.source_item_ids or not self.dependency_task_ids:
            raise ValueError("enrichment candidate provenance is incomplete")
        if len(set(self.source_item_ids)) != len(self.source_item_ids) \
                or len(set(self.dependency_task_ids)) != len(self.dependency_task_ids):
            raise ValueError("enrichment candidate provenance is duplicated")
        if isinstance(self.novelty, bool) or not isinstance(self.novelty, int) or not 0 <= self.novelty <= 100:
            raise ValueError("enrichment candidate novelty is invalid")


@dataclass(frozen=True, slots=True)
class EnrichmentRequest:
    request_id: str
    entity_id: str
    security_id: str | None
    security_revision_id: str
    reference_manifest_id: str
    cik: str
    ticker: str | None
    instrument_type: str | None
    event_ids: tuple[str, ...]
    theme_id: str
    role: str
    hypothesis_ids: tuple[str, ...]
    source_item_ids: tuple[str, ...]
    dependency_task_ids: tuple[str, ...]
    provider: Literal["sec_edgar", "yahoo"]
    capability_id: Literal["sec_issuer_submissions", "sec_filing_document", "yahoo_security_quote"]
    query_kind: Literal["issuer_submissions", "filing_document", "quote"]
    descriptor: Mapping[str, object]
    adverse_path: bool
    priority: int
    requested_window: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    execution_allowed: bool = False

    @property
    def descriptor_hash(self) -> str:
        return hashlib.sha256(_canonical(self._persistence_document()).encode()).hexdigest()

    def _persistence_document(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "task_id": self.request_id,
            "stage": "quote" if self.query_kind == "quote" else "enrich",
            "provider": self.provider,
            "capability_id": self.capability_id,
            "descriptor": dict(self.descriptor),
            "query_kind": self.query_kind,
            "dependency_ids": list(self.dependency_task_ids),
            "requested_window": dict(self.requested_window),
            "request_budget": 1,
            "execution_allowed": False,
        }

    def persistence_row(self) -> dict[str, object]:
        return {
            **self._persistence_document(),
            "descriptor_hash": self.descriptor_hash,
        }


@dataclass(frozen=True, slots=True)
class SelectionManifest:
    manifest_id: str
    run_id: str
    phase: str
    requests: tuple[EnrichmentRequest, ...]
    deferred_reasons: Mapping[str, str]
    provider_reservations: Mapping[str, int]
    semantic_hash: str
    selection_stage: Literal["holding_quotes", "initial", "filing_documents"] = "initial"
    execution_allowed: bool = False

    def persistence_payload(self) -> dict[str, object]:
        manifest = _manifest_document(
            self.run_id, self.phase, self.requests,
            self.deferred_reasons, self.provider_reservations, self.selection_stage,
        )
        return {
            "manifest": {
                "manifest_id": self.manifest_id,
                **manifest,
                "semantic_hash": self.semantic_hash,
            },
            "requests": [row.persistence_row() for row in self.requests],
        }


def _candidate_key(value: EnrichmentCandidate) -> tuple[object, ...]:
    return (
        0 if value.official_support else 1,
        0 if value.hypothesis.adverse_path else 1,
        -value.novelty,
        value.hypothesis.event_id,
        value.entity_id,
        value.security_id,
        value.hypothesis.hypothesis_id,
    )


def _round_robin_candidates(values: Sequence[EnrichmentCandidate], maximum: int) -> list[EnrichmentCandidate]:
    themes: dict[str, list[EnrichmentCandidate]] = defaultdict(list)
    for value in sorted(values, key=_candidate_key):
        themes[value.hypothesis.theme_id].append(value)
    selected: list[EnrichmentCandidate] = []
    used_entities: set[str] = set()
    used_events: dict[str, int] = defaultdict(int)
    while len(selected) < maximum and themes:
        next_themes: dict[str, list[EnrichmentCandidate]] = {}
        for theme in sorted(themes):
            rows = themes[theme]
            chosen = None
            while rows:
                row = rows.pop(0)
                if row.entity_id in used_entities or used_events[row.hypothesis.event_id] >= 2:
                    continue
                chosen = row
                break
            if chosen is not None and len(selected) < maximum:
                selected.append(chosen)
                used_entities.add(chosen.entity_id)
                used_events[chosen.hypothesis.event_id] += 1
            if rows:
                next_themes[theme] = rows
        themes = next_themes
    return selected


def select_enrichment_queue(
    hypotheses: Sequence[EnrichmentCandidate],
    *,
    max_entities: int,
    max_requests: int,
    required_holding_quote_requests: int,
    provider_limits: Mapping[str, int] | None = None,
) -> tuple[EnrichmentRequest, ...]:
    """Prioritize official support, novelty, adverse paths, and theme fairness."""
    if any(not isinstance(value, EnrichmentCandidate) for value in hypotheses):
        raise TypeError("enrichment selection requires resolved typed candidates")
    for value, label, maximum in (
        (max_entities, "max entities", 4),
        (max_requests, "max requests", 100),
        (required_holding_quote_requests, "holding quote reserve", 100),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"{label} is invalid")
    available = max(0, max_requests - required_holding_quote_requests)
    limits = dict(provider_limits or {"sec_edgar": available, "yahoo": available})
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in limits.values()):
        raise ValueError("provider limit is invalid")
    selected_heads = _round_robin_candidates(hypotheses, max_entities)
    by_entity: dict[str, list[EnrichmentCandidate]] = defaultdict(list)
    for value in hypotheses:
        by_entity[value.entity_id].append(value)
    requests: list[EnrichmentRequest] = []
    used = {"sec_edgar": 0, "yahoo": 0}
    for priority, head in enumerate(selected_heads, 1):
        related = sorted(by_entity[head.entity_id], key=_candidate_key)
        hypothesis_ids = tuple(sorted({row.hypothesis.hypothesis_id for row in related}))
        event_ids = tuple(sorted({row.hypothesis.event_id for row in related}))
        source_ids = tuple(sorted({item for row in related for item in row.source_item_ids}))[:64]
        dependencies = tuple(sorted({item for row in related for item in row.dependency_task_ids}))[:32]
        if len(requests) < available and used["sec_edgar"] < limits.get("sec_edgar", 0):
            descriptor = MappingProxyType({
                "cik": head.cik,
                "issuer_entity_id": head.entity_id,
                "reference_manifest_id": head.reference_manifest_id,
                "security_revision_id": head.security_revision_id,
            })
            digest = hashlib.sha256(_canonical({
                "kind": "issuer_submissions", "descriptor": dict(descriptor),
                "hypothesis_ids": hypothesis_ids,
            }).encode()).hexdigest()
            requests.append(EnrichmentRequest(
                request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"enrichment:{digest}")),
                entity_id=head.entity_id, security_id=head.security_id,
                security_revision_id=head.security_revision_id,
                reference_manifest_id=head.reference_manifest_id, cik=head.cik,
                ticker=head.ticker, instrument_type=head.instrument_type, event_ids=event_ids,
                theme_id=head.hypothesis.theme_id, role=head.hypothesis.role,
                hypothesis_ids=hypothesis_ids, source_item_ids=source_ids,
                dependency_task_ids=dependencies, provider="sec_edgar",
                capability_id="sec_issuer_submissions", query_kind="issuer_submissions",
                descriptor=descriptor,
                adverse_path=any(row.hypothesis.adverse_path for row in related),
                priority=priority, execution_allowed=False,
            ))
            used["sec_edgar"] += 1
        for row in sorted({item.security_id: item for item in related}.values(), key=lambda item: item.security_id):
            if len(requests) >= available or used["yahoo"] >= limits.get("yahoo", 0):
                break
            descriptor = MappingProxyType({
                "instrument_type": row.instrument_type,
                "reference_manifest_id": row.reference_manifest_id,
                "security_id": row.security_id,
                "security_revision_id": row.security_revision_id,
                "ticker": row.ticker,
            })
            digest = hashlib.sha256(_canonical({
                "kind": "quote", "descriptor": dict(descriptor),
                "hypothesis_ids": tuple(sorted({item.hypothesis.hypothesis_id for item in related if item.security_id == row.security_id})),
            }).encode()).hexdigest()
            requests.append(EnrichmentRequest(
                request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"enrichment:{digest}")),
                entity_id=row.entity_id, security_id=row.security_id,
                security_revision_id=row.security_revision_id,
                reference_manifest_id=row.reference_manifest_id, cik=row.cik,
                ticker=row.ticker, instrument_type=row.instrument_type,
                event_ids=event_ids, theme_id=row.hypothesis.theme_id,
                role=row.hypothesis.role,
                hypothesis_ids=tuple(sorted({item.hypothesis.hypothesis_id for item in related if item.security_id == row.security_id})),
                source_item_ids=source_ids, dependency_task_ids=dependencies,
                provider="yahoo", capability_id="yahoo_security_quote", query_kind="quote",
                descriptor=descriptor, adverse_path=row.hypothesis.adverse_path,
                priority=priority, execution_allowed=False,
            ))
            used["yahoo"] += 1
    return tuple(sorted(requests, key=lambda row: (row.priority, 0 if row.provider == "sec_edgar" else 1, row.request_id)))


def _manifest_document(
    run_id: str,
    phase: str,
    requests: Sequence[EnrichmentRequest],
    deferred_reasons: Mapping[str, str],
    provider_reservations: Mapping[str, int],
    selection_stage: str = "initial",
) -> dict[str, object]:
    return {
        "deferred_reasons": dict(sorted(deferred_reasons.items())),
        "execution_allowed": False,
        "phase": phase,
        "provider_reservations": dict(sorted(provider_reservations.items())),
        "request_descriptors": [{"request_id": row.request_id, "descriptor_hash": row.descriptor_hash} for row in requests],
        "run_id": run_id,
        "schema_version": 1,
        "selection_stage": selection_stage,
    }


def build_selection_manifest(
    *,
    run_id: str,
    phase: str,
    requests: Sequence[EnrichmentRequest],
    deferred_reasons: Mapping[str, str],
    provider_reservations: Mapping[str, int],
    request_window: Mapping[str, str],
    selection_stage: Literal["holding_quotes", "initial", "filing_documents"] = "initial",
) -> SelectionManifest:
    try:
        parsed = uuid.UUID(run_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("selection run ID is invalid") from exc
    if str(parsed) != run_id or phase not in _PHASE_ENVELOPES:
        raise ValueError("selection manifest identity is invalid")
    if any(not isinstance(row, EnrichmentRequest) for row in requests):
        raise TypeError("selection requests must be typed")
    if len({row.request_id for row in requests}) != len(requests):
        raise ValueError("selection request identity is duplicated")
    if set(request_window) != {"start", "end"} or any(
        not isinstance(request_window[key], str) or not request_window[key]
        for key in ("start", "end")
    ):
        raise ValueError("selection request window is invalid")
    bound: list[EnrichmentRequest] = []
    for row in requests:
        reservation_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"market-intelligence:reservation:{run_id}:{row.provider}",
        ))
        source_receipt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"market-intelligence:receipt:{run_id}:{row.request_id}",
        ))
        base = {
            **dict(row.descriptor),
            "adverse_path": row.adverse_path,
            "cik": row.cik,
            "dependency_task_ids": list(row.dependency_task_ids),
            "entity_id": row.entity_id,
            "event_ids": list(row.event_ids),
            "hypothesis_ids": list(row.hypothesis_ids),
            "instrument_type": row.instrument_type,
            "priority": row.priority,
            "reference_manifest_id": row.reference_manifest_id,
            "reservation_id": reservation_id,
            "role": row.role,
            "source_item_ids": list(row.source_item_ids),
            "source_receipt_id": source_receipt_id,
            "security_id": row.security_id,
            "security_revision_id": row.security_revision_id,
            "theme_id": row.theme_id,
            "ticker": row.ticker,
        }
        base["cache_key"] = hashlib.sha256(_canonical({
            "capability_id": row.capability_id,
            "descriptor": base,
            "provider": row.provider,
            "query_kind": row.query_kind,
            "requested_window": dict(request_window),
        }).encode()).hexdigest()
        bound.append(EnrichmentRequest(**{
            name: getattr(row, name) for name in row.__dataclass_fields__
            if name not in {"descriptor", "requested_window"}
        }, descriptor=MappingProxyType(base), requested_window=MappingProxyType(dict(request_window))))
    if selection_stage not in {"holding_quotes", "initial", "filing_documents"}:
        raise ValueError("selection stage is invalid")
    document = _manifest_document(
        run_id, phase, bound, deferred_reasons, provider_reservations, selection_stage,
    )
    digest = hashlib.sha256(_canonical(document).encode()).hexdigest()
    return SelectionManifest(
        manifest_id=str(uuid.uuid5(uuid.UUID(run_id), f"enrichment-selection:{digest}")),
        run_id=run_id, phase=phase, requests=tuple(bound),
        deferred_reasons=MappingProxyType(dict(sorted(deferred_reasons.items()))),
        provider_reservations=MappingProxyType(dict(sorted(provider_reservations.items()))),
        semantic_hash=digest, selection_stage=selection_stage, execution_allowed=False,
    )


def validate_selection_manifest(value: SelectionManifest) -> SelectionManifest:
    if not isinstance(value, SelectionManifest):
        raise TypeError("selection manifest must be typed")
    document = _manifest_document(
        value.run_id, value.phase, value.requests,
        value.deferred_reasons, value.provider_reservations, value.selection_stage,
    )
    digest = hashlib.sha256(_canonical(document).encode()).hexdigest()
    expected_id = str(uuid.uuid5(uuid.UUID(value.run_id), f"enrichment-selection:{digest}"))
    if digest != value.semantic_hash or expected_id != value.manifest_id:
        raise ValueError("selection manifest hash mismatch")
    if value.execution_allowed is not False:
        raise ValueError("selection manifest cannot authorize execution")
    return value


def selection_manifest_from_payload(payload: Mapping[str, object]) -> SelectionManifest:
    """Load one sealed manifest without deriving any request field again."""
    if not isinstance(payload, Mapping) or set(payload) != {"manifest", "requests"}:
        raise ValueError("persisted enrichment selection is invalid")
    manifest = payload["manifest"]
    raw_requests = payload["requests"]
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS \
            or not isinstance(raw_requests, Sequence) \
            or isinstance(raw_requests, (str, bytes, bytearray)) \
            or len(raw_requests) > 100:
        raise ValueError("persisted enrichment selection is invalid")
    if manifest.get("schema_version") != 1 or manifest.get("execution_allowed") is not False:
        raise ValueError("persisted enrichment selection authority is invalid")
    phase = manifest.get("phase")
    stage = manifest.get("selection_stage")
    if phase not in _PHASE_ENVELOPES or stage not in {
        "holding_quotes", "initial", "filing_documents",
    } or manifest.get("provider_reservations") != _PHASE_ENVELOPES[phase]:
        raise ValueError("persisted enrichment selection envelope is invalid")
    rows: list[EnrichmentRequest] = []
    for raw in raw_requests:
        if not isinstance(raw, Mapping) or set(raw) != _REQUEST_KEYS:
            raise ValueError("persisted enrichment request is invalid")
        descriptor = raw["descriptor"]
        if not isinstance(descriptor, Mapping):
            raise ValueError("persisted enrichment descriptor is invalid")
        query_kind = raw.get("query_kind")
        expected_descriptor = _COMMON_DESCRIPTOR_KEYS | (
            {"issuer_entity_id"} if query_kind == "issuer_submissions" else
            {"accepted_at", "accession_number", "filing_date", "form", "primary_document",
             "reporting_period_end", "submissions_response_hash"}
            if query_kind == "filing_document" else set()
        )
        provider_capability_stage = {
            "issuer_submissions": ("sec_edgar", "sec_issuer_submissions", "enrich", "initial"),
            "filing_document": ("sec_edgar", "sec_filing_document", "enrich", "filing_documents"),
            "quote": ("yahoo", "yahoo_security_quote", "quote", stage),
        }.get(str(query_kind))
        if provider_capability_stage is None or set(descriptor) != expected_descriptor \
                or (raw.get("provider"), raw.get("capability_id"), raw.get("stage"), stage) \
                != provider_capability_stage:
            raise ValueError("persisted enrichment descriptor shape is invalid")
        dependencies = raw.get("dependency_ids")
        window = raw.get("requested_window")
        if raw.get("request_id") != raw.get("task_id") or raw.get("request_budget") != 1 \
                or raw.get("execution_allowed") is not False \
                or not isinstance(dependencies, list) \
                or descriptor.get("dependency_task_ids") != dependencies \
                or not isinstance(window, Mapping) or set(window) != {"start", "end"}:
            raise ValueError("persisted enrichment request binding is invalid")
        row = EnrichmentRequest(
            request_id=str(raw["request_id"]), entity_id=str(descriptor["entity_id"]),
            security_id=str(descriptor["security_id"]),
            security_revision_id=str(descriptor["security_revision_id"]),
            reference_manifest_id=str(descriptor["reference_manifest_id"]),
            cik=str(descriptor["cik"]), ticker=str(descriptor["ticker"]),
            instrument_type=str(descriptor["instrument_type"]),
            event_ids=tuple(str(value) for value in descriptor["event_ids"]),
            theme_id=str(descriptor["theme_id"]), role=str(descriptor["role"]),
            hypothesis_ids=tuple(str(value) for value in descriptor["hypothesis_ids"]),
            source_item_ids=tuple(str(value) for value in descriptor["source_item_ids"]),
            dependency_task_ids=tuple(str(value) for value in dependencies),
            provider=str(raw["provider"]), capability_id=str(raw["capability_id"]),
            query_kind=str(query_kind), descriptor=MappingProxyType(dict(descriptor)),
            adverse_path=descriptor["adverse_path"] is True,
            priority=int(descriptor["priority"]),
            requested_window=MappingProxyType(dict(window)), execution_allowed=False,
        )  # type: ignore[arg-type]
        if row.descriptor_hash != raw.get("descriptor_hash"):
            raise ValueError("persisted enrichment request hash mismatch")
        rows.append(row)
    request_descriptors = [
        {"request_id": row.request_id, "descriptor_hash": row.descriptor_hash}
        for row in rows
    ]
    if manifest.get("request_descriptors") != request_descriptors:
        raise ValueError("persisted enrichment manifest children mismatch")
    result = SelectionManifest(
        manifest_id=str(manifest["manifest_id"]), run_id=str(manifest["run_id"]),
        phase=str(phase), requests=tuple(rows),
        deferred_reasons=MappingProxyType(dict(manifest["deferred_reasons"])),
        provider_reservations=MappingProxyType(dict(manifest["provider_reservations"])),
        semantic_hash=str(manifest["semantic_hash"]),
        selection_stage=str(stage), execution_allowed=False,
    )  # type: ignore[arg-type]
    return validate_selection_manifest(result)


_NOMINATION_KEYS = frozenset({
    "theme_id", "entity_id", "security_id", "role", "reason",
    "evidence_ids", "required_evidence_kind", "priority",
})
_NOMINATION_EVIDENCE_KINDS = frozenset({
    "primary_exposure", "contradictory_primary", "current_filing",
    "official_program", "entity_identity", "relationship", "current_reference",
})
_NOMINATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_NOMINATION_THEME_PATTERN = re.compile(
    r"^(?:[a-z][a-z0-9_]{2,79}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_UNSAFE_NOMINATION_REASON = re.compile(
    r"(?:https?://|\b(?:buy|sell|trade|order|price|score|watchlist|holding|portfolio|cash|alert|policy|allocation|browse|search query)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ResearchNomination:
    nomination_id: str
    packet_id: str
    packet_hash: str
    reference_manifest_id: str
    theme_id: str
    entity_id: str | None
    security_id: str | None
    role: str
    reason: str
    evidence_ids: tuple[str, ...]
    required_evidence_kind: str
    priority: int
    created_at: datetime
    expires_at: datetime
    state: Literal["pending", "selected", "resolved", "rejected", "expired"] = "pending"
    authorizes_action: bool = False
    may_mutate_watchlist: bool = False
    execution_allowed: bool = False

    def request_payload(self) -> dict[str, object]:
        """Return only the reviewed model-write keys; authority stays server-owned."""
        return {
            "theme_id": self.theme_id,
            "entity_id": self.entity_id,
            "security_id": self.security_id,
            "role": self.role,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "required_evidence_kind": self.required_evidence_kind,
            "priority": self.priority,
        }


def _nomination_uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"nomination {field} is invalid")
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"nomination {field} is invalid") from exc
    if str(parsed) != value:
        raise ValueError(f"nomination {field} is invalid")
    return value


def _packet_document(packet: EvidencePacket | Mapping[str, object]) -> Mapping[str, object]:
    value: object = packet.to_dict() if isinstance(packet, EvidencePacket) else packet
    if not isinstance(value, Mapping) or value.get("contract_version") != 2 \
            or value.get("execution_allowed") is not False:
        raise ValueError("nomination packet must be protected v2 research")
    return value


def validate_research_nominations(
    nominations: Sequence[Mapping[str, object]],
    packet: EvidencePacket | Mapping[str, object],
    *,
    now: datetime | None = None,
) -> tuple[ResearchNomination, ...]:
    """Validate bounded candidate-linked research follow-ups without action authority."""
    if isinstance(nominations, (str, bytes, bytearray)) or not isinstance(nominations, Sequence):
        raise ValueError("research nominations must be a list")
    if len(nominations) > 3:
        raise ValueError("research nominations are limited to three per run")
    packet_row = _packet_document(packet)
    packet_id = _nomination_uuid(packet_row.get("packet_id"), "packet identity")
    packet_hash = packet_row.get("packet_hash")
    reference_id = _nomination_uuid(packet_row.get("reference_manifest_id"), "reference identity")
    if not isinstance(packet_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", packet_hash):
        raise ValueError("nomination packet hash is invalid")
    candidate_rows = packet_row.get("research_candidates")
    if not isinstance(candidate_rows, Sequence) or isinstance(candidate_rows, (str, bytes, bytearray)) \
            or len(candidate_rows) > 12:
        raise ValueError("nomination packet candidates are invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates: list[Mapping[str, object]] = [
        row for row in candidate_rows if isinstance(row, Mapping)
    ]
    results: list[ResearchNomination] = []
    request_hashes: set[str] = set()
    for raw in nominations:
        if not isinstance(raw, Mapping) or set(raw) != _NOMINATION_KEYS:
            raise ValueError("nomination request must use exact reviewed keys")
        theme_id = raw["theme_id"]
        entity_id = raw["entity_id"]
        security_id = raw["security_id"]
        role = raw["role"]
        reason = raw["reason"]
        kind = raw["required_evidence_kind"]
        priority = raw["priority"]
        if not isinstance(theme_id, str) or not _NOMINATION_THEME_PATTERN.fullmatch(theme_id):
            raise ValueError("nomination theme identity is invalid")
        for value, label in ((entity_id, "entity"), (security_id, "security")):
            if value is not None and (
                not isinstance(value, str) or not _NOMINATION_ID_PATTERN.fullmatch(value)
            ):
                raise ValueError(f"nomination {label} identity is invalid")
        if not isinstance(role, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", role):
            raise ValueError("nomination role is invalid")
        if not isinstance(reason, str):
            raise ValueError("nomination reason is invalid")
        normalized_reason = " ".join(reason.split())
        if not 20 <= len(normalized_reason) <= 500 or _UNSAFE_NOMINATION_REASON.search(normalized_reason):
            raise ValueError("nomination reason contains an unsafe instruction or authority")
        if kind not in _NOMINATION_EVIDENCE_KINDS:
            raise ValueError("nomination required evidence kind is invalid")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ValueError("nomination priority is invalid")
        raw_ids = raw["evidence_ids"]
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes, bytearray)) \
                or not 1 <= len(raw_ids) <= 8:
            raise ValueError("nomination evidence is invalid")
        evidence_ids = tuple(_nomination_uuid(value, "evidence identity") for value in raw_ids)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("nomination evidence identities are duplicated")
        matches: list[Mapping[str, object]] = []
        for candidate in candidates:
            themes = candidate.get("theme_ids")
            roles = candidate.get("roles")
            evidence = candidate.get("evidence")
            if not isinstance(themes, Sequence) or isinstance(themes, (str, bytes, bytearray)) \
                    or not isinstance(roles, Sequence) or isinstance(roles, (str, bytes, bytearray)) \
                    or not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
                continue
            allowed_evidence = {
                str(item.get("item_id")) for item in evidence
                if isinstance(item, Mapping) and item.get("relationship_eligible") is True
                and item.get("role") in {"supporting", "opposing"}
            }
            if (
                theme_id in themes and role in roles
                and candidate.get("entity_id") == entity_id
                and candidate.get("security_id") == security_id
                and set(evidence_ids) <= allowed_evidence
            ):
                matches.append(candidate)
        if len(matches) != 1:
            raise ValueError("nomination evidence is not candidate-bound to the nominated relationship")
        request = {
            "entity_id": entity_id,
            "evidence_ids": sorted(evidence_ids),
            "packet_hash": packet_hash,
            "priority": priority,
            "reason": normalized_reason,
            "required_evidence_kind": kind,
            "role": role,
            "security_id": security_id,
            "theme_id": theme_id,
        }
        request_hash = hashlib.sha256(_canonical(request).encode()).hexdigest()
        if request_hash in request_hashes:
            raise ValueError("nomination request is duplicated")
        request_hashes.add(request_hash)
        results.append(ResearchNomination(
            nomination_id=str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"market-research-nomination-v2:{packet_id}:{request_hash}",
            )),
            packet_id=packet_id, packet_hash=packet_hash,
            reference_manifest_id=reference_id, theme_id=theme_id,
            entity_id=entity_id if isinstance(entity_id, str) else None,
            security_id=security_id if isinstance(security_id, str) else None,
            role=role, reason=normalized_reason, evidence_ids=tuple(sorted(evidence_ids)),
            required_evidence_kind=str(kind), priority=priority,
            created_at=current, expires_at=current + timedelta(days=7),
            state="pending", authorizes_action=False, may_mutate_watchlist=False,
            execution_allowed=False,
        ))
    return tuple(results)


__all__ = [
    "EnrichmentCandidate",
    "EnrichmentRequest",
    "ResearchNomination",
    "SelectionManifest",
    "adaptive_provider_reservations",
    "build_selection_manifest",
    "select_enrichment_queue",
    "selection_manifest_from_payload",
    "validate_selection_manifest",
    "validate_research_nominations",
]
