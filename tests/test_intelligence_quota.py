import pytest

from lib.intelligence.quota import QuotaExceeded, QuotaSession


def test_quota_consumption_cannot_exceed_reserved_slots():
    session = QuotaSession({"alpha_vantage": (
        {"reservation_id": "r1", "reserved_requests": 2},
    )})
    session.consume("alpha_vantage", "r1")
    session.consume("alpha_vantage", "r1")

    with pytest.raises(QuotaExceeded, match="alpha_vantage"):
        session.consume_next("alpha_vantage")


def test_quota_rejects_unknown_cross_provider_and_duplicate_reservations():
    session = QuotaSession({
        "gdelt": ({"reservation_id": "g1", "reserved_requests": 1},),
        "finnhub": ({"reservation_id": "f1", "reserved_requests": 1},),
    })

    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "unknown")
    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "f1")

    session.consume("gdelt", "g1")
    with pytest.raises(QuotaExceeded):
        session.consume("gdelt", "g1")


def test_consume_next_uses_gateway_order_and_each_reserved_request_exactly_once():
    session = QuotaSession({"gdelt": (
        {"reservation_id": "g2", "reserved_requests": 2},
        {"reservation_id": "g1", "reserved_requests": 1},
    )})

    assert session.consume_next("gdelt") == "g2"
    assert session.consume_next("gdelt") == "g2"
    assert session.consume_next("gdelt") == "g1"
    with pytest.raises(QuotaExceeded):
        session.consume_next("gdelt")


def test_duplicate_ids_in_gateway_reservations_fail_closed():
    with pytest.raises(ValueError, match="duplicate"):
        QuotaSession({"gdelt": (
            {"reservation_id": "g1", "reserved_requests": 1},
            {"reservation_id": "g1", "reserved_requests": 2},
        )})


@pytest.mark.parametrize("reserved_requests", [0, -1, True, "2"])
def test_invalid_gateway_reservation_capacity_fails_closed(reserved_requests):
    with pytest.raises(ValueError, match="invalid quota reservation"):
        QuotaSession({"gdelt": ({
            "reservation_id": "g1",
            "reserved_requests": reserved_requests,
        },)})
