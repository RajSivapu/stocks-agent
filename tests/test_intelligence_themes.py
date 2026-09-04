from datetime import datetime, timezone
from decimal import Decimal

from lib.intelligence.normalize import SourceItem
from lib.intelligence.relationships import propose_relation
from lib.intelligence.themes import (
    SEED_THEMES,
    build_market_event,
    propose_dynamic_theme,
)


NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def source_item(index: int, *, authority: str = "radar", provider: str = "gdelt", exposure_kind=None):
    metadata = {"item_id": f"00000000-0000-4000-8000-{index:012d}"}
    if exposure_kind is not None:
        metadata["exposure_kind"] = exposure_kind
    return SourceItem(
        provider=provider,
        upstream_item_id=f"item-{index}",
        canonical_url=f"https://example.com/{index}",
        title=f"Evidence {index}",
        summary=f"Bounded evidence {index}",
        canonical_content=f"content-{index}",
        content_hash=f"{index:064x}",
        published_at=NOW,
        effective_at=None,
        retrieved_at=NOW,
        authority=authority,
        metadata=metadata,
    )


def event():
    return build_market_event(
        event_type="policy_release",
        title="Grid investment announced",
        summary="An official release describes new grid investment.",
        materiality=Decimal("0.800000"),
        confidence=Decimal("0.900000"),
        evidence=[source_item(1, authority="official")],
        theme_ids=("energy_nuclear_grid",),
        occurred_at=NOW,
    )


def test_seed_taxonomy_is_exact_and_ordered():
    assert SEED_THEMES == (
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


def test_dynamic_theme_requires_evidence_corroboration_novelty_and_coverage():
    eligible = propose_dynamic_theme(
        "water cooling infrastructure",
        [source_item(2), source_item(3, authority="official", provider="doe")],
        coverage_label="GDELT and DOE; 24-hour bounded window",
    )
    seed_duplicate = propose_dynamic_theme(
        "energy nuclear grid",
        [source_item(4), source_item(5, authority="official", provider="doe")],
        coverage_label="GDELT and DOE; 24-hour bounded window",
    )
    uncovered = propose_dynamic_theme("novel topic", [source_item(6)], coverage_label="")

    assert eligible.eligible is True
    assert eligible.theme_id == propose_dynamic_theme(
        "water cooling infrastructure",
        [source_item(3, authority="official", provider="doe"), source_item(2)],
        coverage_label="GDELT and DOE; 24-hour bounded window",
    ).theme_id
    assert seed_duplicate.eligible is False
    assert "not_novel_from_seed_taxonomy" in seed_duplicate.missing_reasons
    assert set(uncovered.missing_reasons) == {
        "requires_two_accepted_items",
        "authoritative_or_corroborating_source_required",
        "coverage_label_required",
    }


def test_market_event_and_direct_or_second_order_links_keep_evidence():
    market_event = event()
    official = source_item(7, authority="official", provider="sec_edgar", exposure_kind="revenue")

    direct = propose_relation(market_event, ticker="GRID", role="direct", evidence=[official])
    supplier = propose_relation(market_event, ticker="SUPP", role="supplier", evidence=[official])

    assert direct.relationship_type == "direct"
    assert supplier.relationship_type == "second_order"
    assert direct.eligible_for_ranking is True
    assert supplier.eligible_for_ranking is True
    assert supplier.exposure_evidence == (official,)
