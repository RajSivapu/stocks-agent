"""Finnhub fundamentals + news (secondary data source).

SECURITY: the Finnhub key is passed in the ``X-Finnhub-Token`` HEADER, never in the
URL. This matches the healthcheck hardening — it keeps the key out of any URL-bearing
exception/traceback that might be logged to cloud stdout or relayed to Telegram.
All HTTP uses stdlib urllib (no requests).
"""
import json, ssl, time, urllib.request
from lib import config
ctx = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0"}


def _get(u, t=20):
    h = {"X-Finnhub-Token": config.secret("finnhub_api_key"), **UA}
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=t, context=ctx).read())


FIELDS = ["peTTM", "psTTM", "pfcfShareTTM", "revenueGrowthTTMYoy", "epsGrowthTTMYoy",
          "netProfitMarginTTM", "grossMarginTTM", "totalDebt/totalEquityQuarterly",
          "currentEv/freeCashFlowTTM", "52WeekHigh", "52WeekLow"]


def metric(sym):
    """Selected Finnhub stock/metric fields for `sym`."""
    m = _get(f"https://finnhub.io/api/v1/stock/metric?symbol={sym}&metric=all").get("metric", {})
    return {f: m.get(f) for f in FIELDS}


def company_news(sym, days=7):
    """Up to 5 recent company-news items for `sym` over the last `days`."""
    to = time.strftime("%Y-%m-%d")
    frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return _get(f"https://finnhub.io/api/v1/company-news?symbol={sym}&from={frm}&to={to}")[:5]


def market_news():
    """Up to 6 general market-news items."""
    return _get("https://finnhub.io/api/v1/news?category=general")[:6]


def earnings_dates(sym):
    """Upcoming/recent earnings calendar entries for `sym`."""
    j = _get(f"https://finnhub.io/api/v1/calendar/earnings?symbol={sym}")
    return j.get("earningsCalendar", [])


def analyst_recommendations(sym):
    """Latest analyst Buy/Hold/Sell consensus from Finnhub.

    Returns dict with keys: strongBuy, buy, hold, sell, strongSell, period.
    Returns {} if unavailable. Free tier includes this endpoint.
    Use: total_bull = strongBuy+buy; total_bear = sell+strongSell; consensus = bull/(bull+bear+hold).
    """
    try:
        data = _get(f"https://finnhub.io/api/v1/stock/recommendation?symbol={sym}")
        return data[0] if data else {}
    except Exception:
        return {}


def insider_sentiment(sym, months=3):
    """Finnhub insider sentiment (MSPR) for the last `months` months.

    MSPR ranges -100 (pure selling) to +100 (pure buying).
    Returns list of {year, month, change (net shares), mspr} dicts, newest first.
    Strong negative MSPR (<-50) is a bear flag; strong positive (>50) is a mild bull signal.
    Note: individual 10b5-1 scheduled sales are included — treat as directional, not absolute.
    """
    frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - months * 31 * 86400))
    to  = time.strftime("%Y-%m-%d")
    data = _get(f"https://finnhub.io/api/v1/stock/insider-sentiment?symbol={sym}&from={frm}&to={to}")
    rows = data.get("data", [])
    return sorted(rows, key=lambda r: (r.get("year", 0), r.get("month", 0)), reverse=True)
