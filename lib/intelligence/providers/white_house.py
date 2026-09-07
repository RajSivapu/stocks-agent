"""Bounded White House listing and sitemap discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from types import MappingProxyType
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from lib.intelligence.http import HttpRequest, HttpResult, SourceFailure

from . import CollectionQuery, SourceAdapter, bounded_text, parse_timestamp
from .rss import FeedItem


WHITE_HOUSE_ROOT = "https://www.whitehouse.gov"
WHITE_HOUSE_SITEMAP_INDEX = f"{WHITE_HOUSE_ROOT}/sitemap_index.xml"
_CATEGORY_PATHS = MappingProxyType({
    "white_house_fact_sheets": "/fact-sheets/",
    "white_house_presidential_actions": "/presidential-actions/",
    "white_house_briefings_statements": "/briefings-statements/",
})
_APPROVED_NEWS_PATH = re.compile(
    r"/(?:fact-sheets|presidential-actions|briefings-statements)/.+/"
)
_POST_SITEMAP_PATH = re.compile(r"/post-sitemap(?:[0-9]+)?\.xml")


def _safe_white_house_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SourceFailure("UNSAFE_URL") from None
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower().rstrip(".") != "www.whitehouse.gov"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
    ):
        raise SourceFailure("UNSAFE_URL")
    return value


def _listing_root(path: str) -> str | None:
    for root in _CATEGORY_PATHS.values():
        if path == root or re.fullmatch(re.escape(root) + r"page/[1-9][0-9]?/", path):
            return root
    return None


class _ListingParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.in_article = 0
        self.current: dict[str, object] | None = None
        self.anchor_text: list[str] | None = None
        self.summary_text: list[str] | None = None
        self.items: list[dict[str, object]] = []
        self.next_page: str | None = None

    def handle_starttag(self, tag, attrs):
        lowered = tag.lower()
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        if lowered == "article":
            self.in_article += 1
            if self.in_article == 1:
                self.current = {}
        elif lowered == "a":
            href = attributes.get("href", "")
            absolute = urljoin(self.source_url, href)
            classes = set(attributes.get("class", "").split())
            if self.in_article and self.current is not None and "url" not in self.current:
                self.current["url"] = absolute
                self.anchor_text = []
            elif "next" in classes or "next" in attributes.get("rel", "").split():
                self.next_page = absolute
        elif lowered == "time" and self.in_article and self.current is not None:
            self.current["published_at"] = attributes.get("datetime", "")
        elif lowered == "p" and self.in_article and self.current is not None:
            self.summary_text = []

    def handle_data(self, data):
        if self.anchor_text is not None:
            self.anchor_text.append(data)
        if self.summary_text is not None:
            self.summary_text.append(data)

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered == "a" and self.anchor_text is not None and self.current is not None:
            self.current["title"] = " ".join(self.anchor_text)
            self.anchor_text = None
        elif lowered == "p" and self.summary_text is not None and self.current is not None:
            self.current["summary"] = " ".join(self.summary_text)
            self.summary_text = None
        elif lowered == "article" and self.in_article:
            if self.in_article == 1 and self.current is not None:
                self.items.append(self.current)
                self.current = None
            self.in_article -= 1


def parse_white_house_listing(
    raw: bytes,
    *,
    source_url: str,
    max_bytes: int,
    max_items: int,
) -> tuple[tuple[FeedItem, ...], str | None]:
    if not raw or len(raw) > max_bytes or b"\x00" in raw:
        raise SourceFailure("INVALID_RESPONSE")
    source = _safe_white_house_url(source_url)
    source_path = urlsplit(source).path
    root = _listing_root(source_path)
    if root is None:
        raise SourceFailure("UNSAFE_URL")
    parser = _ListingParser(source)
    try:
        parser.feed(raw.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeDecodeError, ValueError):
        raise SourceFailure("INVALID_RESPONSE") from None
    records: list[FeedItem] = []
    for record in parser.items:
        url = str(record.get("url", ""))
        try:
            _safe_white_house_url(url)
        except SourceFailure:
            continue
        path = urlsplit(url).path
        if not path.startswith(root) or _APPROVED_NEWS_PATH.fullmatch(path) is None:
            continue
        title = bounded_text(record.get("title"))[:500]
        published = parse_timestamp(record.get("published_at"))
        if not title or published is None:
            continue
        records.append(FeedItem(
            upstream_item_id=url,
            url=url,
            title=title,
            summary=bounded_text(record.get("summary")),
            published_at=published,
        ))
        if len(records) >= max_items:
            break
    next_page = parser.next_page
    if next_page is not None:
        _safe_white_house_url(next_page)
        next_path = urlsplit(next_page).path
        if _listing_root(next_path) != root or next_path == source_path:
            raise SourceFailure("UNSAFE_URL")
    return tuple(records), next_page


@dataclass(frozen=True, slots=True)
class WhiteHouseSitemap:
    child_sitemaps: tuple[str, ...]
    items: tuple[FeedItem, ...]


def parse_white_house_sitemap(
    raw: bytes,
    *,
    source_url: str,
    max_bytes: int,
    max_items: int,
) -> WhiteHouseSitemap:
    if (
        not raw
        or len(raw) > max_bytes
        or b"<!DOCTYPE" in raw.upper()
        or b"<!ENTITY" in raw.upper()
    ):
        raise SourceFailure("INVALID_RESPONSE")
    source = _safe_white_house_url(source_url)
    source_path = urlsplit(source).path
    if source_path != "/sitemap_index.xml" and _POST_SITEMAP_PATH.fullmatch(source_path) is None:
        raise SourceFailure("UNSAFE_URL")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise SourceFailure("INVALID_RESPONSE") from None
    root_name = str(root.tag).rsplit("}", 1)[-1]
    locations = []
    for node in root:
        location = next(
            (
                child.text.strip()
                for child in node
                if str(child.tag).rsplit("}", 1)[-1] == "loc" and child.text
            ),
            "",
        )
        last_modified = next(
            (
                child.text.strip()
                for child in node
                if str(child.tag).rsplit("}", 1)[-1] == "lastmod" and child.text
            ),
            "",
        )
        if location:
            locations.append((location, last_modified))
    if len(locations) > 5_000:
        raise SourceFailure("INVALID_RESPONSE")
    if root_name == "sitemapindex" and source_path == "/sitemap_index.xml":
        children = []
        for location, _last_modified in locations:
            try:
                _safe_white_house_url(location)
            except SourceFailure:
                continue
            if _POST_SITEMAP_PATH.fullmatch(urlsplit(location).path):
                children.append(location)
        return WhiteHouseSitemap(tuple(dict.fromkeys(children[:20])), ())
    if root_name != "urlset" or _POST_SITEMAP_PATH.fullmatch(source_path) is None:
        raise SourceFailure("INVALID_RESPONSE")
    items = []
    for location, last_modified in locations:
        try:
            _safe_white_house_url(location)
        except SourceFailure:
            continue
        path = urlsplit(location).path
        if _APPROVED_NEWS_PATH.fullmatch(path) is None:
            continue
        slug = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        items.append(FeedItem(
            upstream_item_id=location,
            url=location,
            title=bounded_text(slug).title()[:500],
            summary="",
            published_at=None,
            metadata=MappingProxyType({"last_modified": last_modified[:80]}),
        ))
        if len(items) >= max_items:
            break
    return WhiteHouseSitemap((), tuple(items))


def _without_fragment(value: str) -> tuple[str, int]:
    parsed = urlsplit(value)
    offset = 0
    if parsed.fragment:
        match = re.fullmatch(r"offset=([0-9]{1,5})", parsed.fragment)
        if match is None:
            raise SourceFailure("INVALID_QUERY")
        offset = int(match.group(1))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")), offset


class WhiteHouseAdapter(SourceAdapter):
    provider = "white_house"
    authority = "official_executive_statement"
    allowed_hosts = frozenset({"www.whitehouse.gov"})
    max_items_per_request = 50
    max_source_bytes = 350_000

    def _authority(self, query: CollectionQuery) -> str:
        return {
            "white_house_presidential_actions": "official_executive_action",
            "white_house_sitemap": "official_executive_index",
        }.get(query.capability_id, self.authority)

    def _request_url(self, query: CollectionQuery) -> str:
        if query.capability_id in _CATEGORY_PATHS:
            root = _CATEGORY_PATHS[query.capability_id]
            if query.cursor_token is not None:
                value, offset = _without_fragment(query.cursor_token)
                if offset:
                    raise SourceFailure("INVALID_QUERY")
                _safe_white_house_url(value)
                if _listing_root(urlsplit(value).path) != root:
                    raise SourceFailure("UNSAFE_URL")
                return value
            return f"{WHITE_HOUSE_ROOT}{root}" if query.page == 1 else f"{WHITE_HOUSE_ROOT}{root}page/{query.page}/"
        if query.capability_id == "white_house_sitemap":
            if query.cursor_token is None:
                return WHITE_HOUSE_SITEMAP_INDEX
            value, _offset = _without_fragment(query.cursor_token)
            _safe_white_house_url(value)
            if _POST_SITEMAP_PATH.fullmatch(urlsplit(value).path) is None:
                raise SourceFailure("UNSAFE_URL")
            return value
        raise SourceFailure("UNSUPPORTED_QUERY")

    def _request(self, query: CollectionQuery) -> HttpRequest:
        listing = query.capability_id in _CATEGORY_PATHS
        return HttpRequest(
            self._request_url(query),
            max_bytes=self.max_source_bytes,
            expected_document="html" if listing else "xml",
            allowed_redirect_urls=frozenset(),
        )

    def _decode_response(self, response: HttpResult) -> object:
        return response.body

    def _records(self, payload, query, response):
        if not isinstance(payload, bytes) or response.url != self._request_url(query):
            raise SourceFailure("UNSAFE_URL")
        if query.capability_id in _CATEGORY_PATHS:
            items, _next = parse_white_house_listing(
                payload,
                source_url=response.url,
                max_bytes=self.max_source_bytes,
                max_items=self.max_items_per_request,
            )
        else:
            parsed = parse_white_house_sitemap(
                payload,
                source_url=response.url,
                max_bytes=self.max_source_bytes,
                max_items=5_000,
            )
            _url, offset = _without_fragment(query.cursor_token) if query.cursor_token else (response.url, 0)
            items = parsed.items[offset:offset + self.max_items_per_request]
        request_url = self._request_url(query)
        return tuple({
            "upstream_item_id": item.upstream_item_id,
            "request_url": request_url,
            "item_url": item.url,
            "title": item.title,
            "text": item.summary,
            "published_at": item.published_at,
            "metadata": dict(item.metadata),
        } for item in items)

    def _progress_metadata(self, payload, query, response, records, bound):
        if not isinstance(payload, bytes):
            raise SourceFailure("INVALID_RESPONSE")
        if query.capability_id in _CATEGORY_PATHS:
            _items, next_page = parse_white_house_listing(
                payload,
                source_url=response.url,
                max_bytes=self.max_source_bytes,
                max_items=self.max_items_per_request,
            )
            return MappingProxyType({
                "truncated": next_page is not None,
                "backlog_remaining": next_page is not None,
                **({"backlog_token": next_page} if next_page else {}),
            })
        parsed = parse_white_house_sitemap(
            payload,
            source_url=response.url,
            max_bytes=self.max_source_bytes,
            max_items=5_000,
        )
        if parsed.child_sitemaps:
            return MappingProxyType({
                "truncated": True,
                "backlog_remaining": True,
                "backlog_token": parsed.child_sitemaps[0],
                "sitemap_children": list(parsed.child_sitemaps),
            })
        _url, offset = _without_fragment(query.cursor_token) if query.cursor_token else (response.url, 0)
        next_offset = offset + len(records)
        more = next_offset < len(parsed.items)
        token = f"{response.url}#offset={next_offset}" if more else None
        return MappingProxyType({
            "truncated": more,
            "backlog_remaining": more,
            **({"backlog_token": token} if token else {}),
        })


__all__ = [
    "WHITE_HOUSE_SITEMAP_INDEX",
    "WhiteHouseAdapter",
    "WhiteHouseSitemap",
    "parse_white_house_listing",
    "parse_white_house_sitemap",
]
