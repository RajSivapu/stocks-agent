"""Tests for the owner-reviewed market gateway policy projection."""

from copy import deepcopy

import pytest

from lib.config import load_settings
from lib.policy_config import build_policy_config, validate_policy_config


def test_build_policy_config_uses_reviewed_safety_values():
    policy = build_policy_config(load_settings())

    assert policy["version"] == 1
    assert policy["allocation_bps"] == {
        "core": 7000,
        "growth": 2000,
        "speculative": 1000,
    }
    assert policy["max_position_bps_of_bucket"] == {
        "core": 2500,
        "growth": 2000,
        "speculative": 1000,
    }
    assert policy["max_trade_risk_bps"] == {
        "core": 100,
        "growth": 100,
        "speculative": 50,
    }
    assert policy["min_reward_risk_milli"] == 2000
    assert policy["max_actionable_quote_age_minutes"] == 20
    assert policy["alert_near_bps"] == 400
    assert policy["daily_loss_limit_bps"] == 300
    assert policy["circuit_breaker_consecutive_losses"] == 3
    assert policy["speculative_go_live_bucket_micros"] == "500000000"
    assert policy["monthly_investment_micros"] == "500000000"
    assert policy["broad_core_etfs"] == ["SCHD", "VOO", "VTI", "VXUS"]
    assert policy["self_tuning_enabled"] is False
    assert policy["market_calendar_year"] == 2026
    assert "2026-09-07" in policy["nyse_holidays"]
    assert policy["request_limits"] == {
        "max_body_bytes": 262_144,
        "max_candidates": {
            "pre-market": 80,
            "intraday": 20,
            "post-market": 80,
            "on-demand": 10,
        },
        "max_requests_per_run": 20,
        "max_authenticated_requests_per_hour": 100,
    }


def test_build_policy_config_rejects_allocation_not_equal_to_100_percent():
    settings = deepcopy(load_settings())
    settings["strategy"]["allocation"]["growth"] = 0.30

    with pytest.raises(ValueError, match="allocation must total 10000 bps"):
        build_policy_config(settings)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("risk", "daily_loss_limit_pct"), True, "number, not boolean"),
        (("risk", "circuit_breaker_consecutive_losses"), True, "integer, not boolean"),
        (("risk", "min_reward_to_risk"), 0, "must be positive"),
        (("risk", "alert_near_pct"), -1, "must be positive"),
        (("data", "max_actionable_quote_age_minutes"), 0, "must be positive"),
    ],
)
def test_build_policy_config_rejects_invalid_thresholds(path, value, message):
    settings = deepcopy(load_settings())
    settings[path[0]][path[1]] = value

    with pytest.raises(ValueError, match=message):
        build_policy_config(settings)


@pytest.mark.parametrize(
    "missing_key",
    ["always_require_stop_loss", "max_trade_risk_pct_of_portfolio"],
)
def test_build_policy_config_rejects_absent_stop_or_risk_settings(missing_key):
    settings = deepcopy(load_settings())
    del settings["risk"][missing_key]

    with pytest.raises(ValueError, match=missing_key):
        build_policy_config(settings)


def test_build_policy_config_requires_stop_losses():
    settings = deepcopy(load_settings())
    settings["risk"]["always_require_stop_loss"] = False

    with pytest.raises(ValueError, match="always_require_stop_loss must be true"):
        build_policy_config(settings)


def test_build_policy_config_rejects_unknown_or_duplicate_etfs():
    settings = deepcopy(load_settings())
    settings["risk"]["broad_core_etfs"] = ["VTI", "ARKK"]
    with pytest.raises(ValueError, match="unknown broad core ETF"):
        build_policy_config(settings)

    settings["risk"]["broad_core_etfs"] = ["VTI", "VTI"]
    with pytest.raises(ValueError, match="unique"):
        build_policy_config(settings)


def test_build_policy_config_refuses_self_tuning():
    settings = deepcopy(load_settings())
    settings["learning"]["self_tuning_enabled"] = True

    with pytest.raises(ValueError, match="self_tuning_enabled must be false"):
        build_policy_config(settings)


def test_validate_policy_config_rejects_extra_keys_and_boolean_integer():
    policy = build_policy_config(load_settings())
    policy["unreviewed"] = "authority"
    with pytest.raises(ValueError, match="unexpected keys"):
        validate_policy_config(policy)

    policy = build_policy_config(load_settings())
    policy["alert_near_bps"] = True
    with pytest.raises(ValueError, match="alert_near_bps"):
        validate_policy_config(policy)

