from lib import preload as p


def test_max_drawdown():
    assert p.max_drawdown([100, 120, 60, 90]) == round((60 - 120) / 120, 4)


def test_volatility_flat():
    assert p.volatility([100, 100, 100]) == 0.0


def test_notable_moves():
    assert p.notable_moves([(0, 100), (86400, 112)])[0]["change_pct"] == 12.0


def test_notable_moves_below_threshold_empty():
    assert p.notable_moves([(0, 100), (86400, 103)]) == []


def test_seasonality_returns_months():
    # Two consecutive January days (ts 0 = 1970-01-01) -> month 1 present.
    out = p.seasonality([(0, 100), (86400, 110)])
    assert out.get(1) == round(10.0 / 1, 3)
