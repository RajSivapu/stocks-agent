from dataclasses import replace
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
    metadata = {
        "item_id": f"00000000-0000-4000-8000-{index:012d}",
        "publisher_id": provider,
        "upstream_identity": f"original-{index}",
    }
    if exposure_kind is not None:
        metadata["exposure_kind"] = exposure_kind
    return SourceItem(
        provider=provider,
        upstream_item_id=f"item-{index}",
        canonical_url=f"https://{provider}.example/{index}",
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
        "publisher_independent_corroboration_required",
        "coverage_label_required",
    }


def test_syndicated_copies_do_not_count_as_dynamic_theme_corroboration():
    first = source_item(20, provider="gdelt")
    syndicated = replace(
        source_item(21, provider="finnhub"),
        metadata={
            **source_item(21, provider="finnhub").metadata,
            "publisher_id": "same-publisher",
            "upstream_identity": "wire-story-1",
        },
    )
    first = replace(first, metadata={
        **first.metadata,
        "publisher_id": "same-publisher",
        "upstream_identity": "wire-story-1",
    })

    proposal = propose_dynamic_theme(
        "novel cooling loop",
        (first, syndicated),
        coverage_label="two adapters; one upstream story",
    )

    assert proposal.eligible is False
    assert "publisher_independent_corroboration_required" in proposal.missing_reasons


def test_mirrored_identical_headlines_are_one_auditable_syndicated_story():
    first = replace(
        source_item(22),
        canonical_url="https://mirror-a.example/wire/cooling?id=7",
        title="Immersion cooling capacity: shared wire report",
        summary="Immersion cooling capacity: shared wire report",
        metadata={
            **source_item(22).metadata,
            "publisher_id": "mirror-a.example",
            "upstream_identity": "https://mirror-a.example/wire/cooling?id=7",
        },
    )
    second = replace(
        source_item(23),
        canonical_url="https://mirror-b.example/news/cooling?copy=7",
        title=first.title,
        summary=first.summary,
        metadata={
            **source_item(23).metadata,
            "publisher_id": "mirror-b.example",
            "upstream_identity": "https://mirror-b.example/news/cooling?copy=7",
        },
    )

    proposal = propose_dynamic_theme(
        "immersion cooling capacity", (first, second), coverage_label="two mirrors",
    )
    reversed_proposal = propose_dynamic_theme(
        "immersion cooling capacity", (second, first), coverage_label="two mirrors",
    )

    assert proposal.eligible is False
    assert "syndicated_evidence_not_independent" in proposal.missing_reasons
    assert len(proposal.evidence) == 2
    assert proposal.theme_id == reversed_proposal.theme_id
    assert [item.content_hash for item in proposal.evidence] == [
        item.content_hash for item in reversed_proposal.evidence
    ]


def test_shared_wire_attribution_collapses_reworded_mirror_headlines():
    first = replace(source_item(24), title="Cooling loop market: first wire wording", metadata={
        **source_item(24).metadata,
        "publisher_id": "mirror-a.example",
        "upstream_identity": "mirror-a-path",
        "syndication_id": "wire-story-77",
    })
    second = replace(source_item(25), title="Cooling loop market: altered mirror wording", metadata={
        **source_item(25).metadata,
        "publisher_id": "mirror-b.example",
        "upstream_identity": "mirror-b-path",
        "syndication_id": "wire-story-77",
    })

    proposal = propose_dynamic_theme(
        "cooling loop market", (first, second), coverage_label="one wire story",
    )

    assert proposal.eligible is False
    assert "syndicated_evidence_not_independent" in proposal.missing_reasons
    assert len(proposal.evidence) == 2


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
