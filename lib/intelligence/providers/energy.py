"""Reviewed DOE and EIA zero-cost source routes."""

from __future__ import annotations

import json
import re
from types import MappingProxyType
from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest, HttpResult, SourceFailure

from . import CollectionQuery
from .rss import OfficialFeedAdapter


DOE_ENERGY_NEWS_URL = "https://www.energy.gov/rss/energygov/2193718"
EIA_TODAY_IN_ENERGY_URL = "https://www.eia.gov/rss/todayinenergy.xml"
EIA_PRESS_RELEASES_URL = "https://www.eia.gov/rss/press_rss.xml"
_EIA_STATISTICS_ROUTES = MappingProxyType({
    "electricity/rto/region-data": "/v2/electricity/rto/region-data/data/",
})


class DoeAdapter(OfficialFeedAdapter):
    provider = "doe"
    authority = "official_agency_statement"
    allowed_hosts = frozenset({"www.energy.gov"})
    feed_routes = MappingProxyType({"doe_energy_news_rss": DOE_ENERGY_NEWS_URL})
    response_routes = MappingProxyType({
        "doe_energy_news_rss": frozenset({DOE_ENERGY_NEWS_URL}),
    })
    allow_text_html_xml = True
    item_path_patterns = MappingProxyType({
        "doe_energy_news_rss": (re.compile(r"/articles/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+"),),
    })


class EiaAdapter(OfficialFeedAdapter):
    provider = "eia"
    authority = "official_energy_context"
    allowed_hosts = frozenset({"www.eia.gov", "api.eia.gov"})
    feed_routes = MappingProxyType({
        "eia_today_in_energy_rss": EIA_TODAY_IN_ENERGY_URL,
        "eia_press_releases_rss": EIA_PRESS_RELEASES_URL,
    })
    response_routes = MappingProxyType({
        "eia_today_in_energy_rss": frozenset({EIA_TODAY_IN_ENERGY_URL}),
        "eia_press_releases_rss": frozenset({EIA_PRESS_RELEASES_URL}),
    })
    item_path_patterns = MappingProxyType({
        # Both official EIA feeds can cross-link approved Today in Energy and
        # press-room records.  Validate the evidence route, while retaining
        # those two reviewed publication namespaces for either feed.
        "eia_today_in_energy_rss": (
            re.compile(r"/todayinenergy/detail\.php"),
            re.compile(r"/pressroom/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+"),
        ),
        "eia_press_releases_rss": (
            re.compile(r"/todayinenergy/detail\.php"),
            re.compile(r"/pressroom/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+"),
        ),
    })

    def _authority(self, query: CollectionQuery) -> str:
        if query.capability_id == "eia_statistics_v2":
            return "official_energy_statistics"
        return self.authority

    def _statistics_path(self, query: CollectionQuery) -> str:
        if query.capability_id != "eia_statistics_v2":
            raise SourceFailure("UNSUPPORTED_QUERY")
        if not isinstance(query.series_id, str):
            raise SourceFailure("INVALID_QUERY")
        try:
            return _EIA_STATISTICS_ROUTES[query.series_id]
        except KeyError:
            raise SourceFailure("CONFIGURATION_MISSING") from None

    def _statistics_key(self) -> str:
        try:
            value = self.secret_getter("eia_api_key")
        except (KeyError, OSError, ValueError):
            raise SourceFailure("CONFIGURATION_MISSING") from None
        if not isinstance(value, str) or not value.strip():
            raise SourceFailure("CONFIGURATION_MISSING")
        return value.strip()

    def _request(self, query: CollectionQuery) -> HttpRequest:
        if query.capability_id != "eia_statistics_v2":
            return super()._request(query)
        path = self._statistics_path(query)
        key = self._statistics_key()
        params = urlencode({
            "api_key": key,
            "start": query.start.date().isoformat(),
            "end": query.end.date().isoformat(),
            "length": min(query.limit, self.max_items_per_request),
        })
        return HttpRequest(
            f"https://api.eia.gov{path}?{params}",
            max_bytes=1_000_000,
            expected_document="json",
            allowed_redirect_urls=frozenset(),
        )

    def _decode_response(self, response: HttpResult) -> object:
        if response.url.startswith("https://api.eia.gov/"):
            try:
                return json.loads(response.body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise SourceFailure("INVALID_RESPONSE") from None
        return super()._decode_response(response)

    def _records(self, payload, query, response):
        if query.capability_id != "eia_statistics_v2":
            return super()._records(payload, query, response)
        path = self._statistics_path(query)
        parsed_response_url = response.url.split("?", 1)[0]
        if parsed_response_url != f"https://api.eia.gov{path}":
            raise SourceFailure("UNSAFE_URL")
        if not isinstance(payload, dict):
            raise SourceFailure("INVALID_RESPONSE")
        response_object = payload.get("response")
        data = response_object.get("data") if isinstance(response_object, dict) else None
        if not isinstance(data, list):
            raise SourceFailure("INVALID_RESPONSE")
        safe_reference = f"https://api.eia.gov{path}"
        records = []
        for index, row in enumerate(data):
            if not isinstance(row, dict):
                continue
            period = row.get("period")
            value = row.get("value")
            if not isinstance(period, str) or re.fullmatch(r"[0-9TZ:+.-]{4,40}", period) is None:
                continue
            records.append({
                "upstream_item_id": f"{query.series_id}:{period}:{index}",
                "request_url": safe_reference,
                "item_url": safe_reference,
                "title": f"EIA {query.series_id} observation",
                "text": f"{period}: {value}",
                "published_at": response.retrieved_at,
                "effective_at": period,
                "reporting_at": period,
                "metadata": {"series_id": query.series_id, "value": value},
            })
        return records


__all__ = [
    "DOE_ENERGY_NEWS_URL",
    "EIA_PRESS_RELEASES_URL",
    "EIA_TODAY_IN_ENERGY_URL",
    "DoeAdapter",
    "EiaAdapter",
]
