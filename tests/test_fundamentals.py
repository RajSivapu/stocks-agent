"""Tests for lib/fundamentals.py — skipped when no Finnhub key available."""
import os, pytest
from lib import config

pytestmark = pytest.mark.skipif(
    not (os.environ.get("FINNHUB_API_KEY") or
         __import__("pathlib").Path("config/secrets.local.json").exists()),
    reason="no Finnhub API key"
)


def _finnhub_call(fn, *args, **kwargs):
    """Call a Finnhub function; skip the test on transient network errors (503, timeout)."""
    import urllib.error
    try:
        return fn(*args, **kwargs)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            pytest.skip(f"Finnhub transient error {e.code} — skip")
        raise
    except Exception as e:
        if "timed out" in str(e).lower() or "connection" in str(e).lower():
            pytest.skip(f"Finnhub network error — skip: {e}")
        raise


def test_insider_sentiment_structure():
    """insider_sentiment returns a list of month-level records, newest first."""
    from lib.fundamentals import insider_sentiment
    rows = _finnhub_call(insider_sentiment, "AAPL", months=3)
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        assert "year" in r and "month" in r
        assert "mspr" in r
        assert "change" in r
        for i in range(len(rows) - 1):
            assert (rows[i]["year"], rows[i]["month"]) >= (rows[i+1]["year"], rows[i+1]["month"])


def test_insider_sentiment_mspr_range():
    """MSPR must be in [-100, 100]."""
    from lib.fundamentals import insider_sentiment
    rows = _finnhub_call(insider_sentiment, "NVDA", months=6)
    for r in rows:
        assert -100 <= r["mspr"] <= 100, f"MSPR out of range: {r}"


def test_macro_symbols_quote():
    """VIX, TNX, and DXY must be fetchable via lib.marketdata.quote."""
    from lib import marketdata
    for sym in ["^VIX", "^TNX", "DX-Y.NYB"]:
        q = marketdata.quote(sym)
        assert "price" in q and q["price"] > 0, f"{sym} returned bad quote: {q}"
        assert "day_pct" in q
