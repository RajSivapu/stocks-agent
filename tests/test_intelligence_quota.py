import pytest

from lib.intelligence.quota import QuotaExceeded, QuotaSession


def test_quota_consumption_cannot_exceed_reserved_slots():
    session = QuotaSession({"alpha_vantage": ("r1", "r2")})
    session.consume("alpha_vantage", "r1")
    session.consume("alpha_vantage", "r2")

    with pytest.raises(QuotaExceeded, match="alpha_vantage"):
        session.consume_next("alpha_vantage")


def test_quota_rejects_unknown_cross_provider_and_duplicate_reservations():
    session = QuotaSession({"gdelt": ("g1",), "finnhub": ("f1",)})

    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "unknown")
    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "f1")

    session.consume("gdelt", "g1")
    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "g1")


def test_consume_next_uses_gateway_order_and_each_id_exactly_once():
    session = QuotaSession({"gdelt": ("g2", "g1")})

    assert session.consume_next("gdelt") == "g2"
    assert session.consume_next("gdelt") == "g1"
    with pytest.raises(QuotaExceeded):
        session.consume_next("gdelt")


def test_duplicate_ids_in_gateway_reservations_fail_closed():
    with pytest.raises(ValueError, match="duplicate"):
        QuotaSession({"gdelt": ("g1", "g1")})
