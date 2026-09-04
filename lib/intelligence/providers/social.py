from urllib.parse import urlencode

from lib.intelligence.http import HttpRequest

from . import CollectionQuery, SourceAdapter


class SocialAdapter(SourceAdapter):
    provider = "social"
    allowed_hosts = frozenset({"www.reddit.com", "oauth.reddit.com"})
    authority = "hypothesis"
    max_items_per_request = 25

    def _request(self, query: CollectionQuery) -> HttpRequest:
        params = urlencode({"q": query.text, "limit": min(query.limit, self.max_items_per_request)})
        return HttpRequest(f"https://www.reddit.com/search.json?{params}")

    def _records(self, payload, query, response):
        children = payload.get("data", {}).get("children") if isinstance(payload, dict) else None
        if not isinstance(children, list):
            raise ValueError("invalid Reddit response")
        records = []
        for child in children:
            data = child.get("data") if isinstance(child, dict) else None
            if not isinstance(data, dict):
                continue
            permalink = str(data.get("permalink") or "")
            records.append({
                "upstream_item_id": data.get("id"),
                "source_url": f"https://www.reddit.com{permalink}",
                "title": data.get("title"),
                "text": data.get("selftext"),
                "published_at": data.get("created_utc"),
                "effective_at": None,
                "metadata": {"subreddit": data.get("subreddit"), "score": data.get("score")},
            })
        return records
