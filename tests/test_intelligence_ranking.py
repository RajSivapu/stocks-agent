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
    }
    assert row.qualified is False
