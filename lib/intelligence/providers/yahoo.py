import json
from urllib.parse import quote

from lib import marketdata
from lib.intelligence.http import HttpRequest, SourceFailure

from . import CollectionQuery, SourceAdapter, security_ids


class YahooAdapter(SourceAdapter):
    provider = "yahoo"
    allowed_hosts = frozenset({"query1.finance.yahoo.com"})
    authority = "market_data"
    max_items_per_request = 1

    def _request(self, query: CollectionQuery) -> HttpRequest:
        symbols = security_ids(query.symbols)
        if not symbols:
            raise SourceFailure("UNSUPPORTED_QUERY")
        symbol = symbols[0]
        return HttpRequest(
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}?range=5d&interval=1d"
        )

    def _records(self, payload, query, response):
        quote_data = marketdata.parse_quote_payload(payload)
        symbol = query.symbols[0] if query.symbols else query.text
        if quote_data.get("price") is None or quote_data.get("as_of") is None:
            raise ValueError("invalid Yahoo quote")
        return [{
            "upstream_item_id": f"{symbol}:{quote_data['as_of']}",
            "source_url": (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                f"{quote(symbol, safe='')}?range=5d&interval=1d"
            ),
            "request_url": (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                f"{quote(symbol, safe='')}?range=5d&interval=1d"
            ),
            "title": f"{symbol} market quote",
            "text": json.dumps(quote_data, separators=(",", ":"), sort_keys=True),
            "published_at": quote_data["as_of"],
            "effective_at": quote_data["as_of"],
            "metadata": quote_data,
            "security_ids": (symbol,),
        }]
