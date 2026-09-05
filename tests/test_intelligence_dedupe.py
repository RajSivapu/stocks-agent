from dataclasses import replace
from datetime import datetime, timezone

from lib.intelligence.dedupe import deduplicate
from lib.intelligence.normalize import normalize_item
from tests.test_intelligence_normalize import raw_item


def test_exact_and_near_duplicates_keep_reasons():
    first = normalize_item(raw_item())
    same_url = normalize_item(
        raw_item(
            upstream_item_id="story-2",
            title="Different headline",
            normalized_text="Different content",
        )
    )
    syndicated = normalize_item(
        raw_item(
            provider="finnhub",
            upstream_item_id="copy-1",
            source_url="https://finnhub.io/api/v1/company-news?symbol=CENX",
            normalized_text="A bounded market update!",
        )
    )

    result = deduplicate([first, same_url, syndicated])

    assert [row.disposition for row in result] == [
        "accepted",
        "accepted",
        "near_duplicate",
    ]
    assert result[1].reason is None
    assert result[2].reason == "similar_normalized_content"


def test_exact_content_hash_is_a_distinct_reason():
    first = normalize_item(raw_item(source_url="https://api.gdeltproject.org/one"))
    copied = replace(
        first,
        canonical_url="https://api.gdeltproject.org/two",
        upstream_item_id="story-2",
    )

    dispositions = deduplicate([first, copied])

    assert dispositions[1].disposition == "duplicate"
    assert dispositions[1].reason == "same_content_hash"


def test_affirmation_and_negated_correction_are_not_near_duplicates():
    affirmed = normalize_item(raw_item(
        upstream_item_id="affirmed-contract",
        title="Issuer confirms grid contract",
        normalized_text="Issuer confirms the grid contract will proceed.",
    ))
    denied = normalize_item(raw_item(
        upstream_item_id="denied-contract",
        title="Issuer denies grid contract",
        normalized_text="Issuer denies the grid contract will proceed.",
    ))

    dispositions = deduplicate([affirmed, denied])

    assert [row.disposition for row in dispositions] == ["accepted", "accepted"]
    assert [row.item.claim_polarity for row in dispositions] == ["affirmed", "denied"]


def test_same_request_url_does_not_collapse_distinct_items():
    first = normalize_item(raw_item(
        upstream_item_id="one",
        source_url="https://publisher.example/items/one",
        request_url="https://api.gdeltproject.org/api/v2/doc/doc?query=grid",
    ))
    second = normalize_item(raw_item(
        upstream_item_id="two",
        source_url="https://publisher.example/items/two",
        request_url="https://api.gdeltproject.org/api/v2/doc/doc?query=grid",
        title="A distinct second item",
        normalized_text="Different factual content.",
    ))

    dispositions = deduplicate([first, second])

    assert [row.disposition for row in dispositions] == ["accepted", "accepted"]


def test_identical_text_from_distinct_upstreams_has_distinct_persistable_identity():
    first = normalize_item(raw_item(
        upstream_item_id="provider-a-1", source_url="https://publisher-a.example/item",
    ))
    second = normalize_item(raw_item(
        upstream_item_id="provider-b-1", source_url="https://publisher-b.example/item",
    ))

    assert first.content_hash != second.content_hash


def test_near_duplicate_is_retained_as_corroborating_evidence():
    first = normalize_item(raw_item(upstream_item_id="one", normalized_text="Issuer announces a grid contract."))
    corroborating = normalize_item(raw_item(
        provider="finnhub", upstream_item_id="two", source_url="https://publisher.example/two",
        normalized_text="Issuer announces a grid contract!",
    ))

    assert [row.disposition for row in deduplicate([first, corroborating])] == [
        "accepted", "near_duplicate",
    ]
