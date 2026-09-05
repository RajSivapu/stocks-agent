from decimal import Decimal

from lib.intelligence.ranking import CandidateInput, rank_candidates
from lib.intelligence.relationships import propose_relation
from tests.test_intelligence_themes import event, source_item


def candidate(ticker: str, *, evidence=None, complete: bool = True):
    items = evidence or [
        source_item(20, authority="official", provider="sec_edgar", exposure_kind="revenue"),
        source_item(21, authority="radar", provider="gdelt"),
    ]
    relation = propose_relation(event(), ticker=ticker, role="supplier", evidence=items)
    value = Decimal("0.500000") if complete else None
    return CandidateInput(
        ticker=ticker,
        event=event(),
        relation=relation,
        evidence=tuple(items),
        authority_corroboration=value,
        exposure_strength=value,
        recency=value,
        portfolio_relevance=value,
        liquidity=value,
    )


def test_second_order_candidate_requires_authoritative_exposure():
    social = source_item(22, authority="hypothesis", provider="social")
    relation = propose_relation(event(), ticker="SUPP", role="supplier", evidence=[social])

    assert relation.exposure_status == "insufficient"
    assert relation.eligible_for_ranking is False


def test_ranking_is_stable_and_penalizes_holdings_and_plans_overlap():
    ranked = rank_candidates(
        [candidate("PLAN"), candidate("NEW"), candidate("OWNED")],
        holdings={"OWNED": Decimal("0.18")},
        plans={"monthly-vti": {"ticker": "PLAN", "weight": Decimal("0.10")}},
    )

    assert [row.ticker for row in ranked] == ["NEW", "PLAN", "OWNED"]
    assert ranked[1].components["duplication_penalty"] < 0
    assert ranked[2].components["concentration_penalty"] < 0
    assert [row.rank for row in ranked] == [1, 2, 3]


def test_ranking_records_every_fixed_point_component_and_missing_reason():
    ranked = rank_candidates([candidate("MISS", complete=False)])
    row = ranked[0]

    assert tuple(row.components) == (
        "materiality",
        "authority_corroboration",
        "exposure",
        "recency",
        "portfolio_relevance",
        "liquidity",
        "duplication_penalty",
        "concentration_penalty",
    )
    assert all(value.as_tuple().exponent == -6 for value in row.components.values())
    assert set(row.missing_reasons) == {
        "authority_corroboration:missing",
        "exposure:missing",
        "recency:missing",
        "portfolio_relevance:missing",
        "liquidity:missing",
        "holding_weight:missing",
        "overlap:missing",
        "concentration:missing",
    }
    assert row.qualified is False


def test_missing_liquidity_or_high_holding_concentration_is_insufficient():
    missing_liquidity = candidate("MISS", complete=True)
    missing_liquidity = CandidateInput(
        **{field: getattr(missing_liquidity, field) for field in (
            "ticker", "event", "relation", "evidence", "authority_corroboration",
            "exposure_strength", "recency", "portfolio_relevance", "duplication_penalty",
        )},
        liquidity=None,
    )
    concentrated = candidate("CONC", complete=True)

    missing = rank_candidates([missing_liquidity])[0]
    held = rank_candidates([concentrated], holdings={"CONC": Decimal("0.42")})[0]

    assert missing.qualified is False
    assert "liquidity:missing" in missing.missing_reasons
    assert held.qualified is False
    assert "holding_weight:concentrated" in held.missing_reasons


def test_missing_overlap_is_insufficient_and_is_not_substituted_from_holding_weight():
    row = rank_candidates([candidate("HELD")], holdings={"HELD": Decimal("0.42")})[0]

    assert row.qualified is False
    assert "overlap:missing" in row.missing_reasons
    assert "HOLDING_WEIGHT_CONCENTRATED" in row.veto_reasons
