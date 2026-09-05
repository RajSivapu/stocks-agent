"""Bounded adapters for the approved zero-cost intelligence sources."""

from __future__ import annotations

import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from lib import config
from lib.intelligence.http import (
    BoundedHttpClient,
    HttpRequest,
    HttpResult,
    SourceFailure,
    cache_key,
)
from lib.intelligence.quota import QuotaSession


_MAX_TEXT_CHARACTERS = 2_000
_MAX_METADATA_BYTES = 8_192
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_ENTRIES = 32
_MAX_METADATA_STRING_CHARACTERS = 500
_MAX_RECEIPT_COUNT = 10_000
_CACHE_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class CollectionQuery:
    text: str
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    limit: int = 20
    cik: str | None = None
    series_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("collection query text is required")
        if not isinstance(self.symbols, tuple) or any(
            not isinstance(symbol, str) or not symbol.strip() for symbol in self.symbols
        ):
            raise ValueError("collection symbols must be a tuple of non-empty strings")
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start > self.end:
            raise ValueError("collection window must be ordered and timezone-aware")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 50:
            raise ValueError("collection limit must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class SourceItem:
    provider: str
    upstream_item_id: str | None
    source_url: str
    title: str
    normalized_text: str
    canonical_content: str
    content_hash: str
    published_at: datetime | None
    effective_at: datetime | None
    retrieved_at: datetime
    authority: str
    metadata: Mapping[str, Any]
    request_url: str | None = None
    reporting_at: datetime | None = None
    entity_ids: tuple[str, ...] = ()
    security_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RequestReceipt:
    provider: str
    reservation_id: str
    status: str
    cache_key: str
    requested_window: Mapping[str, str]
    requested_limit: int
    retrieved_at: datetime
    observed_at: datetime | None
    expires_at: datetime | None
    request_cost: int
    upstream_remaining: int | None
    returned_count: int
    accepted_count: int
    duplicate_count: int
    dropped_count: int
    response_hash: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionResult:
    items: tuple[SourceItem, ...]
    receipt: RequestReceipt
    requested_limit: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def parse_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        raw = str(value).strip()
        for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _utc(parsed.replace(tzinfo=parsed.tzinfo or timezone.utc))
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def bounded_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:_MAX_TEXT_CHARACTERS]


def publisher_reference(url: object) -> dict[str, str]:
    """Retain a bounded publisher link as untrusted metadata, never as source authority."""
    if not isinstance(url, str) or len(url) > 2_048:
        return {}
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return {}
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return {}
    return {
        "publisher_url": url,
        "publisher_url_authority": "untrusted_reference",
    }


def _bounded_metadata_value(value: object, depth: int = 0) -> object:
    if depth >= _MAX_METADATA_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:_MAX_METADATA_STRING_CHARACTERS]
    if isinstance(value, Mapping):
        bounded = {}
        entries = sorted(value.items(), key=lambda entry: str(entry[0]))
        for key, nested in entries[:_MAX_METADATA_ENTRIES]:
            bounded[str(key)[:100]] = _bounded_metadata_value(nested, depth + 1)
        return bounded
    if isinstance(value, (list, tuple)):
        return [
            _bounded_metadata_value(nested, depth + 1)
            for nested in value[:_MAX_METADATA_ENTRIES]
        ]
    return bounded_text(value)[:_MAX_METADATA_STRING_CHARACTERS]


def bounded_metadata(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return MappingProxyType({})
    bounded = _bounded_metadata_value(value)
    if not isinstance(bounded, dict):
        return MappingProxyType({})
    try:
        encoded = json.dumps(
            bounded, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode()
    except (TypeError, ValueError):
        return MappingProxyType({})
    if len(encoded) > _MAX_METADATA_BYTES:
        return MappingProxyType({})
    return MappingProxyType(bounded)


def security_ids(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    return tuple(sorted({
        symbol.strip().upper()
        for item in values
        if isinstance(item, str)
        for symbol in (item,)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,14}", symbol.strip())
    }))


def entity_ids(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    return tuple(sorted({
        identifier.strip().lower()
        for item in values
        if isinstance(item, str)
        for identifier in (item,)
        if identifier.strip() and len(identifier.strip()) <= 160
    }))


class SourceAdapter(ABC):
    provider: str
    allowed_hosts: frozenset[str]
    authority: str
    max_items_per_request: int = 50

    def __init__(
        self,
        http: BoundedHttpClient,
        quota: QuotaSession,
        *,
        secret_getter: Callable[[str], str] = config.secret,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.http = http
        self.quota = quota
        self.secret_getter = secret_getter
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @abstractmethod
    def _request(self, query: CollectionQuery) -> HttpRequest:
        raise NotImplementedError

    @abstractmethod
    def _records(
        self,
        payload: object,
        query: CollectionQuery,
        response: HttpResult,
    ) -> Sequence[Mapping[str, object]]:
        raise NotImplementedError

    def collect(self, query: CollectionQuery) -> CollectionResult:
        request = self._request(query)
        requested_window = MappingProxyType({
            "start": _utc(query.start).isoformat(),
            "end": _utc(query.end).isoformat(),
        })
        receipt_cache_key = cache_key(
            self.provider,
            {
                "query": query.text,
                "symbols": ",".join(query.symbols),
                "cik": query.cik or "",
                "series_id": query.series_id or "",
                "limit": str(query.limit),
            },
            json.dumps(dict(requested_window), separators=(",", ":"), sort_keys=True),
            1,
        )
        reservation_id = self.quota.consume_next(self.provider)
        response: HttpResult | None = None
        try:
            response = self.http.get(request)
            payload = json.loads(
                response.body,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            records = self._records(payload, query, response)
            if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
                raise ValueError("records must be a sequence")
            if len(records) > _MAX_RECEIPT_COUNT:
                raise ValueError("provider result count exceeds persistence bound")
            items: list[SourceItem] = []
            dropped = 0
            bound = min(query.limit, self.max_items_per_request)
            for record in records:
                if len(items) >= bound:
                    dropped += 1
                    continue
                item = self._normalize_record(record, query, response.retrieved_at)
                if item is None:
                    dropped += 1
                else:
                    items.append(item)
            body_hash = hashlib.sha256(response.body).hexdigest()
            receipt = RequestReceipt(
                provider=self.provider,
                reservation_id=reservation_id,
                status="cache_hit" if response.cache_hit else "succeeded",
                cache_key=receipt_cache_key,
                requested_window=requested_window,
                requested_limit=query.limit,
                retrieved_at=response.retrieved_at,
                observed_at=response.observed_at,
                expires_at=response.retrieved_at + _CACHE_TTL,
                request_cost=0 if response.cache_hit else 1,
                upstream_remaining=None,
                returned_count=len(records),
                accepted_count=len(items),
                duplicate_count=0,
                dropped_count=dropped,
                response_hash=body_hash,
            )
            return CollectionResult(tuple(items), receipt, query.limit)
        except (SourceFailure, UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            cached_failure = response is not None and response.cache_hit
            receipt = RequestReceipt(
                provider=self.provider,
                reservation_id=reservation_id,
                status="failed",
                cache_key=receipt_cache_key,
                requested_window=requested_window,
                requested_limit=query.limit,
                retrieved_at=response.retrieved_at if response is not None else _utc(self.clock()),
                observed_at=response.observed_at if response is not None else None,
                expires_at=None,
                request_cost=0 if cached_failure else 1,
                upstream_remaining=None,
                returned_count=0,
                accepted_count=0,
                duplicate_count=0,
                dropped_count=0,
                response_hash=None,
                error_code=exc.code if isinstance(exc, SourceFailure) else "INVALID_RESPONSE",
            )
            return CollectionResult((), receipt, query.limit)

    def _normalize_record(
        self,
        record: Mapping[str, object],
        query: CollectionQuery,
        retrieved_at: datetime,
    ) -> SourceItem | None:
        if not isinstance(record, Mapping):
            return None
        request_url = str(record.get("request_url") or "")
        item_url = str(record.get("item_url") or record.get("source_url") or request_url)
        try:
            request = urlsplit(request_url)
            item = urlsplit(item_url)
        except ValueError:
            return None
        if (
            request.scheme.lower() != "https"
            or (request.hostname or "").lower().rstrip(".") not in self.allowed_hosts
            or request.username is not None
            or request.password is not None
            or request.port not in (None, 443)
            or item.scheme.lower() != "https"
            or not item.hostname
            or item.username is not None
            or item.password is not None
            or item.port not in (None, 443)
        ):
            return None
        title = bounded_text(record.get("title"))[:500]
        if not title:
            return None
        text = bounded_text(record.get("text"))
        upstream_id = record.get("upstream_item_id")
        if upstream_id is None or not str(upstream_id).strip():
            return None
        metadata = bounded_metadata(record.get("metadata"))
        raw_published_at = record.get("published_at")
        published_at = parse_timestamp(raw_published_at)
        effective_at = parse_timestamp(record.get("effective_at"))
        reporting_at = parse_timestamp(record.get("reporting_at"))
        if raw_published_at not in (None, "") and published_at is None:
            return None
        collection_time = published_at or retrieved_at
        if not _utc(query.start) <= collection_time <= _utc(query.end):
            return None
        entities = entity_ids(record.get("entity_ids"))
        securities = security_ids(record.get("security_ids"))
        source_metadata = dict(metadata)
        if entities:
            source_metadata["entity_ids"] = list(entities)
        if securities:
            source_metadata["security_ids"] = list(securities)
        canonical = json.dumps(
            {
                "provider": self.provider,
                "upstream_item_id": None if upstream_id is None else str(upstream_id)[:512],
                "item_url": item_url[:2048],
                "request_url": request_url[:2048],
                "title": title,
                "text": text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )[:4096]
        return SourceItem(
            provider=self.provider,
            upstream_item_id=None if upstream_id is None else str(upstream_id)[:512],
            source_url=item_url[:2048],
            title=title,
            normalized_text=text,
            canonical_content=canonical,
            content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            published_at=published_at,
            effective_at=effective_at,
            retrieved_at=retrieved_at,
            authority=self.authority,
            metadata=bounded_metadata(source_metadata),
            request_url=request_url[:2048],
            reporting_at=reporting_at,
            entity_ids=entities,
            security_ids=securities,
        )


def build_adapter(
    provider: str,
    http: BoundedHttpClient,
    quota: QuotaSession,
    *,
    secret_getter: Callable[[str], str] = config.secret,
    clock: Callable[[], datetime] | None = None,
) -> SourceAdapter:
    from .alpha_vantage import AlphaVantageAdapter
    from .finnhub import FinnhubAdapter
    from .gdelt import GdeltAdapter
    from .official import OFFICIAL_ADAPTERS
    from .social import SocialAdapter
    from .yahoo import YahooAdapter

    adapter_types: dict[str, type[SourceAdapter]] = {
        "gdelt": GdeltAdapter,
        "alpha_vantage": AlphaVantageAdapter,
        "finnhub": FinnhubAdapter,
        "yahoo": YahooAdapter,
        "social": SocialAdapter,
        **OFFICIAL_ADAPTERS,
    }
    try:
        adapter_type = adapter_types[provider]
    except KeyError:
        raise ValueError("provider is not approved") from None
    return adapter_type(http, quota, secret_getter=secret_getter, clock=clock)


__all__ = [
    "CollectionQuery",
    "CollectionResult",
    "RequestReceipt",
    "SourceAdapter",
    "SourceItem",
    "build_adapter",
    "entity_ids",
    "security_ids",
]
