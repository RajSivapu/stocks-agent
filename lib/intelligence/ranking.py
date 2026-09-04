"""Reproducible fixed-point candidate ranking with explicit missing data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from lib.intelligence.normalize import SourceItem
from lib.intelligence.relationships import EventRelationship
from lib.intelligence.themes import MarketEvent


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
    ticker: str
    event: MarketEvent
    relation: EventRelationship
    evidence: tuple[SourceItem, ...]
    authority_corroboration: Decimal | int | str | None = None
    exposure_strength: Decimal | int | str | None = None
    recency: Decimal | int | str | None = None
    portfolio_relevance: Decimal | int | str | None = None
    liquidity: Decimal | int | str | None = None
    duplication_penalty: Decimal | int | str | None = None


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    ticker: str
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
    return (-candidate.total_score, -candidate.authoritative_evidence_count, candidate.ticker)


def rank_candidates(
    candidates: Sequence[CandidateInput],
    *,
    holdings: Mapping[str, object] | None = None,
    plans: object = None,
) -> list[RankedCandidate]:
    holding_positions = _holding_positions(holdings)
    plan_positions = _plan_positions(plans)
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        ticker = candidate.ticker.strip().upper()
        if ticker != candidate.relation.ticker:
            raise ValueError("candidate ticker must match its relationship")
        missing: list[str] = []
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

        holding_overlap = ticker in holding_positions
        plan_overlap = ticker in plan_positions
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
        concentration = -holding_positions.get(ticker, _ZERO) - (
            plan_positions.get(ticker, _ZERO) / Decimal("2")
        )
        values["concentration_penalty"] = max(Decimal("-1.000000"), concentration).quantize(_Q)

        ordered = MappingProxyType({name: values[name].quantize(_Q) for name in _COMPONENTS})
        total = sum(ordered.values(), _ZERO).quantize(_Q)
        authoritative_count = sum(1 for item in candidate.evidence if item.authority == "official")
        vetoes = list(candidate.relation.missing_reasons)
        vetoes.extend(f"missing_{reason.split(':', 1)[0]}" for reason in missing)
        qualified = candidate.relation.eligible_for_ranking and not missing
        ranked.append(
            RankedCandidate(
                ticker=ticker,
                candidate_key=ticker,
                event_id=candidate.event.event_id,
                relationship_type=candidate.relation.relationship_type,
                evidence=tuple(candidate.evidence),
                exposure_evidence=candidate.relation.exposure_evidence,
                components=ordered,
                missing_reasons=tuple(missing),
                total_score=total,
                authoritative_evidence_count=authoritative_count,
                qualified=qualified,
                veto_reasons=tuple(dict.fromkeys(vetoes)),
            )
        )
    ranked.sort(key=candidate_sort_key)
    return [replace(candidate, rank=index) for index, candidate in enumerate(ranked, 1)]


__all__ = [
    "CandidateInput",
    "RankedCandidate",
    "candidate_sort_key",
    "rank_candidates",
]
