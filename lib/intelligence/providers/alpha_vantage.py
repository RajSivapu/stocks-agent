from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest, SourceFailure

from . import CollectionQuery, SourceAdapter, publisher_reference, security_ids


class AlphaVantageAdapter(SourceAdapter):
    provider = "alpha_vantage"
    allowed_hosts = frozenset({"www.alphavantage.co"})
    authority = "secondary"
    max_items_per_request = 50

    def _query_parameters(self, query: CollectionQuery) -> dict[str, object]:
        params = {
            "function": "NEWS_SENTIMENT",
            "time_from": query.start.strftime("%Y%m%dT%H%M"),
            "time_to": query.end.strftime("%Y%m%dT%H%M"),
            "limit": min(query.limit, self.max_items_per_request),
        }
        if query.symbols:
            params["tickers"] = ",".join(query.symbols)
        else:
            params["topics"] = query.text
        return params

    def _evidence_url(self, query: CollectionQuery) -> str:
        return f"https://www.alphavantage.co/query?{urlencode(self._query_parameters(query))}"

    def _request(self, query: CollectionQuery) -> HttpRequest:
        try:
            key = self.secret_getter("alphavantage_api_key")
        except (KeyError, OSError, ValueError):
            raise SourceFailure("CONFIGURATION_MISSING") from None
        if not isinstance(key, str) or not key.strip():
            raise SourceFailure("CONFIGURATION_MISSING")
        params = urlencode(self._query_parameters(query) | {"apikey": key})
        return HttpRequest(f"https://www.alphavantage.co/query?{params}")

    def _records(self, payload, query, response):
        feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            raise ValueError("invalid Alpha Vantage response")
        records = []
        for article in feed:
            if not isinstance(article, dict):
                continue
            tickers = security_ids([
                row.get("ticker") for row in article.get("ticker_sentiment", [])
                if isinstance(row, dict)
            ]) or security_ids(query.symbols)
            records.append({
                "upstream_item_id": article.get("url"), "request_url": self._evidence_url(query),
                "item_url": article.get("url") or self._evidence_url(query),
                "title": article.get("title"), "text": article.get("summary"),
                "published_at": article.get("time_published"), "effective_at": None,
                "security_ids": tickers,
                "metadata": {"source": article.get("source")} | publisher_reference(article.get("url")),
            })
        return records
