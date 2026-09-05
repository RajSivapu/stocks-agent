from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest, SourceFailure

from . import CollectionQuery, SourceAdapter, publisher_reference, security_ids


class FinnhubAdapter(SourceAdapter):
    provider = "finnhub"
    allowed_hosts = frozenset({"finnhub.io"})
    authority = "secondary"
    max_items_per_request = 50

    def _evidence_url(self, query: CollectionQuery) -> str:
        params = urlencode({
            "symbol": query.symbols[0] if query.symbols else query.text,
            "from": query.start.date().isoformat(),
            "to": query.end.date().isoformat(),
        })
        return f"https://finnhub.io/api/v1/company-news?{params}"

    def _request(self, query: CollectionQuery) -> HttpRequest:
        if not security_ids(query.symbols):
            raise SourceFailure("UNSUPPORTED_QUERY")
        try:
            key = self.secret_getter("finnhub_api_key")
        except (KeyError, OSError, ValueError):
            raise SourceFailure("CONFIGURATION_MISSING") from None
        if not isinstance(key, str) or not key.strip():
            raise SourceFailure("CONFIGURATION_MISSING")
        return HttpRequest(
            self._evidence_url(query),
            headers={"X-Finnhub-Token": key},
        )

    def _records(self, payload, query, response):
        if not isinstance(payload, list):
            raise ValueError("invalid Finnhub response")
        return [{
            "upstream_item_id": article.get("id"), "request_url": self._evidence_url(query),
            "item_url": article.get("url") or self._evidence_url(query),
            "title": article.get("headline"), "text": article.get("summary"),
            "published_at": article.get("datetime"), "effective_at": None,
            "security_ids": security_ids(query.symbols),
            "metadata": {"category": article.get("category")} | publisher_reference(article.get("url")),
        } for article in payload if isinstance(article, dict)]
