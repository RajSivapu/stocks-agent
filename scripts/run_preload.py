"""One-time historical backfill: per-watchlist seasonality / volatility / drawdown +
notable big-move days -> stock_observations.

Idempotent per ticker: if a ticker already has a yfinance-preload observation, it is skipped,
so re-running is safe (e.g. after adding new names to the watchlist).

Best-effort catalyst labeling: notable moves landing within ~3 calendar days of a known
Finnhub earnings date are tagged 'earnings-reaction'; the rest stay 'big-move'. Earnings
lookup is wrapped in try/except so a Finnhub gap never aborts the preload.

Run via:  .venv/bin/python scripts/run_preload.py   (local)
          python scripts/run_preload.py             (cloud Routine)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import time, datetime
from lib import config, db, preload, fundamentals

db.init_schema()

wl = config.load_watchlist()
names = sum([wl.get(b, []) for b in ("core", "growth", "speculative")], [])


def _already_preloaded(sym):
    return any((o.get("source") or "").startswith("yfinance-preload") for o in db.get_observations(sym))


def _earnings_dates(sym):
    """Set of date strings (YYYY-MM-DD) of known earnings reports; best-effort."""
    try:
        out = set()
        for e in fundamentals.earnings_dates(sym):
            d = e.get("date")
            if d:
                out.add(d)
        return out
    except Exception:
        return set()


def _within_days(d1, d2, days=3):
    try:
        a = datetime.date.fromisoformat(d1); b = datetime.date.fromisoformat(d2)
        return abs((a - b).days) <= days
    except Exception:
        return False


preloaded = skipped = 0
for sym in names:
    if _already_preloaded(sym):
        print("skip (already preloaded)", sym); skipped += 1
        continue
    try:
        yf_sym = sym.replace(".", "-")  # Yahoo uses BRK-B, not BRK.B
        dated = preload.dated_history(yf_sym)
        closes = [c for _, c in dated]
        if not closes:
            print("skip (no data)", sym); skipped += 1
            continue
        db.insert_observation({
            "ticker": sym, "obs_date": time.strftime("%Y-%m-%d"), "event_type": "stats",
            "summary": f"vol={preload.volatility(closes)} mdd={preload.max_drawdown(closes)} "
                       f"seasonality={preload.seasonality(dated)}",
            "price_reaction": None, "confidence": "high", "source": "yfinance-preload"})
        earns = _earnings_dates(yf_sym)
        for mv in preload.notable_moves(dated)[:20]:
            is_earn = any(_within_days(mv["date"], e) for e in earns)
            db.insert_observation({
                "ticker": sym, "obs_date": mv["date"],
                "event_type": "earnings-reaction" if is_earn else "big-move",
                "summary": f"{mv['change_pct']}% day move" + (" near earnings" if is_earn else ""),
                "price_reaction": str(mv["change_pct"]),
                "confidence": "medium", "source": "yfinance-preload"})
        print("preloaded", sym, f"({len(closes)} closes, {len(preload.notable_moves(dated))} notable)")
        preloaded += 1
        time.sleep(0.3)
    except Exception as e:
        print("skip", sym, f"{type(e).__name__}"); skipped += 1

print(f"preload complete: {preloaded} preloaded, {skipped} skipped")
