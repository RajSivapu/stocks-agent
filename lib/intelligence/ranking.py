"""Reproducible fixed-point candidate ranking with explicit missing data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Sequence

from lib.intelligence.normalize import SourceItem
from lib.intelligence.relationships import EventRelationship
from lib.intelligence.themes import MarketEvent, evidence_key


_Q = Decimal("0.000001")
_ZERO = Decimal("0.000000")
_COMPONENTS = (
    "materiality",
    "authority_corroboration",
    "exposure",
    "recency",
    "portfolio_relevance",
    "liquidity",
    "duplication_penalty",
    "concentration_penalty",
)


def _fixed(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite decimal")
    try:
        result = Decimal(str(value)).quantize(_Q, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


@dataclass(frozen=True, slots=True)
class CandidateInput:
    ticker: str | None
    event: MarketEvent
    relation: EventRelationship | None
    evidence: tuple[SourceItem, ...]
    authority_corroboration: Decimal | int | str | None = None
    exposure_strength: Decimal | int | str | None = None
    recency: Decimal | int | str | None = None
    portfolio_relevance: Decimal | int | str | None = None
    liquidity: Decimal | int | str | None = None
    duplication_penalty: Decimal | int | str | None = None
    holding_weight: Decimal | int | str | None = None
    overlap: Decimal | int | str | None = None
    concentration: Decimal | int | str | None = None
    contract_version: int = 1
    security_id: str | None = None
    entity_id: str | None = None
    theme_ids: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    exposure_fact_ids: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    opposing_evidence_ids: tuple[str, ...] = ()
    lineage: CandidateLineage | None = None
    reference_state: Literal["current", "stale", "ambiguous", "unavailable"] = "current"
    valuation_state: Literal["passed", "failed", "missing", "stale", "ambiguous", "unverified", "unavailable"] = "missing"
    quote_state: Literal["passed", "failed", "missing", "stale", "ambiguous", "unverified", "unavailable"] = "missing"
    portfolio_state: Literal["passed", "failed", "missing", "stale", "ambiguous", "unverified", "unavailable"] = "missing"
    cash_state: Literal["passed", "failed", "missing", "stale", "ambiguous", "unverified", "unavailable"] = "missing"
    explicit_unresolved_identity: bool = False
    limitations: tuple[str, ...] = ()
    adverse_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateLineage:
    run_id: str
    observed_at: str
    policy_version: int
    reference_manifest_id: str | None
    reference_revision: int | None
    reference_expires_at: str | None
    security_revision_id: str | None
    quote_receipt_id: str | None
    quote_as_of: str | None
    quote_expires_at: str | None
    evidence_receipt_ids: Mapping[str, str]
    portfolio_revision: str | None
    cash_revision: str | None


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    candidate_key: str
    security_id: str | None
    ticker: str | None
    entity_id: str | None
    event_ids: tuple[str, ...]
    theme_ids: tuple[str, ...]
    roles: tuple[str, ...]
    research_state: Literal[
        "observed", "unresolved", "resolved", "exposure_supported",
        "analysis_ready", "research_rejected",
    ]
    priority_components: Mapping[str, Decimal]
    priority_score: Decimal
    evidence: tuple[SourceItem, ...]
    exposure_fact_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    opposing_evidence_ids: tuple[str, ...]
    evidence_receipt_ids: Mapping[str, str]
    limitations: tuple[str, ...]
    adverse_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuitabilityEvaluation:
    candidate_key: str
    state: Literal["unknown", "eligible", "vetoed"]
    component_scores: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]
    veto_reasons: tuple[str, ...]
    lineage: CandidateLineage | None


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    ticker: str | None
    candidate_key: str
    event_id: str
    relationship_type: str
    evidence: tuple[SourceItem, ...]
    exposure_evidence: tuple[SourceItem, ...]
    components: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]
    total_score: Decimal
    authoritative_evidence_count: int
    qualified: bool
    veto_reasons: tuple[str, ...]
    rank: int = 0
    research: ResearchCandidate | None = None
    suitability: SuitabilityEvaluation | None = None


def _plan_positions(plans: object) -> dict[str, Decimal]:
    positions: dict[str, Decimal] = {}
    if plans is None:
        return positions
    values: Iterable[tuple[object, object]]
    if isinstance(plans, Mapping):
        values = plans.items()
    else:
        values = enumerate(plans)  # type: ignore[arg-type]
    for key, value in values:
        if isinstance(value, Mapping):
            if value.get("active", True) is False:
                continue
            symbol = str(value.get("ticker") or value.get("symbol") or "").upper()
            weight = value.get("weight", value.get("allocation", 0))
        else:
            symbol = str(value if not isinstance(plans, Mapping) else key).upper()
            weight = 0
        if symbol:
            positions[symbol] = max(_ZERO, _fixed(weight, "plan weight"))
    return positions


def _holding_positions(holdings: Mapping[str, object] | None) -> dict[str, Decimal]:
    output: dict[str, Decimal] = {}
    for ticker, weight in (holdings or {}).items():
        fixed = _fixed(weight, "holding weight")
        if fixed < 0:
            raise ValueError("holding weight cannot be negative")
        output[str(ticker).upper()] = fixed
    return output


def candidate_sort_key(candidate: RankedCandidate) -> tuple[Decimal, int, str]:
    return (-candidate.total_score, -candidate.authoritative_evidence_count, candidate.candidate_key)


def _evidence_index(items: Sequence[SourceItem]) -> tuple[dict[str, SourceItem], bool]:
    indexed: dict[str, SourceItem] = {}
    consistent = True
    for item in items:
        key = evidence_key(item)
        prior = indexed.get(key)
        if prior is not None and (
            prior.content_hash != item.content_hash
            or prior.canonical_content != item.canonical_content
        ):
            consistent = False
        else:
            indexed[key] = item
    return indexed, consistent


def rank_candidates(
    candidates: Sequence[CandidateInput],
    *,
    holdings: Mapping[str, object] | None = None,
    plans: object = None,
    contract_version: int = 1,
) -> list[RankedCandidate]:
    if contract_version not in {1, 2}:
        raise ValueError("ranking contract version is unsupported")
    holding_positions = _holding_positions(holdings)
    plan_positions = _plan_positions(plans)
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        if candidate.contract_version != contract_version:
            raise ValueError("candidate contract version mismatch")
        ticker = candidate.ticker.strip().upper() if isinstance(candidate.ticker, str) else None
        if contract_version == 1 and (ticker is None or candidate.relation is None):
            raise ValueError("legacy candidate requires ticker relationship")
        if candidate.relation is not None and ticker != candidate.relation.ticker:
            raise ValueError("candidate ticker must match its relationship")
        missing: list[str] = []
        candidate_evidence, candidate_evidence_consistent = _evidence_index(candidate.evidence)
        relation_exposure, relation_exposure_consistent = _evidence_index(
            candidate.relation.exposure_evidence if candidate.relation is not None else ()
        )
        event_consistent = candidate.relation is None or candidate.event.event_id == candidate.relation.event_id
        # V2 deliberately retains resolved research leads before typed primary
        # exposure exists.  The suitability lane remains unknown and cannot be
        # promoted, while the legacy lane still requires exposure evidence.
        evidence_consistent = bool(candidate_evidence) and (
            bool(relation_exposure)
            if contract_version == 1 and candidate.relation is not None
            else True
        )
        evidence_consistent = (
            evidence_consistent
            and candidate_evidence_consistent
            and relation_exposure_consistent
            and all(
                key in candidate_evidence
                and candidate_evidence[key].content_hash == item.content_hash
                and candidate_evidence[key].canonical_content == item.canonical_content
                for key, item in relation_exposure.items()
            )
        )
        exposure_consistent = event_consistent and evidence_consistent
        if not event_consistent:
            missing.append("event_relation:mismatch")
        if not candidate_evidence:
            missing.append("evidence:empty")
        elif not evidence_consistent:
            missing.append("exposure_evidence:mismatch")
        values: dict[str, Decimal] = {"materiality": candidate.event.materiality}
        supplied = {
            "authority_corroboration": candidate.authority_corroboration,
            "exposure": candidate.exposure_strength,
            "recency": candidate.recency,
            "portfolio_relevance": candidate.portfolio_relevance,
            "liquidity": candidate.liquidity,
        }
        for name, value in supplied.items():
            if value is None:
                values[name] = _ZERO
                missing.append(f"{name}:missing")
            else:
                score = _fixed(value, name)
                if score < 0 or score > 1:
                    raise ValueError(f"{name} must be between zero and one")
                values[name] = score
        if not exposure_consistent:
            values["exposure"] = _ZERO

        derived_holding_weight = (
            (holding_positions.get(ticker, _ZERO) if holdings is not None and ticker is not None else None)
            if candidate.holding_weight is None
            else candidate.holding_weight
        )
        derived_overlap = (
            None
        ) if candidate.overlap is None else candidate.overlap
        derived_concentration = (
            derived_holding_weight if candidate.concentration is None else candidate.concentration
        )
        for name, value in {
            "holding_weight": derived_holding_weight,
            "overlap": derived_overlap,
            "concentration": derived_concentration,
        }.items():
            if value is None:
                missing.append(f"{name}:missing")
                continue
            score = _fixed(value, name)
            if score < 0 or score > 1:
                raise ValueError(f"{name} must be between zero and one")
            if name == "holding_weight" and score >= Decimal("0.40"):
                missing.append("holding_weight:concentrated")
            if name == "concentration" and score >= Decimal("0.40"):
                missing.append("concentration:high")

        holding_overlap = ticker in holding_positions if ticker is not None else False
        plan_overlap = ticker in plan_positions if ticker is not None else False
        automatic_duplication = (
            (Decimal("-0.150000") if holding_overlap else _ZERO)
            + (Decimal("-0.100000") if plan_overlap else _ZERO)
        )
        explicit_duplication = (
            _ZERO
            if candidate.duplication_penalty is None
            else _fixed(candidate.duplication_penalty, "duplication_penalty")
        )
        if explicit_duplication > 0:
            raise ValueError("duplication_penalty cannot be positive")
        values["duplication_penalty"] = (automatic_duplication + explicit_duplication).quantize(_Q)
        concentration = -(
            _fixed(derived_holding_weight, "holding_weight")
            if derived_holding_weight is not None else _ZERO
        ) - (
            plan_positions.get(ticker, _ZERO) / Decimal("2") if ticker is not None else _ZERO
        )
        values["concentration_penalty"] = max(Decimal("-1.000000"), concentration).quantize(_Q)

        ordered = MappingProxyType({name: values[name].quantize(_Q) for name in _COMPONENTS})
        total = sum(ordered.values(), _ZERO).quantize(_Q)
        authoritative_count = sum(1 for item in candidate.evidence if item.authority == "official")
        vetoes = list(candidate.relation.missing_reasons if candidate.relation is not None else ())
        vetoes.extend(
            f"missing_{reason.split(':', 1)[0]}" if reason.endswith(":missing")
            else reason.replace(":", "_").upper()
            for reason in missing
        )
        qualified = bool(candidate.relation and candidate.relation.eligible_for_ranking) and not missing
        research = None
        suitability = None
        candidate_key = ticker or candidate.entity_id or f"unresolved:{candidate.event.event_id}"
        if contract_version == 2:
            candidate_key = candidate.security_id or candidate.entity_id or candidate_key
            research, suitability = _assess_v2_candidate(
                candidate,
                candidate_key=candidate_key,
                ticker=ticker,
                components=ordered,
                evidence_consistent=evidence_consistent and event_consistent,
            )
            qualified = research.research_state == "analysis_ready" and suitability.state == "eligible"
            missing = list(suitability.missing_reasons)
            vetoes = list(dict.fromkeys((*research.limitations, *suitability.veto_reasons)))
        ranked.append(
            RankedCandidate(
                ticker=ticker,
                candidate_key=candidate_key,
                event_id=candidate.event.event_id,
                relationship_type=(candidate.relation.relationship_type
                                   if candidate.relation is not None else "unresolved"),
                evidence=tuple(candidate.evidence),
                exposure_evidence=(candidate.relation.exposure_evidence
                                   if candidate.relation is not None and exposure_consistent else ()),
                components=ordered,
                missing_reasons=tuple(missing),
                total_score=total,
                authoritative_evidence_count=authoritative_count,
                qualified=qualified,
                veto_reasons=tuple(dict.fromkeys(vetoes)),
                research=research,
                suitability=suitability,
            )
        )
    ranked.sort(key=candidate_sort_key)
    return [replace(candidate, rank=index) for index, candidate in enumerate(ranked, 1)]


def _assess_v2_candidate(
    candidate: CandidateInput,
    *,
    candidate_key: str,
    ticker: str | None,
    components: Mapping[str, Decimal],
    evidence_consistent: bool,
) -> tuple[ResearchCandidate, SuitabilityEvaluation]:
    evidence_by_id = {evidence_key(item): item for item in candidate.evidence}
    event_ids = tuple(sorted({candidate.event.event_id}))
    themes = tuple(sorted(set(candidate.theme_ids or candidate.event.theme_ids)))
    roles = tuple(sorted(set(candidate.roles or (
        (candidate.relation.role,) if candidate.relation is not None else ()
    ))))
    resolved = bool(candidate.security_id and ticker and candidate.relation is not None)
    unresolved = bool(candidate.explicit_unresolved_identity and candidate.entity_id)
    limitations = list(candidate.limitations)
    if not candidate.evidence or not candidate.event.evidence or not evidence_consistent:
        state = "research_rejected"
        limitations.append("source_backed_event_required")
    elif not resolved:
        state = "unresolved" if unresolved else "research_rejected"
        limitations.append("security_identity_unresolved")
    else:
        typed_exposure = bool(candidate.exposure_fact_ids) and bool(candidate.relation.exposure_evidence)
        matching_exposure = all(
            item_id in evidence_by_id for item_id in candidate.exposure_fact_ids
        )
        # Task 6 facts use their fact IDs rather than source item IDs. The exact
        # fact/source relationship is revalidated by the v2 persistence trigger;
        # this layer requires both typed fact identities and retained primary items.
        if candidate.exposure_fact_ids and not matching_exposure:
            matching_exposure = bool(candidate.relation.exposure_evidence)
        if not typed_exposure or not matching_exposure:
            state = "resolved"
            limitations.append("typed_primary_exposure_missing")
        else:
            state = "exposure_supported"
            if not candidate.supporting_evidence_ids:
                limitations.append("supporting_evidence_missing")
            if not candidate.opposing_evidence_ids:
                limitations.append("opposing_evidence_missing")
            missing_rel = set(candidate.supporting_evidence_ids + candidate.opposing_evidence_ids) - set(evidence_by_id)
            if missing_rel:
                limitations.append("candidate_evidence_relationship_missing")
            if candidate.reference_state != "current":
                limitations.append(f"reference_{candidate.reference_state}")
            if not limitations:
                state = "analysis_ready"

    priority_components = MappingProxyType({
        name: components[name]
        for name in ("materiality", "authority_corroboration", "exposure", "recency")
    })
    priority_score = sum(priority_components.values(), _ZERO).quantize(_Q)
    research = ResearchCandidate(
        candidate_key=candidate_key,
        security_id=candidate.security_id,
        ticker=ticker,
        entity_id=candidate.entity_id,
        event_ids=event_ids,
        theme_ids=themes,
        roles=roles,
        research_state=state,  # type: ignore[arg-type]
        priority_components=priority_components,
        priority_score=priority_score,
        evidence=tuple(candidate.evidence),
        exposure_fact_ids=tuple(sorted(set(candidate.exposure_fact_ids))),
        supporting_evidence_ids=tuple(sorted(set(candidate.supporting_evidence_ids))),
        opposing_evidence_ids=tuple(sorted(set(candidate.opposing_evidence_ids))),
        evidence_receipt_ids=MappingProxyType(dict(candidate.lineage.evidence_receipt_ids)
                                              if candidate.lineage else {}),
        limitations=tuple(dict.fromkeys(limitations)),
        adverse_paths=tuple(sorted(set(candidate.adverse_paths))),
    )

    missing: list[str] = []
    veto: list[str] = []
    if state != "analysis_ready":
        missing.append("analysis_not_ready")
    # Task 8 has no protected issuer-valuation ledger.  CandidateInput is an
    # in-process transport shape, so a producer-provided `passed` value cannot
    # seal this protected gate.  Keep research available and fail closed until
    # an additive server-owned valuation contract exists.
    state_inputs = {
        "reference": candidate.reference_state,
        "valuation": "missing",
        "quote": candidate.quote_state,
        "portfolio": candidate.portfolio_state,
        "cash": candidate.cash_state,
    }
    for name, value in state_inputs.items():
        if value == "failed":
            veto.append(f"{name}_failed")
        elif value != "passed" and not (name == "reference" and value == "current"):
            missing.append(f"{name}_{value}")
    for name, value in {
        "liquidity": candidate.liquidity,
        "portfolio_overlap": candidate.overlap,
        "portfolio_concentration": candidate.concentration,
    }.items():
        if value is None:
            missing.append(f"{name}_missing")
    if candidate.holding_weight is None:
        missing.append("holding_weight_missing")
    else:
        weight = _fixed(candidate.holding_weight, "holding_weight")
        if weight >= Decimal("0.40"):
            veto.append("holding_weight_concentrated")
    if candidate.concentration is not None and _fixed(candidate.concentration, "concentration") >= Decimal("0.40"):
        veto.append("portfolio_concentration_high")
    lineage = candidate.lineage
    if lineage is None:
        missing.append("protected_lineage_missing")
    else:
        lineage_values = {
            "run": lineage.run_id,
            "observation_time": lineage.observed_at,
            "policy": lineage.policy_version,
            "reference_manifest": lineage.reference_manifest_id,
            "reference_revision": lineage.reference_revision,
            "reference_expiry": lineage.reference_expires_at,
            "security_revision": lineage.security_revision_id,
            "quote_receipt": lineage.quote_receipt_id,
            "quote_as_of": lineage.quote_as_of,
            "quote_expiry": lineage.quote_expires_at,
            "portfolio_revision": lineage.portfolio_revision,
            "cash_revision": lineage.cash_revision,
        }
        missing.extend(f"{name}_missing" for name, value in lineage_values.items() if value is None or value == "")
        if set(research.supporting_evidence_ids + research.opposing_evidence_ids) - set(lineage.evidence_receipt_ids):
            missing.append("source_receipt_lineage_missing")
    suitability_state: Literal["unknown", "eligible", "vetoed"] = (
        "vetoed" if veto else "unknown" if missing else "eligible"
    )
    suitability = SuitabilityEvaluation(
        candidate_key=candidate_key,
        state=suitability_state,
        component_scores=MappingProxyType({
            name: components[name]
            for name in ("portfolio_relevance", "liquidity", "duplication_penalty", "concentration_penalty")
        }),
        missing_reasons=tuple(dict.fromkeys(missing)),
        veto_reasons=tuple(dict.fromkeys(veto)),
        lineage=lineage,
    )
    return research, suitability


__all__ = [
    "CandidateInput",
    "CandidateLineage",
    "RankedCandidate",
    "ResearchCandidate",
    "SuitabilityEvaluation",
    "candidate_sort_key",
    "rank_candidates",
]
