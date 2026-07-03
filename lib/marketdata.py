"""Market data via Yahoo (primary) + locally-computed indicators.

Ported from the proven v1.5 dry-run code. All HTTP uses stdlib urllib (no requests).
Indicators (RSI-14, MACD 12/26/9, SMA 50/200) are computed locally so we never depend
on a paid/rate-limited indicator API. Functions return None where there's insufficient
data rather than raising, so callers can mark partials.
"""
import json, ssl, urllib.request
ctx = ssl.create_default_context(); UA = {"User-Agent": "Mozilla/5.0"}


def _get(u, t=20):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t, context=ctx).read())


def history(sym, range_="1y"):
    """Daily closes for `sym` over `range_` (Nones dropped)."""
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d")
    res = j["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]


def quote(sym):
    """Latest price, previous close, and day % move."""
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d")
    m = j["chart"]["result"][0]["meta"]
    px = m.get("regularMarketPrice"); pc = m.get("previousClose") or m.get("chartPreviousClose")
    return {"price": px, "prev_close": pc,
            "day_pct": (round((px - pc) / pc * 100, 2) if px and pc else None)}


def _sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def _ema(v, n):
    k = 2 / (n + 1); e = v[0]; out = [e]
    for x in v[1:]:
        e = x * k + e * (1 - k); out.append(e)
    return out


def _rsi(v, n=14):
    if len(v) < n + 1:
        return None
    g = l = 0.0
    for i in range(-n, 0):
        d = v[i] - v[i - 1]; g += max(d, 0); l += max(-d, 0)
    ag, al = g / n, l / n
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 1)


def _macd(v):
    if len(v) < 35:
        return None
    e12 = _ema(v, 12); e26 = _ema(v, 26)
    line = [a - b for a, b in zip(e12[-len(e26):], e26)]; sig = _ema(line, 9)
    return {"line": round(line[-1], 2), "signal": round(sig[-1], 2), "hist": round(line[-1] - sig[-1], 2)}


def indicators(closes):
    return {"rsi14": _rsi(closes),
            "sma50": round(_sma(closes, 50), 2) if _sma(closes, 50) else None,
            "sma200": round(_sma(closes, 200), 2) if _sma(closes, 200) else None,
            "macd": _macd(closes)}


# NYSE full-day closures. Static and authoritative — a pre-market call can never see
# "today's" bar yet (trading hasn't started), so inferring holidays from live intraday
# state defaulted to "holiday" whenever Yahoo's marketState was ambiguously CLOSED at
# 06:30 CT, even on ordinary trading days — this fired live on the weekday right after
# a real holiday and sent a false "market closed" brief. Update this each December for
# the coming year; if the current year is missing, fail OPEN (assume trading day) —
# missing a real morning brief is worse than one harmless extra run on an unlisted day.
_NYSE_HOLIDAYS = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Washington's Birthday (Presidents' Day)
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth National Independence Day
    "2026-07-03",  # Independence Day (observed; Jul 4 falls on a Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
}


def is_market_holiday(today=None):
    """True if today is a full US market closure (weekday, no trading at all).

    `today` is injectable for tests; defaults to the real date. Checks a static NYSE
    calendar rather than live intraday state — see `_NYSE_HOLIDAYS` above for why.
    """
    import datetime
    if today is None:
        today = datetime.date.today()
    if today.weekday() >= 5:
        return False
    return str(today) in _NYSE_HOLIDAYS
