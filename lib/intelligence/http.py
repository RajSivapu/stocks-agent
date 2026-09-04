"""Bounded, validated HTTP transport and in-run cache primitives."""

from __future__ import annotations

import hashlib
import json
import socket
import zlib
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_READ_CHUNK_BYTES = 64 * 1024
_HTTP_VALIDATION_PROVENANCE = object()
_SENSITIVE_REDIRECT_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
})


class SourceFailure(RuntimeError):
    """A bounded source error whose message never contains upstream details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    url: str
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = 10.0
    max_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.max_bytes <= 0:
            raise ValueError("HTTP bounds must be positive")


@dataclass(frozen=True, slots=True)
class HttpResult:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    retrieved_at: datetime
    observed_at: datetime | None
    cache_hit: bool = False
    _validation_provenance: object | None = field(default=None, repr=False, compare=False)

    @property
    def validated(self) -> bool:
        return self._validation_provenance is _HTTP_VALIDATION_PROVENANCE


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cache_key(
    provider: str,
    query: Mapping[str, str],
    window: str,
    schema_version: int,
) -> str:
    payload = json.dumps(
        [provider, sorted(query.items()), window, schema_version],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class CacheStore:
    """An in-run cache that accepts validated transport or gateway results only."""

    def __init__(self) -> None:
        self._entries: dict[str, HttpResult | Mapping[str, Any]] = {}

    @classmethod
    def from_gateway_entries(cls, entries: Iterable[Mapping[str, Any]]) -> CacheStore:
        store = cls()
        for entry in entries:
            store._put_gateway_entry(entry)
        return store

    def _put_gateway_entry(self, entry: Mapping[str, Any]) -> None:
        key = entry.get("cache_key")
        content_hash = entry.get("content_hash")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(entry.get("provider"), str)
            or not isinstance(entry.get("normalized_text"), str)
            or not isinstance(entry.get("retrieved_at"), str)
            or "published_at" not in entry
            or "effective_at" not in entry
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise ValueError("gateway cache entry is not validated")
        self._entries[key] = MappingProxyType(deepcopy(dict(entry)))

    def put(self, key: str, result: HttpResult) -> None:
        if not key:
            raise ValueError("cache key must not be empty")
        if not isinstance(result, HttpResult) or not result.validated:
            raise ValueError("cache accepts validated results only")
        self._entries[key] = result

    def get(self, key: str) -> HttpResult | Mapping[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if isinstance(entry, HttpResult):
            return replace(entry, cache_hit=True)
        hit = deepcopy(dict(entry))
        hit["cache_hit"] = True
        return MappingProxyType(hit)


class BoundedHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        opener=None,
        clock: Callable[[], datetime] | None = None,
        future_tolerance: timedelta = timedelta(minutes=5),
    ) -> None:
        hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        if not hosts:
            raise ValueError("allowed_hosts must not be empty")
        self._allowed_hosts = hosts
        self._opener = opener or build_opener(_NoRedirect())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._future_tolerance = future_tolerance

    def get(self, request: HttpRequest) -> HttpResult:
        current_url = request.url
        current_headers = dict(request.headers or {})
        for redirect_count in range(_MAX_REDIRECTS + 1):
            self._validate_url(current_url)
            try:
                response = self._open(
                    current_url,
                    headers=current_headers,
                    timeout_seconds=request.timeout_seconds,
                )
            except HTTPError as exc:
                if exc.code in _REDIRECT_STATUSES:
                    response = exc
                else:
                    raise SourceFailure("SOURCE_UNAVAILABLE") from None
            except (TimeoutError, socket.timeout):
                raise SourceFailure("TIMEOUT") from None
            except URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise SourceFailure("TIMEOUT") from None
                raise SourceFailure("SOURCE_UNAVAILABLE") from None
            except SourceFailure:
                raise
            except Exception:
                raise SourceFailure("SOURCE_UNAVAILABLE") from None

            cleanup_failed = False
            try:
                status = int(getattr(response, "status", getattr(response, "code", 200)))
                headers = self._headers(response)
                response_url = response.geturl() or current_url
                self._validate_url(response_url)
                if status in _REDIRECT_STATUSES:
                    location = headers.get("location")
                    if redirect_count == _MAX_REDIRECTS or not location:
                        raise SourceFailure("UNSAFE_URL")
                    redirected_url = urljoin(current_url, location)
                    self._validate_url(redirected_url)
                    if self._origin(current_url) != self._origin(redirected_url):
                        current_headers = self._without_sensitive_headers(current_headers)
                    current_url = redirected_url
                    continue
                if status < 200 or status >= 300:
                    raise SourceFailure("SOURCE_UNAVAILABLE")
                result = self._validated_result(
                    response,
                    response_url=response_url,
                    status=status,
                    headers=headers,
                    max_bytes=request.max_bytes,
                )
            except SourceFailure:
                raise
            except Exception:
                raise SourceFailure("INVALID_RESPONSE") from None
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        cleanup_failed = True
            if cleanup_failed:
                raise SourceFailure("SOURCE_UNAVAILABLE")
            return result
        raise SourceFailure("UNSAFE_URL")

    def _open(self, url: str, *, headers: Mapping[str, str], timeout_seconds: float):
        outgoing = Request(url, headers=dict(headers), method="GET")
        if hasattr(self._opener, "open"):
            return self._opener.open(outgoing, timeout=timeout_seconds)
        return self._opener(outgoing, timeout=timeout_seconds)

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int]:
        parsed = urlsplit(url)
        return parsed.scheme.lower(), (parsed.hostname or "").lower().rstrip("."), parsed.port or 443

    @staticmethod
    def _without_sensitive_headers(headers: Mapping[str, str]) -> dict[str, str]:
        def sensitive(name: str) -> bool:
            normalized = name.lower().replace("_", "-")
            return (
                normalized in _SENSITIVE_REDIRECT_HEADERS
                or "api-key" in normalized
                or "apikey" in normalized
                or "token" in normalized
            )

        return {name: value for name, value in headers.items() if not sensitive(name)}

    def _validate_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise SourceFailure("UNSAFE_URL") from None
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or hostname not in self._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
        ):
            raise SourceFailure("UNSAFE_URL")

    @staticmethod
    def _headers(response) -> dict[str, str]:
        raw_headers = getattr(response, "headers", {})
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}

    def _validated_result(
        self,
        response,
        *,
        response_url: str,
        status: int,
        headers: Mapping[str, str],
        max_bytes: int,
    ) -> HttpResult:
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        document_type = self._document_type(content_type)
        retrieved_at = self._as_utc(self._clock())
        observed_at = self._observed_at(headers.get("date"), retrieved_at)
        body = self._read_body(response, headers.get("content-encoding", ""), max_bytes)
        self._validate_document(body, document_type)
        return HttpResult(
            url=response_url,
            status=status,
            headers=MappingProxyType(dict(headers)),
            body=body,
            retrieved_at=retrieved_at,
            observed_at=observed_at,
            _validation_provenance=_HTTP_VALIDATION_PROVENANCE,
        )

    @staticmethod
    def _document_type(content_type: str) -> str:
        if content_type == "application/json" or content_type.endswith("+json"):
            return "json"
        if content_type in {"application/xml", "text/xml"} or content_type.endswith("+xml"):
            return "xml"
        raise SourceFailure("INVALID_CONTENT_TYPE")

    def _observed_at(self, value: str | None, retrieved_at: datetime) -> datetime | None:
        if value is None:
            return None
        try:
            observed_at = self._as_utc(parsedate_to_datetime(value))
        except (TypeError, ValueError, OverflowError):
            raise SourceFailure("INVALID_RESPONSE") from None
        if observed_at > retrieved_at + self._future_tolerance:
            raise SourceFailure("FUTURE_DATE")
        return observed_at

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise SourceFailure("INVALID_RESPONSE")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _read_body(response, encoding: str, max_bytes: int) -> bytes:
        normalized_encoding = encoding.strip().lower()
        if normalized_encoding in {"", "identity"}:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SourceFailure("RESPONSE_TOO_LARGE")
            return body
        if normalized_encoding == "gzip":
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif normalized_encoding == "deflate":
            decompressor = zlib.decompressobj()
        else:
            raise SourceFailure("INVALID_RESPONSE")

        output = bytearray()
        try:
            while chunk := response.read(_READ_CHUNK_BYTES):
                remaining = max_bytes - len(output)
                output.extend(decompressor.decompress(chunk, remaining + 1))
                if len(output) > max_bytes or decompressor.unconsumed_tail:
                    raise SourceFailure("RESPONSE_TOO_LARGE")
            output.extend(decompressor.flush(max_bytes - len(output) + 1))
        except SourceFailure:
            raise
        except zlib.error:
            raise SourceFailure("INVALID_RESPONSE") from None
        if len(output) > max_bytes:
            raise SourceFailure("RESPONSE_TOO_LARGE")
        if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
            raise SourceFailure("INVALID_RESPONSE")
        return bytes(output)

    @staticmethod
    def _validate_document(body: bytes, document_type: str) -> None:
        try:
            if document_type == "json":
                json.loads(
                    body,
                    parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                )
            else:
                ElementTree.fromstring(body)
        except (UnicodeDecodeError, ValueError, ElementTree.ParseError):
            raise SourceFailure("INVALID_RESPONSE") from None
