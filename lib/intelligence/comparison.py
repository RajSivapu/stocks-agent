"""Immutable, evidence-cited comparisons against current owner records."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Mapping


EvidenceStatus = Literal["supported", "missing", "conflicting", "veto"]


def _ticker(value: object) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker or len(ticker) > 15:
        raise ValueError("ticker must be between 1 and 15 characters")
    return ticker


@dataclass(frozen=True, slots=True)
class EvidenceValue:
    value: Decimal | str | None
    evidence_ids: tuple[str, ...] = ()
    status: EvidenceStatus = "supported"

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))
        if self.status == "supported" and (self.value is None or not self.evidence_ids):
            raise ValueError("supported comparison values require a value and evidence")
        if self.status == "veto" and not self.evidence_ids:
            raise ValueError("comparison vetoes require evidence")
        if self.status in {"missing", "conflicting"} and self.value is not None:
            raise ValueError("unavailable comparison values cannot contain a value")


@dataclass(frozen=True, slots=True)
class Scenario:
    name: Literal["bear", "base", "bull"]
    prose: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonCandidate:
    ticker: str
    role: str
    diversification: EvidenceValue
    overlap: EvidenceValue
    cost: EvidenceValue
    valuation: EvidenceValue
    concentration: EvidenceValue
    liquidity: EvidenceValue
    drawdowns: Mapping[str, Decimal | None]
    correlation: EvidenceValue
    scenario_evidence_ids: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(
            self,
            "drawdowns",
            MappingProxyType({_ticker(key): value for key, value in self.drawdowns.items()}),
        )
        scenarios = {
            name: tuple(dict.fromkeys(self.scenario_evidence_ids.get(name, ())))
            for name in ("bear", "base", "bull")
        }
        object.__setattr__(self, "scenario_evidence_ids", MappingProxyType(scenarios))


@dataclass(frozen=True, slots=True)
class ComparisonContext:
    """The accepted read_context holdings and owner_plans projection."""

    holdings: tuple[Mapping[str, object], ...] = ()
    owner_plans: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "holdings", tuple(self.holdings))
        object.__setattr__(self, "owner_plans", tuple(self.owner_plans))


@dataclass(frozen=True, slots=True)
class PersonalComparison:
    candidate_ticker: str
    anchor_tickers: tuple[str, ...]
    role: str
    diversification: EvidenceValue
    overlap: EvidenceValue
    cost: EvidenceValue
    valuation: EvidenceValue
    concentration: EvidenceValue
    liquidity: EvidenceValue
    drawdowns: Mapping[str, Decimal | None]
    correlation: EvidenceValue
    scenarios: Mapping[str, Scenario]
    limitations: tuple[str, ...]
    status: Literal["eligible", "vetoed", "insufficient"]
    veto_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "drawdowns", MappingProxyType(dict(self.drawdowns)))
        object.__setattr__(self, "scenarios", MappingProxyType(dict(self.scenarios)))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "veto_reasons", tuple(self.veto_reasons))


_FACTORS = (
    "diversification",
    "overlap",
    "cost",
    "valuation",
    "concentration",
    "liquidity",
    "correlation",
)


def _current_anchors(context: ComparisonContext) -> tuple[str, ...]:
    anchors: list[str] = []
    for holding in context.holdings:
        ticker = _ticker(holding.get("ticker"))
        if ticker not in anchors:
            anchors.append(ticker)
    for plan in context.owner_plans:
        if plan.get("active") is not True:
            continue
        ticker = _ticker(plan.get("ticker"))
        if ticker not in anchors:
            anchors.append(ticker)
    return tuple(anchors)


def _scenarios(candidate: ComparisonCandidate) -> Mapping[str, Scenario]:
    descriptions = {
        "bear": "bear conditions hold, the cited risk evidence supports a downside case",
        "base": "base conditions hold, the cited operating evidence supports the central case",
        "bull": "bull conditions hold, the cited catalyst evidence supports an upside case",
    }
    return MappingProxyType({
        name: Scenario(
            name=name,  # type: ignore[arg-type]
            prose=f"If the cited {description}; this is conditional research, not a forecast.",
            evidence_ids=candidate.scenario_evidence_ids[name],
        )
        for name, description in descriptions.items()
    })


def compare_candidate(
    candidate: ComparisonCandidate,
    context: ComparisonContext,
) -> PersonalComparison:
    """Compare one qualified candidate without exposing any mutation operation."""

    anchors = _current_anchors(context)
    limitations: list[str] = []
    for name in _FACTORS:
        item = getattr(candidate, name)
        if item.status in {"missing", "conflicting"}:
            limitations.append(f"{name}:{item.status}")

    drawdowns = dict(candidate.drawdowns)
    drawdowns.setdefault(candidate.ticker, None)
    for ticker in anchors:
        drawdowns.setdefault(ticker, None)
    for ticker, value in drawdowns.items():
        if value is None:
            limitations.append(f"drawdown:{ticker}:missing")

    scenarios = _scenarios(candidate)
    for name, scenario in scenarios.items():
        if not scenario.evidence_ids:
            limitations.append(f"scenario:{name}:missing_evidence")

    veto_reasons = tuple(
        f"{name}_veto"
        for name in ("overlap", "concentration")
        if getattr(candidate, name).status == "veto"
    )
    if veto_reasons:
        status: Literal["eligible", "vetoed", "insufficient"] = "vetoed"
    elif limitations:
        status = "insufficient"
    else:
        status = "eligible"

    return PersonalComparison(
        candidate_ticker=candidate.ticker,
        anchor_tickers=anchors,
        role=candidate.role,
        diversification=candidate.diversification,
        overlap=candidate.overlap,
        cost=candidate.cost,
        valuation=candidate.valuation,
        concentration=candidate.concentration,
        liquidity=candidate.liquidity,
        drawdowns=drawdowns,
        correlation=candidate.correlation,
        scenarios=scenarios,
        limitations=tuple(dict.fromkeys(limitations)),
        status=status,
        veto_reasons=veto_reasons,
    )


__all__ = [
    "ComparisonCandidate",
    "ComparisonContext",
    "EvidenceValue",
    "PersonalComparison",
    "Scenario",
    "compare_candidate",
]
