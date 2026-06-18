"""One-time historical preload stats (pure-python, stdlib only).

Fetches dated daily closes from Yahoo (same stdlib-urllib + UA pattern as lib.marketdata)
and computes seasonality / annualized volatility / max drawdown / notable big-move days.
Used by scripts/run_preload.py to seed stock_observations.
"""
import json, ssl, urllib.request, statistics, math, time as _t
ctx = ssl.create_default_context(); UA = {"User-Agent": "Mozilla/5.0"}


def _get(u, t=25):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t, context=ctx).read())


def dated_history(sym, range_="5y"):
    """List of (unix_ts, close) for the given range, skipping null closes."""
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d")
    r = j["chart"]["result"][0]; ts = r["timestamp"]; cl = r["indicators"]["quote"][0]["close"]
    return [(t, c) for t, c in zip(ts, cl) if c is not None]


def volatility(closes):
    """Annualized stdev of daily returns (population stdev * sqrt(252))."""
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    return round(statistics.pstdev(rets) * math.sqrt(252), 4) if len(rets) > 1 else None


def max_drawdown(closes):
    """Worst peak-to-trough decline as a negative fraction (e.g. -0.5 = -50%)."""
    if not closes:
        return None
    peak = closes[0]; mdd = 0.0
    for c in closes:
        peak = max(peak, c); mdd = min(mdd, (c - peak) / peak)
    return round(mdd, 4)


def seasonality(dated):
    """{month_number: avg_daily_return_pct} aggregated across all years in the series."""
    by = {}
    for i in range(1, len(dated)):
        mth = _t.gmtime(dated[i][0]).tm_mon
        by.setdefault(mth, []).append((dated[i][1] - dated[i - 1][1]) / dated[i - 1][1])
    return {m: round(sum(v) / len(v) * 100, 3) for m, v in sorted(by.items())}


def notable_moves(dated, threshold=0.07):
    """Days whose single-day move is >= threshold (abs). Returns [{date, change_pct}]."""
    out = []
    for i in range(1, len(dated)):
        ch = (dated[i][1] - dated[i - 1][1]) / dated[i - 1][1]
        if abs(ch) >= threshold:
            out.append({"date": _t.strftime("%Y-%m-%d", _t.gmtime(dated[i][0])), "change_pct": round(ch * 100, 1)})
    return out
