import gzip
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
from urllib.error import URLError

import pytest

from lib.intelligence.http import (
    BoundedHttpClient,
    CacheStore,
    HttpRequest,
    HttpResult,
    SourceFailure,
    cache_key,
)
from lib.intelligence.cache import ResumableCollectionCache
from lib.intelligence.providers import CollectionResult, RequestReceipt
from lib.intelligence.quota import QuotaExceeded


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, body=b"{}", *, url=None, status=200, headers=None):
        self._body = BytesIO(body)
        self.url = url
        self.status = status
        self.headers = headers or {
            "Content-Type": "application/json",
            "Date": format_datetime(NOW, usegmt=True),
        }

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, timeout, tuple(request.header_items())))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if response.url is None:
            response.url = request.full_url
        return response


class CloseRaisesResponse(FakeResponse):
    def close(self):
        raise RuntimeError("credential=close-detail")


def client(*responses):
    return BoundedHttpClient(
        opener=FakeOpener(*responses),
        allowed_hosts={"api.gdeltproject.org", "data.example.gov"},
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://api.gdeltproject.org/api/v2/doc/doc",
        "https://user:secret@api.gdeltproject.org/api/v2/doc/doc",
        "https://unapproved.example/api",
    ],
)
def test_http_rejects_unsafe_initial_urls(url):
    with pytest.raises(SourceFailure, match="UNSAFE_URL"):
        client().get(HttpRequest(url))


def test_http_revalidates_each_redirect_target():
    transport = client(FakeResponse(status=302, headers={"Location": "http://data.example.gov/feed"}))

    with pytest.raises(SourceFailure, match="UNSAFE_URL"):
        transport.get(HttpRequest("https://api.gdeltproject.org/start"))


def test_http_follows_an_approved_https_redirect():
    opener = FakeOpener(
        FakeResponse(status=302, headers={"Location": "https://data.example.gov/feed"}),
        FakeResponse(body=b'{"ok":true}', url="https://data.example.gov/feed"),
    )
    result = BoundedHttpClient(
        opener=opener,
        allowed_hosts={"api.gdeltproject.org", "data.example.gov"},
        clock=lambda: NOW,
    ).get(HttpRequest("https://api.gdeltproject.org/start", timeout_seconds=3))

    assert result.body == b'{"ok":true}'
    assert result.url == "https://data.example.gov/feed"
    assert result.attempt_count == 2
    assert opener.requests == [
        ("https://api.gdeltproject.org/start", 3, ()),
        ("https://data.example.gov/feed", 3, ()),
    ]


def test_http_admits_each_open_before_it_reaches_transport():
    opener = FakeOpener(
        FakeResponse(status=302, headers={"Location": "https://data.example.gov/feed"}),
        FakeResponse(body=b'{"ok":true}', url="https://data.example.gov/feed"),
    )
    admitted: list[int] = []
    result = BoundedHttpClient(
        opener=opener,
        allowed_hosts={"api.gdeltproject.org", "data.example.gov"},
        clock=lambda: NOW,
    ).get(HttpRequest("https://api.gdeltproject.org/start"), before_attempt=lambda: admitted.append(len(admitted) + 1))

    assert result.attempt_count == 2
    assert admitted == [1, 2]
    assert len(opener.requests) == 2


def test_http_propagates_quota_exhaustion_without_opening_transport():
    opener = FakeOpener(FakeResponse())
    transport = BoundedHttpClient(
        opener=opener, allowed_hosts={"api.gdeltproject.org"}, clock=lambda: NOW,
    )
    with pytest.raises(QuotaExceeded):
        transport.get(HttpRequest("https://api.gdeltproject.org/feed"), before_attempt=lambda: (_ for _ in ()).throw(QuotaExceeded("gdelt")))
    assert opener.requests == []


def test_http_strips_sensitive_headers_on_cross_origin_redirect():
    opener = FakeOpener(
        FakeResponse(status=302, headers={"Location": "https://data.example.gov/feed"}),
        FakeResponse(body=b'{"ok":true}', url="https://data.example.gov/feed"),
    )
    transport = BoundedHttpClient(
        opener=opener,
        allowed_hosts={"api.gdeltproject.org", "data.example.gov"},
        clock=lambda: NOW,
    )

    transport.get(HttpRequest(
        "https://api.gdeltproject.org/start",
        headers={
            "Authorization": "Bearer secret",
            "Proxy-Authorization": "Basic secret",
            "Cookie": "session=secret",
            "X-API-Key": "api-secret",
            "X-Access-Token": "token-secret",
            "Accept-Language": "en-US",
        },
    ))

    first_headers = {key.lower(): value for key, value in opener.requests[0][2]}
    redirected_headers = {key.lower(): value for key, value in opener.requests[1][2]}
    assert first_headers["authorization"] == "Bearer secret"
    assert redirected_headers == {"accept-language": "en-US"}


def test_http_enforces_the_limit_after_gzip_decompression():
    compressed = gzip.compress(b"x" * 1_000_001)

    with pytest.raises(SourceFailure, match="RESPONSE_TOO_LARGE"):
        client(FakeResponse(body=compressed, headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Date": format_datetime(NOW, usegmt=True),
        })).get(HttpRequest("https://api.gdeltproject.org/feed", max_bytes=1_000_000))


@pytest.mark.parametrize(
    "body",
    [
        gzip.compress(b'{"ok":true}')[:-2],
        gzip.compress(b'{"first":true}') + gzip.compress(b'{"second":true}'),
    ],
)
def test_http_rejects_incomplete_or_concatenated_gzip_streams(body):
    response = FakeResponse(body=body, headers={
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "Date": format_datetime(NOW, usegmt=True),
    })

    with pytest.raises(SourceFailure, match="INVALID_RESPONSE"):
        client(response).get(HttpRequest("https://api.gdeltproject.org/feed"))


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/html", b"<html></html>"),
        ("application/json", b"{bad json"),
        ("application/xml", b"<feed>"),
    ],
)
def test_http_rejects_invalid_content_types_and_malformed_documents(content_type, body):
    response = FakeResponse(body=body, headers={
        "Content-Type": content_type,
        "Date": format_datetime(NOW, usegmt=True),
    })

    with pytest.raises(SourceFailure) as failure:
        client(response).get(HttpRequest("https://api.gdeltproject.org/feed"))

    assert failure.value.code in {"INVALID_CONTENT_TYPE", "INVALID_RESPONSE"}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_http_rejects_non_finite_json_constants(constant):
    response = FakeResponse(body=f'{{"value":{constant}}}'.encode())

    with pytest.raises(SourceFailure, match="INVALID_RESPONSE"):
        client(response).get(HttpRequest("https://api.gdeltproject.org/feed"))


def test_http_rejects_a_date_beyond_the_future_tolerance():
    response = FakeResponse(headers={
        "Content-Type": "application/json",
        "Date": format_datetime(NOW + timedelta(minutes=6), usegmt=True),
    })

    with pytest.raises(SourceFailure, match="FUTURE_DATE"):
        client(response).get(HttpRequest("https://api.gdeltproject.org/feed"))


def test_http_normalizes_transport_exceptions_without_disclosing_details():
    secret = "credential=do-not-leak"

    with pytest.raises(SourceFailure, match="SOURCE_UNAVAILABLE") as failure:
        client(URLError(secret)).get(HttpRequest("https://api.gdeltproject.org/feed"))

    assert secret not in str(failure.value)


def test_http_normalizes_timeouts():
    with pytest.raises(SourceFailure, match="TIMEOUT"):
        client(TimeoutError("private upstream detail")).get(
            HttpRequest("https://api.gdeltproject.org/feed")
        )


def test_http_normalizes_response_cleanup_exceptions():
    with pytest.raises(SourceFailure) as failure:
        client(CloseRaisesResponse()).get(HttpRequest("https://api.gdeltproject.org/feed"))

    assert "close-detail" not in str(failure.value)


def test_cache_key_is_deterministic_for_query_order():
    first = cache_key("gdelt", {"query": "energy", "limit": "10"}, "24h", 1)
    second = cache_key("gdelt", {"limit": "10", "query": "energy"}, "24h", 1)

    assert first == second
    assert first == "3053ab96d57395314be058ccce33f9c69290ae24d13f2cdb723bc4e85bcaa29d"


def test_cache_accepts_only_validated_results_and_preserves_original_timestamps():
    retrieved_at = NOW - timedelta(hours=1)
    observed_at = NOW - timedelta(hours=2)
    manual = HttpResult(
        url="https://api.gdeltproject.org/feed",
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps({"ok": True}).encode(),
        retrieved_at=retrieved_at,
        observed_at=observed_at,
    )
    store = CacheStore()
    with pytest.raises(ValueError, match="validated"):
        store.put("manual", manual)

    original = client(FakeResponse(body=b'{"ok":true}', headers={
        "Content-Type": "application/json",
        "Date": format_datetime(observed_at, usegmt=True),
    })).get(HttpRequest("https://api.gdeltproject.org/feed"))
    store.put("key", original)

    hit = store.get("key")

    assert hit is not original
    assert hit.cache_hit is True
    assert hit.retrieved_at == NOW
    assert hit.observed_at == observed_at


def test_cache_loads_validated_gateway_entries_without_mutating_them():
    gateway_entry = {
        "provider": "gdelt",
        "cache_key": "key",
        "retrieved_at": "2026-09-04T11:00:00+00:00",
        "published_at": "2026-09-04T10:00:00+00:00",
        "effective_at": None,
        "normalized_text": "validated source item",
        "content_hash": "a" * 64,
    }
    store = CacheStore.from_gateway_entries([gateway_entry])

    hit = store.get("key")

    assert gateway_entry.get("cache_hit") is None
    assert hit["cache_hit"] is True
    assert hit["retrieved_at"] == gateway_entry["retrieved_at"]
    assert hit["published_at"] == gateway_entry["published_at"]


def test_gateway_cache_rejects_missing_original_timestamp_metadata():
    entry = {
        "provider": "gdelt",
        "cache_key": "key",
        "published_at": "2026-09-04T10:00:00+00:00",
        "effective_at": None,
        "normalized_text": "validated source item",
        "content_hash": "a" * 64,
    }

    with pytest.raises(ValueError, match="validated"):
        CacheStore.from_gateway_entries([entry])


def test_resumable_cache_key_includes_provider_query_window_and_schema_receipt():
    cache = ResumableCollectionCache()
    receipt = {"provider": "gdelt", "request_cost": 1, "receipt_id": "r1"}
    first = cache.key("gdelt", {"query": "energy"}, "2026-09-04T00:00Z/2026-09-04T12:00Z", 1)
    changed_schema = cache.key("gdelt", {"query": "energy"}, "2026-09-04T00:00Z/2026-09-04T12:00Z", 2)

    cache.put(first, {"items": ["evidence"], "receipt": receipt})

    hit = cache.get(first)

    assert first != changed_schema
    assert hit is not None
    assert hit["receipt"] == receipt
    assert hit["cache_hit"] is True


def test_failed_checkpoint_hydration_keeps_the_original_paid_receipt():
    cache = ResumableCollectionCache()
    original = RequestReceipt(
        provider="gdelt", reservation_id="original-reservation", status="failed", cache_key="key",
        requested_window={"start": NOW.isoformat(), "end": NOW.isoformat()}, requested_limit=1,
        retrieved_at=NOW, observed_at=None, expires_at=None, request_cost=1, upstream_remaining=None,
        returned_count=0, accepted_count=0, duplicate_count=0, dropped_count=0, response_hash=None,
        error_code="SOURCE_UNAVAILABLE", source_receipt_id="original-receipt",
    )
    cache.put_collection("key", CollectionResult((), original, 1))

    resumed = cache.get_collection("key", reservation_id="new-reservation", source_receipt_id="new-receipt", now=NOW)

    assert resumed is not None
    assert resumed.receipt.status == "failed"
    assert resumed.receipt.source_receipt_id == "original-receipt"
    assert resumed.receipt.request_cost == 1
