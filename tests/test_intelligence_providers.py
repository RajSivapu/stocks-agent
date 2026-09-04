import json
from datetime import datetime, timezone

import pytest

from lib.intelligence.http import HttpResult, SourceFailure
from lib.intelligence.quota import QuotaSession
from lib.intelligence.providers import CollectionQuery, build_adapter


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


def sample_query(limit=2):
    return CollectionQuery(
        text="nuclear energy",
        symbols=("TEST",),
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=NOW,
        limit=limit,
    )


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
        "datetime": 1788526800,
    }],
    "sec_edgar": {
        "cik": "0000000001",
        "name": "Test Issuer",
        "filings": {"recent": {
            "accessionNumber": ["0000000001-26-000001"],
            "filingDate": ["2026-09-03"],
            "reportDate": ["2026-08-31"],
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
        "effective_on": "2026-10-01",
    }]},
    "fred": {
        "realtime_start": "2026-09-04",
        "observations": [{"date": "2026-08-01", "value": "103.2"}],
    },
}


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
    }

    result = build_adapter(
        adapter_name,
        http,
        quota,
        secret_getter=lambda name: secrets[name],
        clock=lambda: NOW,
    ).collect(sample_query())

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
    assert item.effective_at.isoformat() == "2026-10-01T00:00:00+00:00"


@pytest.mark.parametrize("adapter_name,secret_name", [
    ("alpha_vantage", "alphavantage_api_key"),
    ("finnhub", "finnhub_api_key"),
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
