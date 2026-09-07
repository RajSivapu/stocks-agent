from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from types import MappingProxyType

import pytest

from lib.intelligence.entities import ReviewedAlias, resolve_entities
from lib.intelligence.normalize import SourceItem
from lib.intelligence.universe import (
    FormerIssuerName,
    IssuerIdentity,
    ReferenceManifest,
    ReferenceSnapshot,
    SecurityIdentity,
)


NOW = datetime(2026, 9, 6, 15, tzinfo=timezone.utc)


def issuer(entity_id: str, cik: str | None, name: str, former=()) -> IssuerIdentity:
    return IssuerIdentity(entity_id, cik, name, tuple(former), date(2020, 1, 1), None, ("fixture",))


def security(
    security_id: str,
    entity_id: str,
    ticker: str,
    *,
    exchange: str | None = "NASDAQ",
    instrument_type: str = "COMMON_STOCK",
    valid_from: date = date(2020, 1, 1),
    valid_to: date | None = None,
    aliases=(),
    eligible: bool = True,
    exclusions=(),
) -> SecurityIdentity:
    return SecurityIdentity(
        security_id, entity_id, ticker, exchange, instrument_type,
        valid_from, valid_to, tuple(aliases) or (ticker,), ("fixture",), eligible,
        tuple(exclusions),
    )


def reference() -> ReferenceSnapshot:
    issuers = (
        issuer("sec-cik:0000000001", "0000000001", "Alpha Incorporated", ("Alpha Systems",)),
        issuer("sec-cik:0000000002", "0000000002", "Zeta Corporation"),
        issuer("sec-cik:0000000003", "0000000003", "Dual Class Holdings"),
        issuer("foreign:0004", None, "Foreign Only Plc"),
        issuer("sec-cik:0000000005", "0000000005", "Collision Labs"),
        issuer("sec-cik:0000000006", "0000000006", "Collision Labs"),
        issuer("sec-cik:0000000007", "0000000007", "Renamed Company"),
    )
    securities = (
        security("sec:AAA", issuers[0].entity_id, "AAA"),
        security("sec:ZZZ", issuers[1].entity_id, "ZZZ", exchange="NYSE"),
        security("sec:DUALA", issuers[2].entity_id, "DUAL.A", aliases=("DUAL.A",)),
        security("sec:DUALC", issuers[2].entity_id, "DUAL.C", aliases=("DUAL.C",)),
        security("sec:FOREIGN", issuers[3].entity_id, "FRGN", exchange="LSE", eligible=False, exclusions=("foreign_only",)),
        security("sec:COLLIDE1", issuers[4].entity_id, "COLA"),
        security("sec:COLLIDE2", issuers[5].entity_id, "COLB"),
        security("sec:RENAME", issuers[6].entity_id, "OLD", valid_to=date(2024, 12, 31), aliases=("OLD",)),
        security("sec:RENAME", issuers[6].entity_id, "NEW", valid_from=date(2025, 1, 1), aliases=("OLD", "NEW")),
    )
    manifest = ReferenceManifest(
        "fixture:v1", "a" * 64, "https://sec.example/tickers", NOW, NOW, 1,
        "scope_not_guaranteed", "healthy", len(securities),
    )
    return ReferenceSnapshot(manifest, issuers, securities)


def text_item(
    text: str,
    *,
    metadata: dict[str, object] | None = None,
    entity_ids: tuple[str, ...] = (),
    security_ids: tuple[str, ...] = (),
    published_at: datetime = NOW,
) -> SourceItem:
    return SourceItem(
        provider="gdelt",
        upstream_item_id=text.casefold().replace(" ", "-")[:40],
        canonical_url="https://publisher.example/item",
        title=text,
        summary=text,
        canonical_content=text,
        content_hash="b" * 64,
        published_at=published_at,
        effective_at=None,
        retrieved_at=NOW,
        authority="radar",
        metadata=MappingProxyType(metadata or {}),
        entity_ids=entity_ids,
        security_ids=security_ids,
    )


def test_explicit_security_and_cik_precede_name_resolution():
    item = text_item(
        "A different company name",
        entity_ids=("cik:0000000002",),
        security_ids=("sec:AAA",),
    )

    result = resolve_entities(item, reference())

    assert [(row.security_id, row.matched_by) for row in result] == [
        ("sec:AAA", "explicit_security_id"),
        ("sec:ZZZ", "explicit_cik"),
    ]


def test_multiple_entities_do_not_collapse_to_first_sorted_security():
    item = text_item(
        "Alpha Incorporated signs agreement with Zeta Corporation",
        metadata={"organization_names": ["Alpha Incorporated", "Zeta Corporation"]},
    )

    resolutions = resolve_entities(item, reference())

    assert {row.security_id for row in resolutions if row.status == "resolved"} == {
        "sec:AAA", "sec:ZZZ",
    }


def test_observed_and_dated_former_names_resolve_only_in_their_valid_period():
    fixture = reference()
    first = replace(
        fixture.issuers[0],
        observed_names=("Alpha Incorporated", "Alpha Incorporated Class A"),
        dated_former_names=(FormerIssuerName(
            "Alpha Systems", date(2020, 1, 1), date(2024, 12, 31),
        ),),
    )
    fixture = replace(fixture, issuers=(first, *fixture.issuers[1:]))

    observed = resolve_entities(
        text_item("Alpha Incorporated Class A",
                  metadata={"organization_names": ["Alpha Incorporated Class A"]}),
        fixture,
    )
    former_in_period = resolve_entities(
        text_item("Alpha Systems", metadata={"organization_names": ["Alpha Systems"]},
                  published_at=datetime(2023, 1, 1, tzinfo=timezone.utc)),
        fixture,
    )
    former_outside_period = resolve_entities(
        text_item("Alpha Systems", metadata={"organization_names": ["Alpha Systems"]}),
        fixture,
    )

    assert [(row.security_id, row.matched_by) for row in observed] == [
        ("sec:AAA", "observed_name"),
    ]
    assert [(row.security_id, row.matched_by) for row in former_in_period] == [
        ("sec:AAA", "former_name"),
    ]
    assert former_outside_period[0].status == "unresolved"


@pytest.mark.parametrize("token", ["AI", "ON", "IT"])
def test_ordinary_words_are_not_resolved_as_tickers(token):
    assert resolve_entities(text_item(token), reference()) == ()


def test_unique_canonical_and_former_names_resolve_but_collision_is_ambiguous():
    canonical = resolve_entities(text_item(
        "Alpha Incorporated",
        metadata={"organization_names": ["Alpha Incorporated"]},
    ), reference())
    former = resolve_entities(text_item(
        "Alpha Systems",
        metadata={"organization_names": ["Alpha Systems"]},
    ), reference())
    collision = resolve_entities(text_item(
        "Collision Labs",
        metadata={"organization_names": ["Collision Labs"]},
    ), reference())

    assert canonical[0].security_id == former[0].security_id == "sec:AAA"
    assert canonical[0].matched_by == "canonical_name"
    assert former[0].matched_by == "former_name"
    assert len(collision) == 1
    assert collision[0].status == "ambiguous"
    assert collision[0].security_id is None


def test_share_classes_and_ticker_validity_are_preserved():
    classes = resolve_entities(text_item(
        "Dual Class Holdings",
        metadata={"organization_names": ["Dual Class Holdings"]},
    ), reference())
    old = resolve_entities(text_item("OLD", security_ids=("OLD",), published_at=datetime(2024, 6, 1, tzinfo=timezone.utc)), reference())
    new = resolve_entities(text_item("OLD", security_ids=("OLD",), published_at=NOW), reference())

    assert {row.security_id for row in classes} == {"sec:DUALA", "sec:DUALC"}
    assert {row.ticker for row in classes} == {"DUAL.A", "DUAL.C"}
    assert old[0].ticker == "OLD"
    assert new[0].ticker == "NEW"


def test_private_foreign_and_unresolved_states_are_explicit():
    aliases = (ReviewedAlias(
        alias="Niron Magnetics",
        entity_id="private:niron-magnetics",
        relationship="private_issuer",
        valid_from=date(2020, 1, 1),
        valid_to=None,
    ),)
    private = resolve_entities(text_item(
        "Niron Magnetics",
        metadata={"organization_names": ["Niron Magnetics"]},
    ), reference(), aliases=aliases)
    foreign = resolve_entities(text_item(
        "Foreign Only Plc",
        metadata={"organization_names": ["Foreign Only Plc"]},
    ), reference())
    unresolved = resolve_entities(text_item(
        "Unknown Supplier LLC",
        metadata={"organization_names": ["Unknown Supplier LLC"]},
    ), reference())

    assert private[0].status == "private" and private[0].security_id is None
    assert foreign[0].status == "foreign_only" and foreign[0].security_id == "sec:FOREIGN"
    assert unresolved[0].status == "unresolved" and unresolved[0].security_id is None


def test_reviewed_peer_alias_cannot_turn_a_private_recipient_into_public_security():
    alias = ReviewedAlias(
        alias="Private Recipient",
        entity_id="sec-cik:0000000001",
        relationship="peer",
        valid_from=date(2020, 1, 1),
        valid_to=None,
    )
    result = resolve_entities(text_item(
        "Private Recipient",
        metadata={"organization_names": ["Private Recipient"]},
    ), reference(), aliases=(alias,))

    assert result[0].status == "unresolved"
    assert result[0].security_id is None
