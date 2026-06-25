"""Tests for lib/fundamentals.py — skipped when no Finnhub key available."""
import os, pytest
from lib import config

pytestmark = pytest.mark.skipif(
    not (os.environ.get("FINNHUB_API_KEY") or
         __import__("pathlib").Path("config/secrets.local.json").exists()),
    reason="no Finnhub API key"
)


def test_insider_sentiment_structure():
    """insider_sentiment returns a list of month-level records, newest first."""
    from lib.fundamentals import insider_sentiment
    rows = insider_sentiment("AAPL", months=3)
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        assert "year" in r and "month" in r
        assert "mspr" in r
        assert "change" in r
        # sorted newest first
        for i in range(len(rows) - 1):
            assert (rows[i]["year"], rows[i]["month"]) >= (rows[i+1]["year"], rows[i+1]["month"])


def test_insider_sentiment_mspr_range():
    """MSPR must be in [-100, 100]."""
    from lib.fundamentals import insider_sentiment
    rows = insider_sentiment("NVDA", months=6)
    for r in rows:
        assert -100 <= r["mspr"] <= 100, f"MSPR out of range: {r}"


def test_macro_symbols_quote():
    """VIX, TNX, and DXY must be fetchable via lib.marketdata.quote."""
    from lib import marketdata
    for sym in ["^VIX", "^TNX", "DX-Y.NYB"]:
        q = marketdata.quote(sym)
        assert "price" in q and q["price"] > 0, f"{sym} returned bad quote: {q}"
        assert "day_pct" in q
