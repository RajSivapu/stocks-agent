"""Safe bounded RSS and Atom parsing for reviewed official feeds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from types import MappingProxyType
from urllib.parse import urlsplit
from xml.etree import ElementTree

from lib.intelligence.http import HttpRequest, HttpResult, SourceFailure

from . import CollectionQuery, SourceAdapter, bounded_text


_MAX_FEED_ITEMS = 500


@dataclass(frozen=True, slots=True)
class FeedItem:
    upstream_item_id: str
    url: str
    title: str
    summary: str
    published_at: datetime | None
    effective_at: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _children(node: ElementTree.Element, name: str) -> tuple[ElementTree.Element, ...]:
    return tuple(child for child in node if _local_name(child.tag) == name)


def _first_text(node: ElementTree.Element, *names: str) -> str:
    wanted = set(names)
    for child in node.iter():
        if _local_name(child.tag) in wanted and child.text:
            value = bounded_text(child.text)
            if value:
                return value
    return ""


def _link(node: ElementTree.Element) -> str:
    for child in node.iter():
        if _local_name(child.tag) != "link":
            continue
        relation = str(child.attrib.get("rel", "alternate")).lower()
        if relation not in {"", "alternate"}:
            continue
        value = child.attrib.get("href") or child.text
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_https_url(value: str, allowed_hosts: frozenset[str]) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SourceFailure("INVALID_FEED") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or host not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
    ):
        raise SourceFailure("INVALID_FEED")
    return value


def parse_bounded_feed(
    raw: bytes,
    *,
    source_url: str,
    max_bytes: int,
    max_items: int,
    allowed_hosts: frozenset[str] | None = None,
) -> tuple[FeedItem, ...]:
    """Parse RSS/Atom bytes without DTDs, entities, active HTML, or unsafe links."""
    if (
        not isinstance(raw, bytes)
        or not raw
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= 5_000_000
        or len(raw) > max_bytes
        or isinstance(max_items, bool)
        or not isinstance(max_items, int)
        or not 1 <= max_items <= _MAX_FEED_ITEMS
    ):
        raise SourceFailure("INVALID_FEED")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper or b"<SCRIPT" in upper:
        raise SourceFailure("INVALID_FEED")
    try:
        source_host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
    except ValueError:
        raise SourceFailure("INVALID_FEED") from None
    hosts = allowed_hosts or frozenset({source_host})
    _safe_https_url(source_url, hosts)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise SourceFailure("INVALID_FEED") from None
    root_name = _local_name(root.tag)
    if root_name not in {"rss", "rdf", "feed"}:
        raise SourceFailure("INVALID_FEED")
    nodes = tuple(
        node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}
    )
    if len(nodes) > _MAX_FEED_ITEMS:
        raise SourceFailure("INVALID_FEED")
    items: list[FeedItem] = []
    for node in nodes[:max_items]:
        identity = _first_text(node, "guid", "id")
        title = _first_text(node, "title")[:500]
        url = _link(node)
        summary = _first_text(node, "description", "summary", "content")
        raw_date = _first_text(node, "pubdate", "published", "updated")
        published_at = _timestamp(raw_date)
        if not identity or not title or not url or not raw_date or published_at is None:
            raise SourceFailure("INVALID_FEED")
        _safe_https_url(url, hosts)
        if re.search(r"<\s*(script|iframe|object|embed)\b", summary, re.IGNORECASE):
            raise SourceFailure("INVALID_FEED")
        items.append(FeedItem(
            upstream_item_id=identity[:512],
            url=url[:2_048],
            title=title,
            summary=summary,
            published_at=published_at,
            metadata=MappingProxyType({}),
        ))
    return tuple(items)


class OfficialFeedAdapter(SourceAdapter):
    """One-request official feed adapter with exact route and redirect validation."""

    feed_routes: Mapping[str, str] = MappingProxyType({})
    response_routes: Mapping[str, frozenset[str]] = MappingProxyType({})
    allow_text_html_xml = False
    max_source_bytes = 1_000_000

    def _route(self, query: CollectionQuery) -> str:
        capability_id = query.capability_id
        try:
            return self.feed_routes[capability_id]  # type: ignore[index]
        except KeyError:
            raise SourceFailure("UNSUPPORTED_QUERY") from None

    def _request(self, query: CollectionQuery) -> HttpRequest:
        route = self._route(query)
        allowed = self.response_routes.get(query.capability_id or "", frozenset({route}))
        return HttpRequest(
            route,
            max_bytes=self.max_source_bytes,
            expected_document="xml",
            allow_mislabeled_xml=self.allow_text_html_xml,
            allowed_redirect_urls=frozenset(allowed - {route}),
        )

    def _decode_response(self, response: HttpResult) -> object:
        content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].lower()
        allowed = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
        if self.allow_text_html_xml:
            allowed.add("text/html")
        if content_type not in allowed:
            raise SourceFailure("INVALID_CONTENT_TYPE")
        return response.body

    def _validate_response_route(self, query: CollectionQuery, response: HttpResult) -> None:
        requested = self._route(query)
        allowed = self.response_routes.get(query.capability_id or "", frozenset({requested}))
        if response.url not in allowed:
            raise SourceFailure("UNSAFE_URL")

    def _records(
        self,
        payload: object,
        query: CollectionQuery,
        response: HttpResult,
    ) -> Sequence[Mapping[str, object]]:
        if not isinstance(payload, bytes):
            raise SourceFailure("INVALID_FEED")
        self._validate_response_route(query, response)
        records = parse_bounded_feed(
            payload,
            source_url=response.url,
            max_bytes=self.max_source_bytes,
            max_items=self.max_items_per_request,
            allowed_hosts=self.allowed_hosts,
        )
        request_url = self._route(query)
        return tuple({
            "upstream_item_id": item.upstream_item_id,
            "request_url": request_url,
            "item_url": item.url,
            "title": item.title,
            "text": item.summary,
            "published_at": item.published_at,
            "effective_at": item.effective_at,
            "metadata": dict(item.metadata),
        } for item in records)


__all__ = ["FeedItem", "OfficialFeedAdapter", "parse_bounded_feed"]
