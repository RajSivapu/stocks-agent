from datetime import timezone
from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest

from . import CollectionQuery, SourceAdapter


class GdeltAdapter(SourceAdapter):
    provider = "gdelt"
    allowed_hosts = frozenset({"api.gdeltproject.org"})
    authority = "radar"
    max_items_per_request = 50

    def _request(self, query: CollectionQuery) -> HttpRequest:
        params = urlencode({
            "query": query.text,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": min(query.limit, self.max_items_per_request),
            "startdatetime": query.start.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "enddatetime": query.end.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
        })
        return HttpRequest(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}")

    def _records(self, payload, query, response):
        articles = payload.get("articles") if isinstance(payload, dict) else None
        if not isinstance(articles, list):
            raise ValueError("invalid GDELT response")
        return [{
            "upstream_item_id": article.get("url"),
            "source_url": article.get("url"),
            "title": article.get("title"),
            "text": article.get("title"),
            "published_at": article.get("seendate"),
            "effective_at": None,
            "metadata": {
                key: article[key]
                for key in ("domain", "language", "sourcecountry") if key in article
            },
        } for article in articles if isinstance(article, dict)]
