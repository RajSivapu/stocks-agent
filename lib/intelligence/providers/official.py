import re
from urllib.parse import quote, urlencode

from lib.edgar import parse_submissions
from lib.intelligence.http import HttpRequest, SourceFailure

from . import CollectionQuery, SourceAdapter, entity_ids, security_ids


class OfficialAdapter(SourceAdapter):
    authority = "official"
    max_items_per_request = 50
    endpoint = ""

    def _request_reference(self, query: CollectionQuery) -> str:
        if self.provider == "sec_edgar":
            return f"https://data.sec.gov/submissions/CIK{str(query.cik).zfill(10)}.json"
        if self.provider == "fred":
            return f"{self.endpoint}?{urlencode({
                'series_id': query.series_id, 'file_type': 'json',
                'observation_start': query.start.date().isoformat(),
                'observation_end': query.end.date().isoformat(),
                'limit': min(query.limit, self.max_items_per_request),
            })}"
        return f"{self.endpoint}?{urlencode({
            'query': query.text, 'limit': min(query.limit, self.max_items_per_request),
            'from': query.start.date().isoformat(), 'to': query.end.date().isoformat(),
        })}"

    def _request(self, query: CollectionQuery) -> HttpRequest:
        if self.provider == "sec_edgar":
            if (
                not isinstance(query.cik, str)
                or re.fullmatch(r"[0-9]{1,10}", query.cik) is None
                or int(query.cik) == 0
            ):
                raise SourceFailure("INVALID_QUERY")
            identifier = query.cik.zfill(10)
            return HttpRequest(f"https://data.sec.gov/submissions/CIK{quote(identifier)}.json")
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
            return HttpRequest(f"{self.endpoint}?{params}")
        if self.provider in {"white_house", "doe", "dod", "eia", "bls", "bea"}:
            # The approved provider exists, but abstract themes do not identify a
            # documented free endpoint/series. Do not spend quota guessing one.
            raise SourceFailure("UNSUPPORTED_QUERY")
        params = urlencode({
            "query": query.text,
            "limit": min(query.limit, self.max_items_per_request),
            "from": query.start.date().isoformat(),
            "to": query.end.date().isoformat(),
        })
        return HttpRequest(f"{self.endpoint}?{params}")

    def _records(self, payload, query, response):
        if self.provider == "sec_edgar":
            records = parse_submissions(payload, min(query.limit, self.max_items_per_request))
            return [{
                **record,
                "request_url": self._request_reference(query),
                "item_url": record["source_url"],
                "reporting_at": record.get("effective_at"),
                "entity_ids": entity_ids((f"cik:{str(query.cik).zfill(10)}",)),
                "security_ids": security_ids(query.symbols),
            } for record in records]
        if self.provider == "federal_register":
            return self._with_query_identity(self._federal_register(payload), query, self._request_reference(query))
        if self.provider == "fred":
            return self._fred(payload, query, self._request_reference(query))
        return self._with_query_identity(
            self._generic(payload, self._request_reference(query)), query, self._request_reference(query)
        )

    @staticmethod
    def _with_query_identity(records, query, request_url):
        return [{
            **record,
            "request_url": request_url,
            "item_url": record.get("source_url") or request_url,
            "security_ids": security_ids(query.symbols),
        } for record in records]

    @staticmethod
    def _federal_register(payload):
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("invalid Federal Register response")
        return [{
            "upstream_item_id": item.get("document_number"),
            "source_url": item.get("html_url"),
            "title": item.get("title"),
            "text": item.get("abstract"),
            "published_at": item.get("publication_date"),
            "effective_at": item.get("effective_on"),
            "metadata": {"document_number": item.get("document_number")},
        } for item in results if isinstance(item, dict)]

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
            "entity_ids": entity_ids((f"series:{series_id}",)),
            "metadata": {"series_id": series_id, "value": item.get("value")},
        } for item in observations if isinstance(item, dict)]

    def _generic(self, payload, request_url):
        if not isinstance(payload, dict):
            raise ValueError("invalid official source response")
        raw = payload.get("results", payload.get("items", payload.get("data")))
        if isinstance(raw, dict):
            raw = raw.get("items", raw.get("results", raw.get("series")))
        if not isinstance(raw, list):
            raise ValueError("invalid official source response")
        records = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            records.append({
                "upstream_item_id": item.get("id") or item.get("document_number"),
                "source_url": item.get("url") or item.get("html_url") or request_url,
                "title": item.get("title") or item.get("name") or f"{self.provider} release",
                "text": item.get("summary") or item.get("description") or item.get("value"),
                "published_at": item.get("published_at") or item.get("publication_date") or item.get("release_date"),
                "effective_at": item.get("effective_at") or item.get("effective_date") or item.get("observation_date"),
                "metadata": {},
            })
        return records


class SecEdgarAdapter(OfficialAdapter):
    provider = "sec_edgar"
    allowed_hosts = frozenset({"www.sec.gov", "data.sec.gov"})


class FederalRegisterAdapter(OfficialAdapter):
    provider = "federal_register"
    allowed_hosts = frozenset({"www.federalregister.gov"})
    endpoint = "https://www.federalregister.gov/api/v1/documents.json"


class WhiteHouseAdapter(OfficialAdapter):
    provider = "white_house"
    allowed_hosts = frozenset({"www.whitehouse.gov"})
    endpoint = "https://www.whitehouse.gov/wp-json/wp/v2/search"


class DoeAdapter(OfficialAdapter):
    provider = "doe"
    allowed_hosts = frozenset({"www.energy.gov"})
    endpoint = "https://www.energy.gov/api/search"


class DodAdapter(OfficialAdapter):
    provider = "dod"
    allowed_hosts = frozenset({"www.defense.gov"})
    endpoint = "https://www.defense.gov/News/Releases"


class EiaAdapter(OfficialAdapter):
    provider = "eia"
    allowed_hosts = frozenset({"api.eia.gov", "www.eia.gov"})
    endpoint = "https://api.eia.gov/v2/"


class FredAdapter(OfficialAdapter):
    provider = "fred"
    allowed_hosts = frozenset({"api.stlouisfed.org", "fred.stlouisfed.org"})
    endpoint = "https://api.stlouisfed.org/fred/series/observations"


class BlsAdapter(OfficialAdapter):
    provider = "bls"
    allowed_hosts = frozenset({"api.bls.gov", "www.bls.gov"})
    endpoint = "https://api.bls.gov/publicAPI/v2/timeseries/data"


class BeaAdapter(OfficialAdapter):
    provider = "bea"
    allowed_hosts = frozenset({"apps.bea.gov", "www.bea.gov"})
    endpoint = "https://apps.bea.gov/api/data"


OFFICIAL_ADAPTERS = {
    adapter.provider: adapter
    for adapter in (
        SecEdgarAdapter, FederalRegisterAdapter, WhiteHouseAdapter, DoeAdapter,
        DodAdapter, EiaAdapter, FredAdapter, BlsAdapter, BeaAdapter,
    )
}
