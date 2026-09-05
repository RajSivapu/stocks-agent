import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from lib.intelligence.http import HttpResult, SourceFailure
from lib.intelligence.quota import QuotaSession
from lib.intelligence.providers import CollectionQuery, build_adapter
from lib import config


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


class FixtureHttp:
    def __init__(self, payload, *, url="https://fixture.invalid/feed", cache_hit=False):
        self.payload = payload
        self.url = url
        self.cache_hit = cache_hit
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return HttpResult(
            url=self.url,
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(self.payload).encode(),
            retrieved_at=NOW,
            observed_at=NOW,
            cache_hit=self.cache_hit,
        )


def sample_query(limit=2, **overrides):
    values = dict(
        text="nuclear energy",
        symbols=("TEST",),
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=NOW,
        limit=limit,
    )
    values.update(overrides)
    return CollectionQuery(**values)


def test_zero_capacity_collection_returns_quota_blocked_without_open():
    http = FixtureHttp({"articles": []})
    result = build_adapter("gdelt", http, QuotaSession({"gdelt": ()}), clock=lambda: NOW).collect(sample_query())
    assert result.receipt.status == "quota_blocked"
    assert result.receipt.error_code == "QUOTA_BLOCKED"
    assert result.receipt.request_cost == 0
    assert http.requests == []


def test_secondary_adapters_normalize_independent_claims_and_syndication():
    from lib.intelligence.pipeline import _discover
    from lib.intelligence.normalize import normalize_item
    from decimal import Decimal

    alpha_payload = {"feed": [{
        "title": "TEST raises full-year revenue guidance", "summary": "Management lifts sales outlook for 2026.",
        "time_published": "20260904T110000", "url": "https://reuters.com/test-guidance", "source": "Reuters",
        "ticker_sentiment": [{"ticker": "TEST", "relevance_score": "1"}],
    }]}
    finnhub_payload = [{"id": 19, "headline": "TEST boosts 2026 sales forecast",
        "summary": "The company increases its annual revenue outlook.", "datetime": 1788519600,
        "url": "https://bloomberg.com/test-sales", "source": "Bloomberg", "related": "TEST", "category": "company"}]
    def collect(provider, payload):
        return normalize_item(build_adapter(provider, FixtureHttp(payload),
            QuotaSession({provider: ("reservation",)}), secret_getter=lambda _: "fixture", clock=lambda: NOW,
        ).collect(sample_query()).items[0])
    alpha = collect("alpha_vantage", alpha_payload)
    finn = collect("finnhub", finnhub_payload)
    assert alpha.metadata["claim_key"] == finn.metadata["claim_key"]
    assert alpha.metadata["polarity"] == finn.metadata["polarity"] == "positive"
    context = {"holdings": {"TEST": "0.1"}, "liquidity_by_ticker": {"TEST": "1"}, "overlap_by_ticker": {"TEST": "0.1"}}
    events, _, ranked = _discover([alpha, finn], context, NOW)
    assert len(events) == 1
    assert ranked[0].components["authority_corroboration"] == Decimal("0.75")
    syndicated = dict(finnhub_payload[0], source="Reuters", url="https://reuters.com/test-guidance?utm_source=finnhub")
    _, _, ranked = _discover([alpha, collect("finnhub", [syndicated])], context, NOW)
    assert "authority_corroboration:missing" in ranked[0].missing_reasons
    opposite = dict(finnhub_payload[0], headline="TEST cuts 2026 sales forecast", summary="Management lowers annual revenue guidance.")
    events, _, ranked = _discover([alpha, collect("finnhub", [opposite])], context, NOW)
    assert len(events) == 2
    assert all(not item.qualified and "CONFLICTING_CLAIM_POLARITY" in item.veto_reasons for item in ranked)


FIXTURES = {
    "gdelt": {
        "articles": [{
            "url": "https://api.gdeltproject.org/doc/1",
            "title": "Grid investment",
            "seendate": "20260904T100000Z",
        }],
    },
    "alpha_vantage": {
        "feed": [{
            "title": "Energy earnings",
            "url": "https://www.alphavantage.co/news/1",
            "summary": "A bounded summary",
            "time_published": "20260904T093000",
        }],
    },
    "finnhub": [{
        "id": 7,
        "headline": "Company update",
        "summary": "A company update",
        "url": "https://finnhub.io/news/7",
        "datetime": 1788516000,
    }],
    "sec_edgar": {
        "cik": "0000000001",
        "name": "Test Issuer",
        "filings": {"recent": {
            "accessionNumber": ["0000000001-26-000001"],
            "filingDate": ["2026-09-03"],
            "reportDate": ["2026-09-01"],
            "form": ["8-K"],
            "primaryDocument": ["test-8k.htm"],
        }},
    },
    "federal_register": {"results": [{
        "document_number": "2026-12345",
        "title": "Grid rule",
        "abstract": "Rule summary",
        "html_url": "https://www.federalregister.gov/documents/2026/09/04/2026-12345/grid-rule",
        "publication_date": "2026-09-04",
        "effective_on": "2026-09-04T12:00:00Z",
    }]},
    "fred": {
        "realtime_start": "2026-09-04",
        "observations": [{"date": "2026-09-01", "value": "103.2"}],
    },
}


@pytest.mark.parametrize("adapter_name,payload,query_overrides,expect_item", [
    ("gdelt", FIXTURES["gdelt"], {}, True),
    ("alpha_vantage", FIXTURES["alpha_vantage"], {}, True),
    ("finnhub", FIXTURES["finnhub"], {}, True),
    ("yahoo", {"chart": {"result": [{"meta": {
        "symbol": "TEST", "regularMarketPrice": 101, "previousClose": 100,
        "regularMarketTime": 1788516000, "marketState": "REGULAR",
    }, "timestamp": [1788516000], "indicators": {"quote": [{"close": [101]}]}}]}}, {}, True),
    ("sec_edgar", FIXTURES["sec_edgar"], {"cik": "0000000001"}, True),
    ("federal_register", FIXTURES["federal_register"], {}, True),
    ("fred", FIXTURES["fred"], {"series_id": "CPIAUCSL"}, True),
    ("white_house", {}, {}, False),
    ("doe", {}, {}, False),
    ("dod", {}, {}, False),
    ("eia", {}, {"series_id": "PET.WCESTUS1.W"}, False),
    ("bls", {}, {"series_id": "CUUR0000SA0"}, False),
    ("bea", {}, {"series_id": "T10105"}, False),
])
def test_each_declared_provider_yields_discoverable_evidence_or_pre_http_unsupported_failure(
    adapter_name, payload, query_overrides, expect_item,
):
    assert adapter_name in config.load_settings()["intelligence"]["providers"]
    http = FixtureHttp(payload)
    quota = QuotaSession({adapter_name: ({"reservation_id": "declared", "reserved_requests": 1},)})
    secrets = {
        "alphavantage_api_key": "existing-free-alpha-key",
        "finnhub_api_key": "existing-free-finnhub-key",
        "fred_api_key": "existing-free-fred-key",
    }
    adapter = build_adapter(
        adapter_name, http, quota, secret_getter=lambda name: secrets[name], clock=lambda: NOW,
    )

    if not expect_item:
        with pytest.raises(SourceFailure, match="UNSUPPORTED_QUERY"):
            adapter.collect(sample_query(**query_overrides))
        assert http.requests == []
        assert quota.consume_next(adapter_name) == "declared"
        return

    result = adapter.collect(sample_query(**query_overrides))
    assert len(result.items) == 1
    item = result.items[0]
    assert item.upstream_item_id
    assert item.request_url and item.source_url and item.request_url != item.source_url
    assert item.published_at and item.retrieved_at
    assert item.security_ids or item.entity_ids


@pytest.mark.parametrize("adapter_name", tuple(FIXTURES))
def test_adapter_returns_bounded_items_and_one_request_receipt(adapter_name):
    http = FixtureHttp(FIXTURES[adapter_name])
    quota = QuotaSession({adapter_name: ({
        "reservation_id": f"{adapter_name}-reservation",
        "reserved_requests": 1,
    },)})
    secrets = {
        "alphavantage_api_key": "existing-free-alpha-key",
        "finnhub_api_key": "existing-free-finnhub-key",
        "fred_api_key": "existing-free-fred-key",
    }

    query = sample_query(**(
        {"cik": "0000000001"} if adapter_name == "sec_edgar"
        else {"series_id": "TEST"} if adapter_name == "fred"
        else {}
    ))
    result = build_adapter(
        adapter_name,
        http,
        quota,
        secret_getter=lambda name: secrets[name],
        clock=lambda: NOW,
    ).collect(query)

    assert len(http.requests) == 1
    assert 0 <= len(result.items) <= result.requested_limit
    assert result.receipt.provider == adapter_name
    assert result.receipt.reservation_id == f"{adapter_name}-reservation"
    assert len(result.receipt.cache_key) == 64
    assert result.receipt.requested_window == {
        "start": "2026-09-01T00:00:00+00:00",
        "end": "2026-09-04T12:00:00+00:00",
    }
    assert result.receipt.returned_count == len(result.items)
    assert result.receipt.duplicate_count == 0
    assert result.receipt.upstream_remaining is None
    assert result.receipt.retrieved_at == NOW
    assert all(item.source_url.startswith("https://") for item in result.items)


def test_provider_bounds_drop_extra_and_wrong_host_items():
    payload = {"articles": [
        {"url": "https://api.gdeltproject.org/doc/1", "title": "One", "seendate": "20260904T100000Z"},
        {"url": "https://evil.example/doc/2", "title": "Wrong host", "seendate": "20260904T100000Z"},
        {"url": "https://api.gdeltproject.org/doc/3", "title": "Three", "seendate": "20260904T100000Z"},
    ]}
    result = build_adapter(
        "gdelt",
        FixtureHttp(payload),
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query(limit=1))

    assert [item.title for item in result.items] == ["One"]
    assert result.receipt.returned_count == 3
    assert result.receipt.accepted_count == 1
    assert result.receipt.dropped_count == 2


def test_official_release_and_effective_timestamps_remain_distinct():
    result = build_adapter(
        "federal_register",
        FixtureHttp(FIXTURES["federal_register"]),
        QuotaSession({"federal_register": ({"reservation_id": "fr1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    item = result.items[0]
    assert item.published_at.isoformat() == "2026-09-04T00:00:00+00:00"
    assert item.effective_at.isoformat() == "2026-09-04T12:00:00+00:00"


def test_federal_register_uses_documented_conditions_and_per_page_shape():
    http = FixtureHttp(FIXTURES["federal_register"])
    build_adapter(
        "federal_register", http,
        QuotaSession({"federal_register": ({"reservation_id": "fr-shape", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query(symbols=("CENX",)))

    params = parse_qs(urlsplit(http.requests[0].url).query)
    assert "conditions[term]" in params and "CENX" in params["conditions[term]"][0]
    assert "conditions[publication_date][gte]" in params
    assert "conditions[publication_date][lte]" in params
    assert params["per_page"] == ["2"]
    assert "query" not in params and "limit" not in params


def test_newly_published_prior_period_filing_is_retained_with_distinct_times():
    payload = {"cik": "0000000001", "name": "Test Issuer", "filings": {"recent": {
        "accessionNumber": ["0000000001-26-000002"],
        "filingDate": ["2026-09-04"],
        "reportDate": ["2025-12-31"],
        "form": ["10-K"],
        "primaryDocument": ["annual.htm"],
    }}}
    result = build_adapter(
        "sec_edgar", FixtureHttp(payload),
        QuotaSession({"sec_edgar": ({"reservation_id": "s2", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query(cik="0000000001", symbols=("TEST",)))

    assert len(result.items) == 1
    item = result.items[0]
    assert item.published_at.isoformat() == "2026-09-04T00:00:00+00:00"
    assert item.reporting_at.isoformat() == "2025-12-31T00:00:00+00:00"
    assert item.security_ids == ("TEST",)
    assert item.entity_ids == ("cik:0000000001",)


def test_provider_keeps_secret_free_request_url_separate_from_item_url_and_identity():
    result = build_adapter(
        "alpha_vantage", FixtureHttp({"feed": [{
            "title": "Test issuer contract update",
            "url": "https://publisher.example/stories/contract-update",
            "summary": "A contract update for the issuer.",
            "time_published": "20260904T093000",
            "ticker_sentiment": [{"ticker": "TEST"}],
        }]}),
        QuotaSession({"alpha_vantage": ({"reservation_id": "a1", "reserved_requests": 1},)}),
        secret_getter=lambda _name: "existing-free-alpha-key", clock=lambda: NOW,
    ).collect(sample_query())

    item = result.items[0]
    assert item.source_url == "https://publisher.example/stories/contract-update"
    assert item.request_url.startswith("https://www.alphavantage.co/query?")
    assert "existing-free-alpha-key" not in item.request_url
    assert item.security_ids == ("TEST",)
    assert item.upstream_item_id == "https://publisher.example/stories/contract-update"


@pytest.mark.parametrize("adapter_name,secret_name", [
    ("alpha_vantage", "alphavantage_api_key"),
    ("finnhub", "finnhub_api_key"),
    ("fred", "fred_api_key"),
])
def test_missing_free_key_fails_before_quota_or_http(adapter_name, secret_name):
    http = FixtureHttp({})
    quota = QuotaSession({adapter_name: ({"reservation_id": "reserved", "reserved_requests": 1},)})
    adapter = build_adapter(
        adapter_name,
        http,
        quota,
        secret_getter=lambda name: (_ for _ in ()).throw(KeyError(name)),
        clock=lambda: NOW,
    )

    with pytest.raises(SourceFailure) as failure:
        adapter.collect(sample_query())

    assert failure.value.code == "CONFIGURATION_MISSING"
    assert http.requests == []
    assert quota.consume_next(adapter_name) == "reserved"


def test_social_result_is_hypothesis_only():
    payload = {"data": {"children": [{"data": {
        "id": "abc",
        "title": "Possible supplier constraint",
        "selftext": "Needs corroboration",
        "permalink": "/r/stocks/comments/abc/example/",
        "created_utc": 1788523200,
    }}]}}
    result = build_adapter(
        "social",
        FixtureHttp(payload),
        QuotaSession({"social": ({"reservation_id": "s1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.items
    assert all(item.authority == "hypothesis" for item in result.items)


def test_cache_hit_receipt_keeps_original_transport_timestamps():
    original_retrieval = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    http = FixtureHttp(FIXTURES["gdelt"], cache_hit=True)
    http.get = lambda request: HttpResult(
        url="https://api.gdeltproject.org/feed",
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(FIXTURES["gdelt"]).encode(),
        retrieved_at=original_retrieval,
        observed_at=datetime(2026, 9, 4, 9, 59, tzinfo=timezone.utc),
        cache_hit=True,
    )
    result = build_adapter(
        "gdelt",
        http,
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.receipt.status == "cache_hit"
    assert result.receipt.retrieved_at == original_retrieval
    assert result.receipt.observed_at == datetime(2026, 9, 4, 9, 59, tzinfo=timezone.utc)


def test_yahoo_adapter_preserves_exchange_timestamp_and_market_state():
    payload = {"chart": {"result": [{
        "meta": {
            "symbol": "TEST",
            "regularMarketPrice": 101.0,
            "previousClose": 100.0,
            "regularMarketTime": 1788296400,
            "marketState": "REGULAR",
        },
        "timestamp": [1788210000],
        "indicators": {"quote": [{"close": [100.0]}]},
    }]}}
    result = build_adapter(
        "yahoo",
        FixtureHttp(payload, url="https://query1.finance.yahoo.com/v8/finance/chart/TEST"),
        QuotaSession({"yahoo": ({"reservation_id": "y1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    item = result.items[0]
    assert item.published_at.isoformat() == "2026-09-01T21:00:00+00:00"
    assert item.metadata["market_state"] == "REGULAR"
    assert item.metadata["source"] == "yahoo-chart"


def test_malformed_response_returns_a_bounded_failed_receipt():
    http = FixtureHttp({})
    http.get = lambda request: HttpResult(
        url="https://api.gdeltproject.org/feed",
        status=200,
        headers={"content-type": "application/json"},
        body=b"not-json",
        retrieved_at=NOW,
        observed_at=None,
    )
    result = build_adapter(
        "gdelt",
        http,
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "INVALID_RESPONSE"
    assert result.receipt.reservation_id == "g1"


def test_transport_failure_keeps_bounded_error_code_and_request_receipt():
    http = FixtureHttp({})
    http.get = lambda request: (_ for _ in ()).throw(SourceFailure("TIMEOUT"))
    result = build_adapter(
        "gdelt",
        http,
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "TIMEOUT"
    assert result.receipt.reservation_id == "g1"


def test_item_without_a_parseable_provider_timestamp_is_dropped():
    payload = {"articles": [{
        "url": "https://api.gdeltproject.org/doc/1",
        "title": "Undated item",
        "seendate": "not-a-date",
    }]}
    result = build_adapter(
        "gdelt",
        FixtureHttp(payload),
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.items == ()
    assert result.receipt.returned_count == 1
    assert result.receipt.dropped_count == 1


@pytest.mark.parametrize("adapter_name,payload,secret_name,expected_host,reference_key", [
    ("gdelt", {"articles": [{
        "url": "https://publisher.example/gdelt-story",
        "title": "Publisher story",
        "seendate": "20260904T100000Z",
    }]}, None, "api.gdeltproject.org", "query"),
    ("alpha_vantage", {"feed": [{
        "url": "https://publisher.example/alpha-story",
        "title": "Publisher story",
        "summary": "Summary",
        "time_published": "20260904T100000",
    }]}, "alphavantage_api_key", "www.alphavantage.co", "tickers"),
    ("finnhub", [{
        "id": 8,
        "url": "https://publisher.example/finnhub-story",
        "headline": "Publisher story",
        "summary": "Summary",
        "datetime": 1788516000,
    }], "finnhub_api_key", "finnhub.io", "symbol"),
])
def test_external_publisher_links_use_secret_free_provider_evidence_url(
    adapter_name, payload, secret_name, expected_host, reference_key,
):
    secret = "private-existing-key"
    result = build_adapter(
        adapter_name,
        FixtureHttp(payload),
        QuotaSession({adapter_name: ({"reservation_id": "r1", "reserved_requests": 1},)}),
        secret_getter=lambda name: secret if name == secret_name else (_ for _ in ()).throw(KeyError(name)),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert len(result.items) == 1
    item = result.items[0]
    assert urlsplit(item.source_url).hostname == "publisher.example"
    assert urlsplit(item.request_url).hostname == expected_host
    assert reference_key in parse_qs(urlsplit(item.request_url).query)
    assert secret not in item.request_url
    assert item.metadata["publisher_url"].startswith("https://publisher.example/")
    assert item.metadata["publisher_url_authority"] == "untrusted_reference"


@pytest.mark.parametrize("timestamp,accepted", [
    ("20260831T235959Z", False),
    ("20260901T000000Z", True),
    ("20260904T120000Z", True),
    ("20260904T120001Z", False),
])
def test_collection_window_is_inclusive_and_rejects_stale_or_future_items(timestamp, accepted):
    payload = {"articles": [{
        "url": "https://publisher.example/story",
        "title": "Windowed story",
        "seendate": timestamp,
    }]}
    result = build_adapter(
        "gdelt",
        FixtureHttp(payload),
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert (len(result.items) == 1) is accepted
    assert result.receipt.dropped_count == (0 if accepted else 1)


def test_future_effective_timestamp_does_not_drop_newly_published_fact():
    payload = {"results": [{
        "document_number": "2026-99999",
        "title": "Future rule",
        "abstract": "Published now, effective later",
        "html_url": "https://www.federalregister.gov/documents/2026/09/04/future-rule",
        "publication_date": "2026-09-04",
        "effective_on": "2026-09-04T12:00:01Z",
    }]}
    result = build_adapter(
        "federal_register",
        FixtureHttp(payload),
        QuotaSession({"federal_register": ({"reservation_id": "fr1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert len(result.items) == 1
    assert result.items[0].effective_at.isoformat() == "2026-09-04T12:00:01+00:00"
    assert result.receipt.dropped_count == 0


def test_cached_schema_failure_uses_original_timestamps_and_zero_request_cost():
    retrieved_at = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    observed_at = datetime(2026, 9, 4, 8, 59, tzinfo=timezone.utc)
    http = FixtureHttp({})
    http.get = lambda request: HttpResult(
        url="https://api.gdeltproject.org/feed",
        status=200,
        headers={"content-type": "application/json"},
        body=b"not-json",
        retrieved_at=retrieved_at,
        observed_at=observed_at,
        cache_hit=True,
    )
    result = build_adapter(
        "gdelt",
        http,
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.receipt.status == "failed"
    assert result.receipt.retrieved_at == retrieved_at
    assert result.receipt.observed_at == observed_at
    assert result.receipt.request_cost == 0
    assert result.receipt.expires_at is None


def test_fred_uses_explicit_canonical_series_id_for_request_and_label():
    payload = {"realtime_start": "2026-09-04", "observations": [{
        "date": "2026-09-01", "value": "103.2",
    }]}
    http = FixtureHttp(payload)
    result = build_adapter(
        "fred",
        http,
        QuotaSession({"fred": ({"reservation_id": "f1", "reserved_requests": 1},)}),
        secret_getter=lambda name: "existing-free-fred-key",
        clock=lambda: NOW,
    ).collect(sample_query(series_id="CPIAUCSL", symbols=("WRONG",)))

    request_query = parse_qs(urlsplit(http.requests[0].url).query)
    assert request_query["series_id"] == ["CPIAUCSL"]
    assert result.items[0].metadata["series_id"] == "CPIAUCSL"
    assert "CPIAUCSL" in result.items[0].title
    assert "existing-free-fred-key" not in result.items[0].source_url


@pytest.mark.parametrize("cik", [None, "", "TEST", "123TEST", "0", "12345678901"])
def test_sec_rejects_invalid_cik_before_quota_or_http(cik):
    http = FixtureHttp(FIXTURES["sec_edgar"])
    quota = QuotaSession({"sec_edgar": ({"reservation_id": "s1", "reserved_requests": 1},)})
    adapter = build_adapter("sec_edgar", http, quota, clock=lambda: NOW)

    with pytest.raises(SourceFailure) as failure:
        adapter.collect(sample_query(cik=cik))

    assert failure.value.code == "INVALID_QUERY"
    assert http.requests == []
    assert quota.consume_next("sec_edgar") == "s1"


def test_more_than_gateway_count_limit_returns_bounded_failure_receipt():
    payload = {"articles": [{
        "url": "https://publisher.example/story",
        "title": "Story",
        "seendate": "20260904T100000Z",
    }] * 10_001}
    result = build_adapter(
        "gdelt",
        FixtureHttp(payload),
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "INVALID_RESPONSE"
    assert result.receipt.returned_count == 0
    assert result.receipt.dropped_count == 0


def test_oversized_nested_metadata_is_deterministically_bounded():
    payload = {"articles": [{
        "url": "https://publisher.example/story",
        "title": "Story",
        "seendate": "20260904T100000Z",
        "domain": "x" * 9_000,
    }]}
    result = build_adapter(
        "gdelt",
        FixtureHttp(payload),
        QuotaSession({"gdelt": ({"reservation_id": "g1", "reserved_requests": 1},)}),
        clock=lambda: NOW,
    ).collect(sample_query())

    assert len(result.items) == 1
    encoded = json.dumps(dict(result.items[0].metadata), separators=(",", ":"))
    assert len(encoded.encode()) <= 8_192
    assert len(result.items[0].metadata["domain"]) <= 500
