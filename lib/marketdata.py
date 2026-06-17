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
