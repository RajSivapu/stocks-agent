from dataclasses import replace

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
        "duplicate",
        "near_duplicate",
    ]
    assert result[1].reason == "same_canonical_url"
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
