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
        self.requests.append((request.full_url, timeout))
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
    assert opener.requests == [
        ("https://api.gdeltproject.org/start", 3),
        ("https://data.example.gov/feed", 3),
    ]


def test_http_enforces_the_limit_after_gzip_decompression():
    compressed = gzip.compress(b"x" * 1_000_001)

    with pytest.raises(SourceFailure, match="RESPONSE_TOO_LARGE"):
        client(FakeResponse(body=compressed, headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Date": format_datetime(NOW, usegmt=True),
        })).get(HttpRequest("https://api.gdeltproject.org/feed", max_bytes=1_000_000))


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
    original = HttpResult(
        url="https://api.gdeltproject.org/feed",
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps({"ok": True}).encode(),
        retrieved_at=retrieved_at,
        observed_at=observed_at,
        validated=True,
    )
    store = CacheStore()
    store.put("key", original)

    hit = store.get("key")

    assert hit is not original
    assert hit.cache_hit is True
    assert hit.retrieved_at == retrieved_at
    assert hit.observed_at == observed_at
    with pytest.raises(ValueError, match="validated"):
        store.put("bad", HttpResult(
            url=original.url,
            status=200,
            headers=original.headers,
            body=original.body,
            retrieved_at=retrieved_at,
            observed_at=observed_at,
            validated=False,
        ))


def test_cache_loads_validated_gateway_entries_without_mutating_them():
    gateway_entry = {
        "provider": "gdelt",
        "cache_key": "key",
        "retrieved_at": "2026-09-04T11:00:00+00:00",
        "published_at": "2026-09-04T10:00:00+00:00",
        "normalized_text": "validated source item",
        "content_hash": "a" * 64,
    }
    store = CacheStore.from_gateway_entries([gateway_entry])

    hit = store.get("key")

    assert gateway_entry.get("cache_hit") is None
    assert hit["cache_hit"] is True
    assert hit["retrieved_at"] == gateway_entry["retrieved_at"]
    assert hit["published_at"] == gateway_entry["published_at"]
