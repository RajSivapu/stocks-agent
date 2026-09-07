from datetime import timezone
from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest
from lib.intelligence.themes import source_dynamic_theme_label

from . import CollectionQuery, SourceAdapter, publisher_reference, security_ids


class GdeltAdapter(SourceAdapter):
    provider = "gdelt"
    allowed_hosts = frozenset({"api.gdeltproject.org"})
    authority = "radar"
    max_items_per_request = 50

    def _evidence_url(self, query: CollectionQuery) -> str:
        params = urlencode({
            "query": " ".join((query.text, *query.symbols)).strip(),
            "mode": "ArtList",
            "format": "json",
            "maxrecords": min(query.limit, self.max_items_per_request),
            "startdatetime": query.start.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "enddatetime": query.end.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
        })
        return f"https://api.gdeltproject.org/api/v2/doc/doc?{params}"

    def _request(self, query: CollectionQuery) -> HttpRequest:
        return HttpRequest(self._evidence_url(query))

    def _records(self, payload, query, response):
        articles = payload.get("articles") if isinstance(payload, dict) else None
        if not isinstance(articles, list):
            raise ValueError("invalid GDELT response")
        return [{
            "upstream_item_id": article.get("url"),
            "request_url": self._evidence_url(query),
            "item_url": article.get("url") or self._evidence_url(query),
            "title": article.get("title"),
            "text": article.get("title"),
            "published_at": article.get("seendate"),
            "effective_at": None,
            "security_ids": security_ids(query.symbols),
            "metadata": {
                key: article[key]
                for key in ("domain", "language", "sourcecountry") if key in article
            } | publisher_reference(article.get("url")) | ({
                "dynamic_theme_label": label,
                "dynamic_theme_origin": "source_title_prefix",
            } if (label := source_dynamic_theme_label(article.get("title"))) else {}),
        } for article in articles if isinstance(article, dict)]
