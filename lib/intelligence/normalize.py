"""Deterministic normalization for untrusted provider evidence."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


TITLE_LIMIT = 500
SUMMARY_LIMIT = 2_000
URL_LIMIT = 2_048
UPSTREAM_ID_LIMIT = 512


def normalize_text(value: object, limit: int) -> str:
    """Normalize bytes-as-data without assigning instructions any authority."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("normalization limit must be a non-negative integer")
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", " ")
    text = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf"} else character
        for character in text
    )
    return " ".join(text.split())[:limit]


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("source timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def canonicalize_url(value: object) -> str | None:
    raw = normalize_text(value, URL_LIMIT)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL must be valid HTTPS") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("source URL must be valid HTTPS")
    host = parsed.hostname.lower().rstrip(".")
    path = quote(unquote(parsed.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    canonical = urlunsplit(("https", host, path, query, ""))
    if len(canonical) > URL_LIMIT:
        raise ValueError("source URL exceeds limit")
    return canonical


@dataclass(frozen=True, slots=True)
class SourceItem:
    provider: str
    upstream_item_id: str | None
    canonical_url: str | None
    title: str
    summary: str
    canonical_content: str
    content_hash: str
    published_at: datetime | None
    effective_at: datetime | None
    retrieved_at: datetime
    authority: str
    metadata: Mapping[str, Any]
    trust: str = "untrusted_data"

    @property
    def source_url(self) -> str:
        return self.canonical_url or ""

    @property
    def normalized_text(self) -> str:
        return self.summary

    def hash_fields(self) -> dict[str, object]:
        return {"summary": self.summary, "title": self.title}


def content_hash(item: SourceItem) -> str:
    canonical = json.dumps(
        item.hash_fields(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256_hex(canonical.encode("utf-8"))


def _field(raw: object, name: str, default: object = None) -> object:
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def normalize_item(raw: object) -> SourceItem:
    provider = normalize_text(_field(raw, "provider", ""), 80).lower()
    title = normalize_text(_field(raw, "title", ""), TITLE_LIMIT)
    summary = normalize_text(
        _field(raw, "normalized_text", _field(raw, "summary", "")),
        SUMMARY_LIMIT,
    )
    if not provider or not title:
        raise ValueError("source provider and title are required")

    upstream = _field(raw, "upstream_item_id")
    upstream_id = None if upstream is None else normalize_text(upstream, UPSTREAM_ID_LIMIT)
    published = _field(raw, "published_at")
    effective = _field(raw, "effective_at")
    retrieved = _field(raw, "retrieved_at")
    _timestamp(published)
    _timestamp(effective)
    if not isinstance(retrieved, datetime) or retrieved.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    authority = normalize_text(_field(raw, "authority", ""), 40).lower()
    metadata = _field(raw, "metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("source metadata must be an object")

    preliminary = SourceItem(
        provider=provider,
        upstream_item_id=upstream_id or None,
        canonical_url=canonicalize_url(
            _field(raw, "source_url", _field(raw, "canonical_url", ""))
        ),
        title=title,
        summary=summary,
        canonical_content="",
        content_hash="",
        published_at=published,
        effective_at=effective,
        retrieved_at=retrieved,
        authority=authority,
        metadata=dict(metadata),
    )
    canonical = json.dumps(
        preliminary.hash_fields(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return SourceItem(
        provider=preliminary.provider,
        upstream_item_id=preliminary.upstream_item_id,
        canonical_url=preliminary.canonical_url,
        title=preliminary.title,
        summary=preliminary.summary,
        canonical_content=canonical,
        content_hash=sha256_hex(canonical.encode("utf-8")),
        published_at=preliminary.published_at,
        effective_at=preliminary.effective_at,
        retrieved_at=preliminary.retrieved_at,
        authority=preliminary.authority,
        metadata=preliminary.metadata,
    )


__all__ = [
    "SourceItem",
    "canonicalize_url",
    "content_hash",
    "normalize_item",
    "normalize_text",
    "sha256_hex",
]
