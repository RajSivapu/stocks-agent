from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from lib.intelligence.comparison import (
    ComparisonCandidate,
    ComparisonContext,
    EvidenceValue,
    PersonalComparison,
    compare_candidate,
)


def evidence(value, *evidence_ids, status="supported"):
    return EvidenceValue(value=value, evidence_ids=evidence_ids, status=status)


def candidate(ticker="AA", **overrides):
    values = {
        "ticker": ticker,
        "role": "peer",
        "diversification": evidence("different end markets", "aa-10k"),
        "overlap": evidence(Decimal("0.35"), "aa-revenue", "cenx-revenue"),
        "cost": evidence(None, status="missing"),
        "valuation": evidence(Decimal("8.2"), "aa-valuation"),
        "concentration": evidence(Decimal("0.12"), "portfolio-snapshot"),
        "liquidity": evidence(Decimal("25000000"), "aa-market-data"),
        "drawdowns": {
            "AA": evidence(Decimal("0.28"), "aa-history"),
            "CENX": evidence(Decimal("0.34"), "cenx-history"),
        },
        "correlation": evidence(Decimal("0.62"), "matched-history"),
        "scenario_evidence_ids": {
            "bear": ("aa-risk",),
            "base": ("aa-10k",),
            "bull": ("aa-catalyst",),
        },
    }
    values.update(overrides)
    return ComparisonCandidate(**values)


def context(*, holdings=(), owner_plans=()):
    return ComparisonContext(holdings=holdings, owner_plans=owner_plans)


def test_candidate_is_compared_with_current_database_anchors_only():
    result = compare_candidate(
        candidate(),
        context(
            holdings=({"ticker": "CENX", "shares": "43.7482"},),
            owner_plans=(
                {"ticker": "VTI", "active": True, "amount": "300"},
                {"ticker": "VXUS", "active": False, "amount": "100"},
            ),
        ),
    )

    assert result.anchor_tickers == ("CENX", "VTI")
    assert set(result.scenarios) == {"bear", "base", "bull"}
    assert "VXUS" not in result.anchor_tickers
    assert result.drawdowns["CENX"].evidence_ids == ("cenx-history",)


def test_anchor_selection_never_invents_cenx_or_vti():
    result = compare_candidate(
        candidate("MSFT"),
        context(
            holdings=({"ticker": "AAPL", "shares": "1"},),
            owner_plans=({"ticker": "SCHD", "active": True, "amount": "50"},),
        ),
    )

    assert result.anchor_tickers == ("AAPL", "SCHD")
    assert "CENX" not in result.anchor_tickers
    assert "VTI" not in result.anchor_tickers
    missing = compare_candidate(candidate("MSFT"), context())
    assert missing.status == "insufficient"
    assert "anchor:missing" in missing.limitations


def test_missing_expense_valuation_liquidity_and_drawdown_are_explicit():
    result = compare_candidate(
        candidate(
            cost=evidence(None, status="missing"),
            valuation=evidence(None, status="missing"),
            liquidity=evidence(None, status="missing"),
            drawdowns={"AA": evidence(None, status="missing")},
            correlation=evidence(None, status="missing"),
        ),
        context(holdings=({"ticker": "CENX", "shares": "1"},)),
    )

    assert set(result.limitations) >= {
        "cost:missing",
        "valuation:missing",
        "liquidity:missing",
        "correlation:missing",
        "drawdown:AA:missing",
        "drawdown:CENX:missing",
    }


@pytest.mark.parametrize("factor", ["overlap", "concentration"])
def test_overlap_or_concentration_evidence_can_veto_standalone_case(factor):
    result = compare_candidate(
        candidate(**{factor: evidence(Decimal("0.9"), f"{factor}-proof", status="veto")}),
        context(holdings=({"ticker": "CENX", "shares": "1"},)),
    )

    assert result.status == "vetoed"
    assert result.veto_reasons == (f"{factor}_veto",)


def test_scenarios_are_conditional_evidence_cited_and_not_forecasts():
    result = compare_candidate(candidate(), context())

    for name, scenario in result.scenarios.items():
        assert scenario.name == name
        assert scenario.evidence_ids
        assert scenario.prose.startswith("If the cited ")
        lowered = scenario.prose.lower()
        assert "price target" not in lowered
        assert "probability" not in lowered
        assert "allocate" not in lowered
        assert "guarantee" not in lowered


def test_comparison_is_deeply_read_only_and_has_no_mutation_surface():
    result = compare_candidate(candidate(), context())

    with pytest.raises(FrozenInstanceError):
        result.role = "replacement"
    with pytest.raises(TypeError):
        result.scenarios["base"] = result.scenarios["bear"]
    with pytest.raises(TypeError):
        result.drawdowns["AA"] = evidence(Decimal("0"), "replacement-history")
    assert not hasattr(PersonalComparison, "apply")
    assert not hasattr(PersonalComparison, "rebalance")
