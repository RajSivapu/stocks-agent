"""Market data via Yahoo (primary) + locally-computed indicators.

Ported from the proven v1.5 dry-run code. All HTTP uses stdlib urllib (no requests).
Indicators (RSI-14, MACD 12/26/9, SMA 50/200) are computed locally so we never depend
on a paid/rate-limited indicator API. Functions return None where there's insufficient
data rather than raising, so callers can mark partials.
"""
import datetime
import json, ssl, urllib.request
from pathlib import Path
ctx = ssl.create_default_context(); UA = {"User-Agent": "Mozilla/5.0"}

_MAX_FUTURE_CLOCK_SKEW_MINUTES = 5


def _get(u, t=20):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t, context=ctx).read())


def history(sym, range_="1y"):
    """Daily closes for `sym` over `range_` (Nones dropped)."""
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d")
    return parse_history_payload(j)


def parse_history_payload(payload):
    """Extract closes from a Yahoo chart payload without changing legacy semantics."""
    res = payload["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]


def quote(sym):
    """Latest quote plus exchange-provided freshness metadata."""
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d")
    return parse_quote_payload(j)


def parse_quote_payload(payload):
    """Normalize Yahoo quote metadata while preserving its exchange timestamp/session."""
    m = payload["chart"]["result"][0]["meta"]
    px = m.get("regularMarketPrice"); pc = m.get("previousClose") or m.get("chartPreviousClose")
    timestamp = m.get("regularMarketTime")
    try:
        as_of = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat() if timestamp is not None else None
    except (OverflowError, OSError, TypeError, ValueError):
        as_of = None
    return {"price": px, "prev_close": pc,
            "day_pct": (round((px - pc) / pc * 100, 2) if px and pc else None),
            "as_of": as_of, "market_state": m.get("marketState"), "source": "yahoo-chart"}


def quote_age_minutes(quote_data, now=None):
    """Age of an exchange-timestamped quote, or None when freshness is unknown."""
    raw = quote_data.get("as_of") if isinstance(quote_data, dict) else None
    if not raw:
        return None
    try:
        as_of = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None

    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    age = (now.astimezone(datetime.timezone.utc) - as_of.astimezone(datetime.timezone.utc)).total_seconds() / 60
    if age < -_MAX_FUTURE_CLOCK_SKEW_MINUTES:
        return None
    return round(max(age, 0.0), 2)


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


_CALENDAR_PATH = Path(__file__).resolve().parents[1] / "config" / "nyse_calendar.json"


def _load_nyse_calendar():
    try:
        document = json.loads(_CALENDAR_PATH.read_text())
        coverage = document["coverage"]
        years = document["years"]
        if document["version"] != 1 or document["exchange"] != "NYSE" \
                or document["timezone"] != "America/New_York" \
                or coverage != {"start_year": 2026, "end_year": 2028} \
                or list(years) != ["2026", "2027", "2028"]:
            raise ValueError
        return document
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("reviewed NYSE calendar is unavailable") from exc


_NYSE_CALENDAR = _load_nyse_calendar()


def nyse_holidays(year):
    """Return the reviewed full-day NYSE closures for ``year`` as a new tuple."""
    if type(year) is not int:
        raise ValueError("year must be an integer")
    row = _NYSE_CALENDAR["years"].get(str(year))
    if row is None:
        raise ValueError("calendar coverage is unavailable")
    return tuple(row["full_day_closures"])


def nyse_early_closes(year):
    """Return reviewed ``(date, local_time)`` early-close pairs for ``year``."""
    if type(year) is not int:
        raise ValueError("year must be an integer")
    row = _NYSE_CALENDAR["years"].get(str(year))
    if row is None:
        raise ValueError("calendar coverage is unavailable")
    return tuple((value["date"], value["local_time"]) for value in row["early_closes"])


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
    return str(today) in nyse_holidays(today.year)
