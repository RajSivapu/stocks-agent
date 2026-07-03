"""Pure-math tests for the local indicators (no network)."""
import datetime
from lib import marketdata as m


def test_is_market_holiday_known_holiday():
    assert m.is_market_holiday(datetime.date(2026, 6, 19)) is True  # Juneteenth


def test_is_market_holiday_day_after_holiday_not_flagged():
    # Regression: the weekday right after a Monday holiday must NOT be flagged — this
    # is the exact scenario that produced a false "market closed" brief in production.
    assert m.is_market_holiday(datetime.date(2026, 1, 20)) is False  # Tue after MLK Day


def test_is_market_holiday_friday_holiday_then_monday_after_weekend():
    # A holiday on Friday (Good Friday) must still flag correctly, and the next real
    # trading day (Monday, across the weekend) must NOT be flagged — no adjacency
    # reasoning is involved, so this holds for any day-of-week the holiday falls on.
    assert m.is_market_holiday(datetime.date(2026, 4, 3)) is True   # Good Friday
    assert m.is_market_holiday(datetime.date(2026, 4, 4)) is False  # Saturday (weekend, not a "holiday")
    assert m.is_market_holiday(datetime.date(2026, 4, 6)) is False  # Monday after


def test_is_market_holiday_ordinary_weekday():
    assert m.is_market_holiday(datetime.date(2026, 6, 17)) is False


def test_is_market_holiday_weekend():
    assert m.is_market_holiday(datetime.date(2026, 6, 20)) is False  # Saturday


def test_sma():
    assert m._sma([1, 2, 3, 4], 2) == 3.5


def test_rsi_all_gains():
    assert m._rsi(list(range(1, 30))) == 100.0


def test_indicators_short_series_none():
    out = m.indicators([1, 2, 3])
    assert out["sma50"] is None and out["rsi14"] is None


def test_macd_needs_history():
    assert m._macd([1, 2, 3]) is None
