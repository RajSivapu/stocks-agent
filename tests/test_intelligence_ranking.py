from dataclasses import replace
from decimal import Decimal

from lib.intelligence.ranking import (
    CandidateInput,
    CandidateLineage,
    rank_candidates,
)
from lib.intelligence.relationships import propose_relation
from lib.intelligence.themes import evidence_key
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


def test_relationship_pressure_preserves_primary_and_adverse_before_corroboration():
    primary = source_item(
        30, authority="official", provider="sec_edgar", exposure_kind="filing",
    )
    optional = [source_item(index) for index in range(31, 39)]
    adverse_base = source_item(39)
    adverse = replace(adverse_base, metadata={
        **adverse_base.metadata,
        "adverse_path": True,
        "claim_polarity": "denied",
    })

    relation = propose_relation(
        event(), ticker="SUPP", role="supplier",
        evidence=[primary, *optional, adverse],
    )

    retained = {evidence_key(item) for item in relation.evidence}
    assert len(retained) == 8
    assert evidence_key(primary) in retained
    assert evidence_key(adverse) in retained
    assert evidence_key(adverse) not in relation.dropped_evidence_keys


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


def v2_candidate(ticker: str = "AAA", **overrides):
    value = candidate(ticker)
    evidence_ids = tuple(evidence_key(item) for item in value.evidence)
    lineage = CandidateLineage(
        run_id="11111111-1111-4111-8111-111111111111",
        observed_at="2026-09-04T12:00:00.000Z",
        policy_version=1,
        reference_manifest_id="22222222-2222-4222-8222-222222222222",
        reference_revision=2,
        reference_expires_at="2026-09-05T12:00:00.000Z",
        security_revision_id="33333333-3333-4333-8333-333333333333",
        quote_receipt_id="44444444-4444-4444-8444-444444444444",
        quote_as_of="2026-09-04T11:59:00.000Z",
        quote_expires_at="2026-09-04T12:05:00.000Z",
        evidence_receipt_ids={
            evidence_ids[0]: "55555555-5555-4555-8555-555555555555",
            evidence_ids[1]: "66666666-6666-4666-8666-666666666666",
        },
        portfolio_revision="portfolio:7",
        cash_revision="cash:9",
    )
    fields = {
        name: getattr(value, name)
        for name in value.__dataclass_fields__
    }
    fields.update(
        contract_version=2,
        security_id=f"sec:{ticker}",
        entity_id=f"issuer:{ticker}",
        theme_ids=("magnets",),
        exposure_fact_ids=("77777777-7777-4777-8777-777777777777",),
        supporting_evidence_ids=(evidence_ids[0],),
        opposing_evidence_ids=(evidence_ids[1],),
        lineage=lineage,
        valuation_state="passed",
        quote_state="passed",
        portfolio_state="passed",
        cash_state="passed",
        holding_weight=Decimal("0"),
        overlap=Decimal("0"),
        concentration=Decimal("0"),
    )
    fields.update(overrides)
    return CandidateInput(**fields)


def test_v2_research_priority_excludes_portfolio_and_valuation_inputs():
    eligible = rank_candidates([v2_candidate()], contract_version=2)[0]
    unknown = rank_candidates([
        v2_candidate(
            portfolio_relevance=None,
            holding_weight=None,
            overlap=None,
            concentration=None,
            valuation_state="missing",
            portfolio_state="unavailable",
            cash_state="unavailable",
        )
    ], contract_version=2)[0]

    assert eligible.research.priority_score == unknown.research.priority_score
    assert unknown.research.research_state == "analysis_ready"
    assert unknown.suitability.state == "unknown"
    assert "valuation_missing" in unknown.suitability.missing_reasons
    assert "portfolio_overlap_missing" in unknown.suitability.missing_reasons
    assert unknown.qualified is False


def test_v2_unbacked_caller_valuation_cannot_promote_an_action():
    """There is no protected issuer-valuation ledger in the current schema."""
    row = rank_candidates([v2_candidate(
        valuation_state="passed", quote_state="passed",
        portfolio_state="passed", cash_state="passed",
    )], contract_version=2)[0]

    assert row.research.research_state == "analysis_ready"
    assert row.suitability.state == "unknown"
    assert "valuation_missing" in row.suitability.missing_reasons
    assert row.qualified is False


def test_v2_known_gate_failure_is_vetoed_and_retains_missing_reasons():
    row = rank_candidates([
        v2_candidate(
            holding_weight=Decimal("0.42"),
            overlap=None,
            valuation_state="failed",
        )
    ], contract_version=2)[0]

    assert row.suitability.state == "vetoed"
    assert "valuation_missing" in row.suitability.missing_reasons
    assert "holding_weight_concentrated" in row.suitability.veto_reasons
    assert "portfolio_overlap_missing" in row.suitability.missing_reasons


def test_v2_stale_reference_blocks_every_action_but_retains_research():
    row = rank_candidates([
        v2_candidate(reference_state="stale")
    ], contract_version=2)[0]

    assert row.research.research_state == "exposure_supported"
    assert row.suitability.state == "unknown"
    assert "reference_stale" in row.suitability.missing_reasons
    assert row.qualified is False


def test_v2_requires_typed_exposure_and_supporting_and_opposing_evidence_for_analysis():
    no_fact = rank_candidates([
        v2_candidate(exposure_fact_ids=())
    ], contract_version=2)[0]
    no_opposition = rank_candidates([
        v2_candidate(opposing_evidence_ids=())
    ], contract_version=2)[0]

    assert no_fact.research.research_state == "resolved"
    assert "typed_primary_exposure_missing" in no_fact.research.limitations
    assert no_opposition.research.research_state == "exposure_supported"
    assert "opposing_evidence_missing" in no_opposition.research.limitations
    assert no_fact.qualified is no_opposition.qualified is False


def test_v2_explicit_unresolved_identity_survives_without_security_or_ticker():
    row = rank_candidates([
        v2_candidate(
            ticker=None,
            security_id=None,
            entity_id="unresolved:private-recipient",
            relation=None,
            explicit_unresolved_identity=True,
            exposure_fact_ids=(),
            supporting_evidence_ids=(),
            opposing_evidence_ids=(),
            lineage=None,
        )
    ], contract_version=2)[0]

    assert row.research.ticker is None
    assert row.research.security_id is None
    assert row.research.research_state == "unresolved"
    assert row.suitability.state == "unknown"
