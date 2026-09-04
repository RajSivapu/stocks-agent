from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest, SourceFailure

from . import CollectionQuery, SourceAdapter


class AlphaVantageAdapter(SourceAdapter):
    provider = "alpha_vantage"
    allowed_hosts = frozenset({"www.alphavantage.co"})
    authority = "secondary"
    max_items_per_request = 50

    def _request(self, query: CollectionQuery) -> HttpRequest:
        try:
            key = self.secret_getter("alphavantage_api_key")
        except (KeyError, OSError, ValueError):
            raise SourceFailure("CONFIGURATION_MISSING") from None
        if not isinstance(key, str) or not key.strip():
            raise SourceFailure("CONFIGURATION_MISSING")
        params = urlencode({
            "function": "NEWS_SENTIMENT",
            "topics": query.text,
            "time_from": query.start.strftime("%Y%m%dT%H%M"),
            "time_to": query.end.strftime("%Y%m%dT%H%M"),
            "limit": min(query.limit, self.max_items_per_request),
            "apikey": key,
        })
        return HttpRequest(f"https://www.alphavantage.co/query?{params}")

    def _records(self, payload, query, response):
        feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            raise ValueError("invalid Alpha Vantage response")
        return [{
            "upstream_item_id": article.get("url"),
            "source_url": article.get("url"),
            "title": article.get("title"),
            "text": article.get("summary"),
            "published_at": article.get("time_published"),
            "effective_at": None,
            "metadata": {"source": article.get("source")},
        } for article in feed if isinstance(article, dict)]
