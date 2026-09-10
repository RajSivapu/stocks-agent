"""Reviewed official JSON and Defense RSS adapters."""

from __future__ import annotations

import re
from types import MappingProxyType
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from lib.intelligence.http import HttpRequest, SourceFailure
from lib.intelligence.limits import maximum_collection_page

from . import CollectionQuery, SourceAdapter
from .energy import DoeAdapter, EiaAdapter
from .rss import OfficialFeedAdapter
from .sec import SecEdgarAdapter
from .white_house import WhiteHouseAdapter


DEFENSE_RELEASES_URL = (
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx"
    "?ContentType=9&Site=945&max=10"
)
DEFENSE_NEWS_URL = (
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx"
    "?ContentType=1&Site=945&max=10"
)


class DefenseAdapter(OfficialFeedAdapter):
    provider = "dod"
    authority = "official_defense_statement"
    allowed_hosts = frozenset({"www.defense.gov", "www.war.gov"})
    feed_routes = MappingProxyType({
        "defense_releases_rss": DEFENSE_RELEASES_URL,
        "defense_news_rss": DEFENSE_NEWS_URL,
    })
    response_routes = MappingProxyType({
        capability_id: frozenset({source})
        for capability_id, source in feed_routes.items()
    })
    item_path_patterns = MappingProxyType({
        "defense_releases_rss": (re.compile(r"/News/(?:Releases|Contracts)/.+", re.IGNORECASE),),
        "defense_news_rss": (re.compile(r"/News/(?:News-Stories|Features|Releases)/.+", re.IGNORECASE),),
    })

    def _authority(self, query: CollectionQuery) -> str:
        if query.capability_id == "defense_news_rss":
            return "official_defense_news"
        return self.authority


class OfficialJsonAdapter(SourceAdapter):
    authority = "official"
    max_items_per_request = 50
    endpoint = ""

    def _request_reference(self, query: CollectionQuery) -> str:
        if self.provider == "fred":
            params = urlencode({
                'series_id': query.series_id, 'file_type': 'json',
                'observation_start': query.start.date().isoformat(),
                'observation_end': query.end.date().isoformat(),
                'limit': min(query.limit, self.max_items_per_request),
            })
            return f"{self.endpoint}?{params}"
        if self.provider == "federal_register":
            params = {
                "conditions[term]": " ".join((query.text, *query.symbols)).strip(),
                "conditions[publication_date][gte]": query.start.date().isoformat(),
                "conditions[publication_date][lte]": query.end.date().isoformat(),
                "per_page": min(query.limit, self.max_items_per_request),
                "order": "newest",
            }
            if query.cursor_token is not None:
                page_match = re.fullmatch(r"page:([0-9]{1,3})", query.cursor_token)
                if page_match is not None:
                    page = int(page_match.group(1))
                    if (
                        page != query.page
                        or not 2 <= page <= maximum_collection_page(query.capability_id)
                    ):
                        raise SourceFailure("INVALID_QUERY")
                    params["page"] = page
                elif re.fullmatch(r"[A-Za-z0-9._~:-]{1,512}", query.cursor_token) is None:
                    raise SourceFailure("INVALID_QUERY")
                else:
                    params["search_after"] = query.cursor_token
                endpoint = "https://www.federalregister.gov/api/v1/documents"
            else:
                endpoint = self.endpoint
            return f"{endpoint}?{urlencode(params)}"
        params = urlencode({
            'query': query.text,
            'limit': min(query.limit, self.max_items_per_request),
            'from': query.start.date().isoformat(),
            'to': query.end.date().isoformat(),
        })
        return f"{self.endpoint}?{params}"

    def _request(self, query: CollectionQuery) -> HttpRequest:
        if self.provider == "fred":
            try:
                key = self.secret_getter("fred_api_key")
            except (KeyError, OSError, ValueError):
                raise SourceFailure("CONFIGURATION_MISSING") from None
            if not isinstance(key, str) or not key.strip():
                raise SourceFailure("CONFIGURATION_MISSING")
            if not isinstance(query.series_id, str) or re.fullmatch(
                r"[A-Za-z0-9._-]{1,120}", query.series_id,
            ) is None:
                raise SourceFailure("INVALID_QUERY")
            params = urlencode({
                "series_id": query.series_id,
                "api_key": key,
                "file_type": "json",
                "observation_start": query.start.date().isoformat(),
                "observation_end": query.end.date().isoformat(),
                "limit": min(query.limit, self.max_items_per_request),
            })
            return HttpRequest(f"{self.endpoint}?{params}", expected_document="json")
        if self.provider in {"bls", "bea"}:
            raise SourceFailure("UNSUPPORTED_QUERY")
        if self.provider == "federal_register":
            return HttpRequest(
                self._request_reference(query),
                expected_document="json",
                allowed_redirect_urls=frozenset(),
            )
        raise SourceFailure("UNSUPPORTED_QUERY")

    def _records(self, payload, query, response):
        if self.provider == "federal_register":
            return self._federal_register(payload, query, self._request_reference(query))
        if self.provider == "fred":
            return self._fred(payload, query, self._request_reference(query))
        raise SourceFailure("UNSUPPORTED_QUERY")

    @staticmethod
    def _federal_register(payload, query, request_url):
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("invalid Federal Register response")
        records = []
        for item in results:
            if not isinstance(item, dict):
                continue
            document_number = item.get("document_number")
            item_url = item.get("html_url")
            if not isinstance(document_number, str) or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", document_number
            ) is None or not isinstance(item_url, str):
                continue
            try:
                parsed_item_url = urlsplit(item_url)
                port = parsed_item_url.port
            except ValueError:
                continue
            if (
                parsed_item_url.scheme != "https"
                or parsed_item_url.hostname != "www.federalregister.gov"
                or parsed_item_url.username is not None
                or parsed_item_url.password is not None
                or port not in (None, 443)
                or parsed_item_url.query
                or parsed_item_url.fragment
                or re.fullmatch(
                    rf"/documents/[0-9]{{4}}/[0-9]{{2}}/[0-9]{{2}}/{re.escape(document_number)}/[A-Za-z0-9._~%-]+",
                    parsed_item_url.path,
                ) is None
            ):
                continue
            records.append({
                "upstream_item_id": document_number,
                "request_url": request_url,
                "item_url": item_url,
                "title": item.get("title"),
                "text": item.get("abstract"),
                "published_at": item.get("publication_date"),
                "effective_at": item.get("effective_on"),
                "metadata": {
                    "document_number": item.get("document_number"),
                    "document_status": item.get("type") or item.get("document_type") or "unknown",
                },
            })
        return records

    @staticmethod
    def _next_cursor(payload, query) -> tuple[str | None, bool]:
        next_page_url = payload.get("next_page_url") if isinstance(payload, dict) else None
        if next_page_url in (None, ""):
            return None, False
        if not isinstance(next_page_url, str) or len(next_page_url) > 2_048:
            raise SourceFailure("INVALID_RESPONSE")
        try:
            parsed = urlsplit(next_page_url)
            params = parse_qs(parsed.query, keep_blank_values=True)
        except ValueError:
            raise SourceFailure("INVALID_RESPONSE") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.federalregister.gov"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in {"/api/v1/documents", "/api/v1/documents.json"}
        ):
            raise SourceFailure("INVALID_RESPONSE")
        search_after = params.get("search_after", ())
        pages = params.get("page", ())
        maximum_page = maximum_collection_page(query.capability_id)
        if len(search_after) == 1 and not pages:
            cursor = search_after[0]
            continuation_beyond_cap = query.page == maximum_page
        elif len(pages) == 1 and not search_after and re.fullmatch(r"[0-9]{1,3}", pages[0]):
            page = int(pages[0])
            if page != query.page + 1 or page > maximum_page + 1:
                raise SourceFailure("INVALID_RESPONSE")
            cursor = f"page:{page}"
            continuation_beyond_cap = page > maximum_page
        else:
            raise SourceFailure("INVALID_RESPONSE")
        if re.fullmatch(r"[A-Za-z0-9._~:-]{1,512}", cursor) is None:
            raise SourceFailure("INVALID_RESPONSE")
        return cursor, continuation_beyond_cap

    def _progress_metadata(self, payload, query, response, records, bound):
        if self.provider != "federal_register":
            return super()._progress_metadata(payload, query, response, records, bound)
        cursor, continuation_beyond_cap = self._next_cursor(payload, query)
        if cursor is not None and cursor == query.cursor_token:
            raise SourceFailure("INVALID_RESPONSE")
        backlog_token = None if continuation_beyond_cap else cursor
        return MappingProxyType({
            "truncated": cursor is not None,
            "backlog_remaining": backlog_token is not None,
            **({"backlog_token": backlog_token} if backlog_token else {}),
            **({
                "continuation_unavailable": True,
                "coverage_gap": True,
            } if continuation_beyond_cap else {}),
        })

    @staticmethod
    def _fred(payload, query, request_url):
        observations = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(observations, list):
            raise ValueError("invalid FRED response")
        series_id = query.series_id
        return [{
            "upstream_item_id": f"{series_id}:{item.get('date')}",
            "source_url": f"https://fred.stlouisfed.org/series/{quote(series_id)}",
            "request_url": request_url,
            "item_url": f"https://fred.stlouisfed.org/series/{quote(series_id)}",
            "title": f"FRED {series_id} observation",
            "text": f"{item.get('date')}: {item.get('value')}",
            "published_at": item.get("realtime_start") or payload.get("realtime_start"),
            "effective_at": item.get("date"),
            "reporting_at": item.get("date"),
            "entity_ids": (f"series:{series_id}",),
            "metadata": {"series_id": series_id, "value": item.get("value")},
        } for item in observations if isinstance(item, dict)]


class FederalRegisterAdapter(OfficialJsonAdapter):
    provider = "federal_register"
    authority = "official_policy_record"
    allowed_hosts = frozenset({"www.federalregister.gov"})
    endpoint = "https://www.federalregister.gov/api/v1/documents.json"


class FredAdapter(OfficialJsonAdapter):
    provider = "fred"
    allowed_hosts = frozenset({"api.stlouisfed.org", "fred.stlouisfed.org"})
    endpoint = "https://api.stlouisfed.org/fred/series/observations"


class BlsAdapter(OfficialJsonAdapter):
    provider = "bls"
    allowed_hosts = frozenset({"api.bls.gov", "www.bls.gov"})
    endpoint = "https://api.bls.gov/publicAPI/v2/timeseries/data"


class BeaAdapter(OfficialJsonAdapter):
    provider = "bea"
    allowed_hosts = frozenset({"apps.bea.gov", "www.bea.gov"})
    endpoint = "https://apps.bea.gov/api/data"


OFFICIAL_ADAPTERS = {
    adapter.provider: adapter
    for adapter in (
        SecEdgarAdapter,
        FederalRegisterAdapter,
        WhiteHouseAdapter,
        DoeAdapter,
        DefenseAdapter,
        EiaAdapter,
        FredAdapter,
        BlsAdapter,
        BeaAdapter,
    )
}


__all__ = [
    "DEFENSE_NEWS_URL",
    "DEFENSE_RELEASES_URL",
    "OFFICIAL_ADAPTERS",
    "DefenseAdapter",
    "FederalRegisterAdapter",
]
