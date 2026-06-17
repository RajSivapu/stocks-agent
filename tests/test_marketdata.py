"""Pure-math tests for the local indicators (no network)."""
from lib import marketdata as m


def test_sma():
    assert m._sma([1, 2, 3, 4], 2) == 3.5


def test_rsi_all_gains():
    assert m._rsi(list(range(1, 30))) == 100.0


def test_indicators_short_series_none():
    out = m.indicators([1, 2, 3])
    assert out["sma50"] is None and out["rsi14"] is None


def test_macd_needs_history():
    assert m._macd([1, 2, 3]) is None
