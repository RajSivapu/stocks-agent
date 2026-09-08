from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from lib.intelligence.discovery import (
    build_reverse_discovery_tasks,
    detect_events,
    expand_value_chain,
    load_theme_taxonomy,
    match_themes,
)
from lib.intelligence.normalize import SourceItem


NOW = datetime(2026, 9, 6, 15, tzinfo=timezone.utc)


def item(
    title: str,
    summary: str,
    *,
    provider: str = "doe",
    upstream_item_id: str = "award-1",
    metadata: dict[str, object] | None = None,
    security_ids: tuple[str, ...] = (),
    effective_at: datetime | None = None,
) -> SourceItem:
    return SourceItem(
        provider=provider,
        upstream_item_id=upstream_item_id,
        canonical_url=f"https://{provider}.example/{upstream_item_id}",
        title=title,
        summary=summary,
        canonical_content=f"{title}\n{summary}",
        content_hash=(upstream_item_id.encode().hex() + "0" * 64)[:64],
        published_at=NOW,
        effective_at=effective_at,
        retrieved_at=NOW,
        authority="official",
        metadata=MappingProxyType({
            "publisher_id": provider,
            **(metadata or {}),
        }),
        security_ids=security_ids,
    )


def test_private_magnet_announcement_survives_without_ticker():
    source = item(
        "DOE awards Niron Magnetics funding",
        "The award expands rare-earth-free permanent magnet manufacturing.",
        metadata={"organization_names": ["Niron Magnetics"]},
    )

    event = detect_events((source,), load_theme_taxonomy())[0]

    assert event.event_type == "awarded_funding"
    assert event.theme_ids == ("critical_minerals_magnets",)
    assert event.security_ids == ()
    assert event.research_state == "observed"
    assert event.execution_allowed is False


def test_event_identity_depends_on_claim_date_publisher_and_evidence_not_ticker():
    source = item(
        "DOE awards magnet manufacturing funding",
        "The award expands permanent magnet manufacturing.",
    )
    with_ticker = replace(source, security_ids=("AAA",))

    first = detect_events((source,), load_theme_taxonomy())[0]
    second = detect_events((with_ticker,), load_theme_taxonomy())[0]
    other_publisher = detect_events((replace(
        source,
        provider="dod",
        upstream_item_id="award-2",
        canonical_url="https://dod.example/award-2",
        metadata=MappingProxyType({"publisher_id": "dod"}),
    ),), load_theme_taxonomy())[0]

    assert first.event_id == second.event_id
    assert first.event_id != other_publisher.event_id


def test_event_identity_uses_the_millisecond_timestamp_precision_persisted_by_gateway():
    source = item(
        "DOE awards magnet manufacturing funding",
        "The award expands permanent magnet manufacturing.",
    )
    precise = replace(source, published_at=NOW.replace(microsecond=123_456))
    persisted = replace(source, published_at=NOW.replace(microsecond=123_000))
    next_millisecond = replace(source, published_at=NOW.replace(microsecond=124_000))

    precise_event = detect_events((precise,), load_theme_taxonomy())[0]
    persisted_event = detect_events((persisted,), load_theme_taxonomy())[0]
    next_event = detect_events((next_millisecond,), load_theme_taxonomy())[0]

    assert precise_event.event_id == persisted_event.event_id
    assert precise_event.event_id != next_event.event_id


@pytest.mark.parametrize(
    ("title", "summary", "effective_at", "expected"),
    [
        ("Agency issues a statement", "Officials discuss grid policy.", None, "statement"),
        ("New demand forecast", "Power demand may rise next year.", None, "forecast"),
        ("Agency proposes copper rule", "A proposed policy opens for comment.", None, "proposed_policy"),
        ("Final copper rule", "The policy becomes effective.", NOW, "effective_policy"),
        ("DOE announces funding", "Funding is planned for grid equipment.", None, "announced_funding"),
        ("DOE awards funding", "A grant was awarded for grid equipment.", None, "awarded_funding"),
        ("Supplier wins contract", "A supply contract was signed.", None, "contract"),
        ("Company expands capacity", "A new manufacturing plant opens.", None, "capacity"),
        ("Copper demand rises", "Demand for conductors increased.", None, "demand"),
        ("Uranium supply falls", "Supply declined after an outage.", None, "supply"),
        ("Quarterly earnings", "Revenue beat expectations.", None, "earnings"),
        ("Company acquisition closes", "The transaction closed.", None, "transaction"),
    ],
)
def test_event_types_are_separated(title, summary, effective_at, expected):
    event = detect_events((item(
        title,
        summary,
        upstream_item_id=expected,
        effective_at=effective_at,
        metadata={"theme_id": "industrial_infrastructure"},
    ),), load_theme_taxonomy())[0]

    assert event.event_type == expected


def test_initial_taxonomy_is_versioned_typed_and_contains_no_action_authority():
    taxonomy = load_theme_taxonomy()

    assert taxonomy.version == 1
    assert set(taxonomy.themes) == {
        "critical_minerals_magnets",
        "aluminum_copper",
        "data_center_power",
        "nuclear_uranium",
        "robotics",
    }
    encoded = taxonomy.canonical_json.casefold()
    assert '"execution_allowed":false' in encoded
    assert all(word not in encoded for word in ('"buy"', '"sell"', '"portfolio_mutation"'))
    assert all(
        edge.invalidation_rule and edge.evidence_requirement
        for theme in taxonomy.themes.values()
        for edge in theme.value_chain
    )
    assert any(
        edge.adverse_path
        for theme in taxonomy.themes.values()
        for edge in theme.value_chain
    )


def test_aluminum_taxonomy_models_data_center_electricity_cost_as_adverse_smelting_path():
    theme = load_theme_taxonomy().themes["aluminum_copper"]

    edge = next(row for row in theme.value_chain
                if row.role == "data_center_electricity_cost_smelting")

    assert edge.direction == "adverse"
    assert edge.geography == "United States"
    assert edge.horizon == "near to medium term"
    assert edge.evidence_requirement == (
        "Primary power-price, smelter-cost, curtailment, or capacity evidence"
    )
    assert edge.adverse_path is True
    assert edge.invalidation_rule == (
        "No verified data-center power-price effect on smelter economics or output"
    )


def test_theme_matching_uses_phrase_boundaries_and_returns_typed_matches():
    taxonomy = load_theme_taxonomy()
    source = item(
        "Data center power buildout",
        "Utilities need transformers, switchgear, cooling and battery storage.",
        upstream_item_id="power-1",
    )

    matches = match_themes(source, taxonomy)

    assert matches[0].theme_id == "data_center_power"
    assert {"data center", "transformers"} <= set(matches[0].matched_terms)
    assert match_themes(item("Magnificent result", "No material market topic.", upstream_item_id="none"), taxonomy) == ()


def test_reverse_discovery_is_bounded_fair_keyless_and_contains_no_company_list():
    source = item(
        "DOE awards Niron Magnetics funding",
        "The award expands rare-earth-free permanent magnet manufacturing.",
        metadata={"organization_names": ["Niron Magnetics"]},
    )
    event = detect_events((source,), load_theme_taxonomy())[0]
    hypotheses = expand_value_chain(event, load_theme_taxonomy())

    tasks = build_reverse_discovery_tasks(event, hypotheses, max_tasks=4)

    assert all(row.theme_terms[:2] == ("critical minerals", "rare earth")
               for row in hypotheses)
    assert len(tasks) == 4
    assert all(task.provider == "gdelt" for task in tasks)
    assert all(task.capability_id == "gdelt_theme_search" for task in tasks)
    assert all(task.query_kind == "theme_search" for task in tasks)
    assert all(task.execution_allowed is False for task in tasks)
    assert len({task.role for task in tasks}) == len(tasks)
    assert {task.adverse_path for task in tasks} == {False, True}
    assert all("niron" not in task.query_text.casefold() for task in tasks)
    assert all(any(term in task.query_text.casefold()
                   for term in ("critical minerals", "rare earth", "permanent magnet"))
               for task in tasks)
    assert all(task.invalidation_rule for task in tasks)


def test_reverse_discovery_uses_exact_parenthesized_gdelt_boolean_blocks():
    source = item(
        "DOE awards Niron Magnetics funding",
        "The award expands rare-earth-free permanent magnet manufacturing.",
        metadata={"organization_names": ["Niron Magnetics"]},
    )
    event = detect_events((source,), load_theme_taxonomy())[0]

    tasks = build_reverse_discovery_tasks(
        event,
        expand_value_chain(event, load_theme_taxonomy()),
        max_tasks=2,
    )

    assert [task.query_text for task in tasks] == [
        '("permanent magnet motor" OR "electric motor demand" OR "critical minerals")',
        '("rare earth free magnet" OR "magnet substitute" OR "critical minerals")',
    ]
    assert [(task.geography, task.horizon) for task in tasks] == [
        ("global markets", "near to long term"),
        ("global markets", "medium to long term"),
    ]


def test_value_chain_roles_are_hypotheses_and_never_exposure_proof():
    source = item(
        "Robotics production expands",
        "Industrial robots need actuators, reducers, sensors and controls.",
        upstream_item_id="robotics-1",
    )
    event = detect_events((source,), load_theme_taxonomy())[0]

    hypotheses = expand_value_chain(event, load_theme_taxonomy())

    assert hypotheses
    assert all(row.status == "hypothesis" for row in hypotheses)
    assert all(row.exposure_supported is False for row in hypotheses)
    assert all(row.execution_allowed is False for row in hypotheses)
