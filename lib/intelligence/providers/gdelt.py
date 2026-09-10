from datetime import timezone
from email.utils import parsedate_to_datetime
import re
from types import MappingProxyType
from urllib.parse import urlsplit
from xml.etree import ElementTree

from lib.intelligence.http import HttpRequest, SourceFailure
from lib.intelligence.themes import (
    source_dynamic_theme_label,
    source_syndication_fingerprint,
)

from . import (
    CollectionQuery,
    SourceAdapter,
    bounded_text,
    publisher_reference,
    security_ids,
)


_FEED_URL = "https://data.gdeltproject.org/gdeltv3/gal/feed.rss"
_MAX_FEED_BYTES = 5_000_000
_MAX_FEED_ITEMS = 10_000
_QUERY_STOP_WORDS = frozenset({
    "and", "developments", "industry", "market", "news", "or", "rolling",
    "sample", "the",
})
_MARKET_SIGNAL_TERMS = (
    "acquisition", "ai", "aluminium", "aluminum", "award", "battery",
    "capacity", "chip", "commercial", "company", "contract", "copper",
    "corporation", "data center", "defense", "demand", "deployment",
    "earnings", "energy",
    "economy", "economic", "expansion", "factory", "federal", "funding",
    "gold", "grid", "heat", "inflation", "interest rate", "jobs",
    "infrastructure", "investment", "investor", "magnet", "manufacturing",
    "market", "merger", "natural gas", "nuclear", "oil", "plant", "power",
    "profit", "rare earth",
    "revenue", "robot", "robotics", "semiconductor", "shares", "silver",
    "solar", "stock", "supplier", "supply", "tariff", "trade", "uranium",
    "utility", "wind",
)
def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _first_text(node: ElementTree.Element, name: str) -> str:
    for child in node:
        if _local_name(child.tag) == name and child.text:
            return bounded_text(child.text)
    return ""


def _published_at(value: str):
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _contains_term(title: str, term: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title,
    ) is not None


def _relevance_score(title: str, query: CollectionQuery) -> int:
    normalized = title.casefold()
    query_terms = tuple(dict.fromkeys(
        term for term in re.findall(
            r"[a-z0-9][a-z0-9.+&-]{1,}",
            " ".join((query.text, *query.symbols)).casefold(),
        ) if term not in _QUERY_STOP_WORDS
    ))
    query_matches = sum(_contains_term(normalized, term) for term in query_terms)
    query_score = query_matches if query_matches >= 2 else 0
    market_score = sum(
        _contains_term(normalized, term) for term in _MARKET_SIGNAL_TERMS
    )
    return query_score * 100 + market_score


class GdeltAdapter(SourceAdapter):
    provider = "gdelt"
    allowed_hosts = frozenset({"data.gdeltproject.org"})
    authority = "radar"
    max_items_per_request = 50

    def _evidence_url(self, query: CollectionQuery) -> str:
        return _FEED_URL

    def _request(self, query: CollectionQuery) -> HttpRequest:
        return HttpRequest(
            _FEED_URL,
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9",
                "User-Agent": "stocks-agent owner research",
            },
            max_bytes=_MAX_FEED_BYTES,
            expected_document="xml",
            allowed_redirect_urls=frozenset(),
        )

    def _decode_response(self, response):
        content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].lower()
        if content_type not in {
            "application/rss+xml", "application/xml", "text/xml",
        }:
            raise SourceFailure("INVALID_CONTENT_TYPE")
        if not isinstance(response.body, bytes) or not response.body \
                or len(response.body) > _MAX_FEED_BYTES:
            raise SourceFailure("INVALID_FEED")
        upper = response.body.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper or b"<SCRIPT" in upper:
            raise SourceFailure("INVALID_FEED")
        try:
            root = ElementTree.fromstring(response.body)
        except ElementTree.ParseError:
            raise SourceFailure("INVALID_FEED") from None
        if _local_name(root.tag) != "rss":
            raise SourceFailure("INVALID_FEED")
        return root

    def _records(self, payload, query, response):
        if not isinstance(payload, ElementTree.Element) or response.url != _FEED_URL:
            raise SourceFailure("INVALID_FEED")
        nodes = tuple(
            node for node in payload.iter() if _local_name(node.tag) == "item"
        )
        if len(nodes) > _MAX_FEED_ITEMS:
            raise SourceFailure("INVALID_FEED")
        ranked_records = []
        for node in nodes:
            title = _first_text(node, "title")[:500]
            item_url = _first_text(node, "link")[:2_048]
            raw_date = _first_text(node, "pubdate")
            published_at = _published_at(raw_date)
            if not title or not item_url or published_at is None \
                    or not query.start.astimezone(timezone.utc) <= published_at \
                    <= query.end.astimezone(timezone.utc):
                continue
            score = _relevance_score(title, query)
            if score == 0:
                continue
            try:
                parsed_url = urlsplit(item_url)
                port = parsed_url.port
            except ValueError:
                continue
            if parsed_url.scheme.lower() != "https" or not parsed_url.hostname \
                    or parsed_url.username is not None or parsed_url.password is not None \
                    or port not in (None, 443):
                continue
            record = {
                "upstream_item_id": item_url,
                "request_url": _FEED_URL,
                "item_url": item_url,
                "title": title,
                "text": title,
                "published_at": published_at,
                "effective_at": None,
                "security_ids": security_ids(query.symbols),
                "metadata": {
                    "feed_kind": "gdelt_article_list",
                    "feed_window": "rolling_15_minutes",
                } | publisher_reference(item_url) | ({
                    "dynamic_theme_label": label,
                    "dynamic_theme_origin": "source_title_prefix",
                } if (label := source_dynamic_theme_label(title)) else {}) | ({
                    "syndication_fingerprint": fingerprint,
                    "syndication_origin": "normalized_source_headline",
                } if (fingerprint := source_syndication_fingerprint(title)) else {}),
            }
            ranked_records.append((
                -score, -published_at.timestamp(), title.casefold(), item_url, record,
            ))
        ranked_records.sort(key=lambda value: value[:4])
        return [value[4] for value in ranked_records]

    def _progress_metadata(self, payload, query, response, records, bound):
        return MappingProxyType({
            "truncated": len(records) > bound,
            "backlog_remaining": False,
            "continuation_unavailable": True,
            "coverage_gap": True,
            "sampled_output": True,
            "source_window": "rolling_15_minutes",
        })
