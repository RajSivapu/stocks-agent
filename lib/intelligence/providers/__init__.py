"""Bounded adapters for the approved zero-cost intelligence sources."""

from __future__ import annotations

import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from lib import config
from lib.intelligence.policy import _PROVIDERS
from lib.intelligence.http import (
    BoundedHttpClient,
    HttpRequest,
    HttpResult,
    SourceFailure,
    cache_key,
)
from lib.intelligence.quota import QuotaExceeded, QuotaSession


# Every reviewed outbound reservation uses the same durable transport barrier.
# Yahoo is deliberately separate: its quote transport is protected by the gateway.
RESERVED_OUTBOUND_PROVIDERS = frozenset(_PROVIDERS) - {"yahoo"}


_MAX_TEXT_CHARACTERS = 2_000
_MAX_METADATA_BYTES = 8_192
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_ENTRIES = 32
_MAX_METADATA_STRING_CHARACTERS = 500
_MAX_RECEIPT_COUNT = 10_000
_CACHE_TTL = timedelta(minutes=15)
_ACTIVE_MARKUP = re.compile(
    r"(?:<\s*/?\s*(?:script|iframe|object|embed|style|svg|math|img|link|meta|form|input|video|audio)\b|"
    r"\bon[a-z]{2,40}\s*=|\bjavascript\s*:)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CollectionQuery:
    text: str
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    limit: int = 20
    cik: str | None = None
    series_id: str | None = None
    capability_id: str | None = None
    cursor_token: str | None = None
    page: int = 1
    overlap_seconds: int = 7_200
    next_retry_phase: str | None = None
    accession_number: str | None = None
    primary_document: str | None = None

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
        if self.capability_id is not None and (
            not isinstance(self.capability_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.capability_id) is None
        ):
            raise ValueError("collection capability ID is invalid")
        if self.cursor_token is not None and (
            not isinstance(self.cursor_token, str)
            or not self.cursor_token
            or len(self.cursor_token) > 2_048
            or any(ord(character) < 32 for character in self.cursor_token)
        ):
            raise ValueError("collection cursor token is invalid")
        if isinstance(self.page, bool) or not isinstance(self.page, int) or not 1 <= self.page <= 10:
            raise ValueError("collection page must be between 1 and 10")
        if (
            isinstance(self.overlap_seconds, bool)
            or not isinstance(self.overlap_seconds, int)
            or not 7_200 <= self.overlap_seconds <= 31 * 24 * 60 * 60
        ):
            raise ValueError("collection overlap must be at least two hours and bounded")
        if self.next_retry_phase is not None and self.next_retry_phase not in {
            "pre-market", "intraday", "post-market", "on-demand"
        }:
            raise ValueError("collection retry phase is invalid")
        for value, label in (
            (self.accession_number, "accession number"),
            (self.primary_document, "primary document"),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value
                or len(value) > 255
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"collection {label} is invalid")


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
    source_receipt_id: str | None = None
    cache_predecessor_receipt_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


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


def contains_active_markup(value: object) -> bool:
    return _ACTIVE_MARKUP.search(str(value or "")) is not None


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

    def _decode_response(self, response: HttpResult) -> object:
        return json.loads(
            response.body,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )

    def _authority(self, query: CollectionQuery) -> str:
        return self.authority

    def _progress_metadata(
        self,
        payload: object,
        query: CollectionQuery,
        response: HttpResult,
        records: Sequence[Mapping[str, object]],
        bound: int,
    ) -> Mapping[str, object]:
        return MappingProxyType({
            "truncated": len(records) > bound,
            "backlog_remaining": False,
        })

    @staticmethod
    def _receipt_metadata(
        query: CollectionQuery,
        *,
        status: str,
        returned: int,
        progress: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        from lib.intelligence.cursors import source_outcome

        outcome = source_outcome(status=status, returned=returned)
        metadata: dict[str, object] = {
            "backlog_remaining": False,
            "capability_id": query.capability_id,
            "coverage_status": outcome.coverage_status,
            "cursor_end": _utc(query.end).isoformat(),
            "cursor_start": _utc(query.start).isoformat(),
            "next_retry_phase": query.next_retry_phase,
            "overlap_seconds": query.overlap_seconds,
            "truncated": False,
        }
        if progress:
            metadata.update(progress)
        return bounded_metadata({key: value for key, value in metadata.items() if value is not None})

    def collect(
        self,
        query: CollectionQuery,
        *,
        source_receipt_id: str | None = None,
        before_transport_attempt: Callable[[RequestReceipt], None] | None = None,
    ) -> CollectionResult:
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
                "capability_id": query.capability_id or "",
                "cursor_token": query.cursor_token or "",
                "page": str(query.page),
                "accession_number": query.accession_number or "",
                "primary_document": query.primary_document or "",
                "limit": str(query.limit),
            },
            json.dumps(dict(requested_window), separators=(",", ":"), sort_keys=True),
            1,
        )
        # The pre-open validation is deliberately before quota admission: zero transport attempts cost zero.
        validate_request = getattr(self.http, "validate_request", None)
        if callable(validate_request):
            validate_request(request)
        reservation_id = self.quota.receipt_reservation_id(self.provider)
        attempts = 0

        def admit_attempt() -> None:
            # A receipt has one reservation identity, so every counted open for
            # this collection must fit that reservation.  Do not silently spill
            # a redirect into another reservation that the terminal receipt
            # cannot prove; quota exhaustion stops before that next open.
            nonlocal attempts
            self.quota.consume(self.provider, reservation_id)
            attempts += 1
            if before_transport_attempt is not None:
                before_transport_attempt(RequestReceipt(
                    provider=self.provider,
                    reservation_id=reservation_id,
                    status="failed",
                    cache_key=receipt_cache_key,
                    requested_window=requested_window,
                    requested_limit=query.limit,
                    retrieved_at=_utc(self.clock()),
                    observed_at=None,
                    expires_at=None,
                    request_cost=attempts,
                    upstream_remaining=None,
                    returned_count=0,
                    accepted_count=0,
                    duplicate_count=0,
                    dropped_count=0,
                    response_hash=None,
                    error_code="TRANSPORT_OUTCOME_UNCERTAIN",
                    source_receipt_id=source_receipt_id,
                ))

        response: HttpResult | None = None
        try:
            reservation_id = self.quota.next_reservation_id(self.provider)
            if callable(validate_request):
                response = self.http.get(request, before_attempt=admit_attempt)
            else:  # deterministic fixture transport has one declared outbound attempt
                admit_attempt()
                response = self.http.get(request)
            payload = self._decode_response(response)
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
            progress = self._progress_metadata(payload, query, response, records, bound)
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
                request_cost=0 if response.cache_hit else attempts,
                upstream_remaining=None,
                returned_count=len(records),
                accepted_count=len(items),
                duplicate_count=0,
                dropped_count=dropped,
                response_hash=body_hash,
                source_receipt_id=source_receipt_id,
                metadata=self._receipt_metadata(
                    query,
                    status="cache_hit" if response.cache_hit else "succeeded",
                    returned=len(items),
                    progress=progress,
                ),
            )
            return CollectionResult(tuple(items), receipt, query.limit)
        except (SourceFailure, QuotaExceeded, UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            cached_failure = response is not None and response.cache_hit
            receipt = RequestReceipt(
                provider=self.provider,
                reservation_id=reservation_id,
                status="quota_blocked" if isinstance(exc, QuotaExceeded) else "failed",
                cache_key=receipt_cache_key,
                requested_window=requested_window,
                requested_limit=query.limit,
                retrieved_at=response.retrieved_at if response is not None else _utc(self.clock()),
                observed_at=response.observed_at if response is not None else None,
                expires_at=None,
                request_cost=0 if cached_failure else attempts,
                upstream_remaining=None,
                returned_count=0,
                accepted_count=0,
                duplicate_count=0,
                dropped_count=0,
                response_hash=None,
                error_code=(exc.code if isinstance(exc, SourceFailure) else "QUOTA_BLOCKED" if isinstance(exc, QuotaExceeded) else "INVALID_RESPONSE"),
                source_receipt_id=source_receipt_id,
                metadata=self._receipt_metadata(
                    query,
                    status=(
                        "quota_blocked"
                        if isinstance(exc, QuotaExceeded)
                        else "configuration_missing"
                        if isinstance(exc, SourceFailure) and exc.code == "CONFIGURATION_MISSING"
                        else "unsupported"
                        if isinstance(exc, SourceFailure) and exc.code == "UNSUPPORTED_QUERY"
                        else "failed"
                    ),
                    returned=0,
                ),
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
        if any(contains_active_markup(value) for value in (upstream_id, title, text)):
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
            authority=self._authority(query),
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
