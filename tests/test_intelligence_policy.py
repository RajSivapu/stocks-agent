from copy import deepcopy

import pytest

from lib.config import load_settings
from lib.intelligence.policy import load_intelligence_policy


@pytest.fixture
def settings():
    return deepcopy(load_settings())


def test_v1_provider_and_alpha_budget_contract(settings):
    policy = load_intelligence_policy(settings)

    assert policy.providers == (
        "gdelt", "alpha_vantage", "finnhub", "yahoo", "sec_edgar",
        "federal_register", "white_house", "doe", "dod", "eia",
        "fred", "bls", "bea",
    )
    assert sum(policy.alpha_vantage_phase_budget.values()) == 18
    assert policy.alpha_vantage_daily_ceiling == 20
    assert policy.packet.max_candidates == 12
    assert policy.packet.max_evidence_per_candidate == 8
    assert policy.packet.max_item_characters == 2_000
    assert policy.packet.max_serialized_bytes == 98_304


def test_budget_for_returns_configured_phase_budget_and_zero_for_unknown_values(settings):
    policy = load_intelligence_policy(settings)

    assert policy.budget_for("alpha_vantage", "pre-market") == 8
    assert policy.budget_for("finnhub", "intraday") == 3
    assert policy.budget_for("unknown", "pre-market") == 0
    assert policy.budget_for("yahoo", "unknown") == 0


def test_paid_and_execution_authority_are_rejected(settings):
    settings["intelligence"]["providers"].append("benzinga")

    with pytest.raises(ValueError, match="provider allowlist"):
        load_intelligence_policy(settings)


def test_root_execution_authority_is_rejected(settings):
    settings["guardrails"]["execution_allowed"] = True

    with pytest.raises(ValueError, match="execution_allowed"):
        load_intelligence_policy(settings)


def test_alpha_vantage_phase_allocation_cannot_exceed_daily_ceiling(settings):
    settings["intelligence"]["alpha_vantage_daily_ceiling"] = 10

    with pytest.raises(ValueError, match="daily ceiling"):
        load_intelligence_policy(settings)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("execution_allowed",), True, "execution_allowed"),
        (("paid_fallback_enabled",), True, "paid_fallback_enabled"),
        (("runtime_model_api_enabled",), True, "runtime_model_api_enabled"),
        (("automatic_policy_changes_enabled",), True, "automatic_policy_changes_enabled"),
        (("alpha_vantage_daily_ceiling",), 21, "daily ceiling"),
        (("alpha_vantage_phase_budget", "pre-market"), 9, "phase budget"),
        (("packet", "max_candidates"), 13, "packet limit"),
        (("packet", "max_evidence_per_candidate"), 9, "packet limit"),
        (("packet", "max_item_characters"), 2_001, "packet limit"),
        (("packet", "max_serialized_bytes"), 98_305, "packet limit"),
    ],
)
def test_unapproved_authority_or_bound_is_rejected(settings, path, value, message):
    target = settings["intelligence"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        load_intelligence_policy(settings)
