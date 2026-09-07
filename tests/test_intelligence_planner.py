from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from lib.config import load_settings
from lib.intelligence.planner import build_discovery_plan, load_source_capabilities
from lib.intelligence.policy import load_intelligence_policy


WINDOW = {
    "start": "2026-09-05T11:30:00+00:00",
    "end": "2026-09-06T11:30:00+00:00",
}
RUN_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_CONFIG = Path(__file__).parents[1] / "config" / "intelligence_sources.json"


def _plan(*, credentials=frozenset(), holding_quotes=0, scans=None):
    policy = load_intelligence_policy(load_settings())
    return build_discovery_plan(
        policy,
        load_source_capabilities(),
        phase="pre-market",
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window=WINDOW,
        available_credentials=credentials,
        required_holding_quote_requests=holding_quotes,
        last_completed_scans=scans or {},
    )


def _write_source_config(tmp_path, mutate):
    document = json.loads(SOURCE_CONFIG.read_text())
    mutate(document)
    target = tmp_path / "intelligence_sources.json"
    target.write_text(json.dumps(document))
    return target


def test_real_pre_market_plan_gives_every_seed_theme_a_supported_discovery_task():
    policy = load_intelligence_policy(load_settings())
    plan = _plan()

    covered = {task.theme_id for task in plan.tasks if task.stage == "signals"}
    assert set(policy.seed_domains) <= covered
    assert all(
        task.query_kind == plan.capabilities[task.capability_id].query_kind
        for task in plan.tasks
    )
    assert all(
        task.provider != "alpha_vantage"
        for task in plan.tasks
        if task.requires_credential
    )
    assert {
        task.capability_id for task in plan.tasks
    } >= set(policy.required_baseline_capability_ids)


def test_plan_is_fair_and_never_round_robins_unsupported_provider_target_pairs():
    scans = {
        ("gdelt_theme_search", "critical_minerals_and_magnets"): "2026-09-03T00:00:00+00:00",
        ("gdelt_theme_search", "energy_nuclear_and_grid_infrastructure"): "2026-09-01T00:00:00+00:00",
        ("federal_register_document_search", "energy_nuclear_and_grid_infrastructure"):
            "2026-09-04T00:00:00+00:00",
        ("gdelt_theme_search", "healthcare"): "2026-09-02T00:00:00+00:00",
    }
    plan = _plan(scans=scans)
    ordered = [
        task.theme_id
        for task in plan.tasks
        if task.capability_id == "gdelt_theme_search"
        and task.theme_id in {
            "critical_minerals_and_magnets",
            "energy_nuclear_and_grid_infrastructure",
            "healthcare",
        }
    ]

    assert ordered == [
        "energy_nuclear_and_grid_infrastructure",
        "healthcare",
        "critical_minerals_and_magnets",
    ]
    assert plan.coverage["unsupported_pairs"] == []


def test_provider_can_have_separate_keyless_and_keyed_capabilities():
    capabilities = load_source_capabilities()

    assert capabilities["eia_today_in_energy_rss"].required_credential is None
    assert capabilities["eia_statistics_v2"].required_credential == "EIA_API_KEY"
    assert capabilities["eia_today_in_energy_rss"].provider == "eia"
    assert capabilities["eia_statistics_v2"].provider == "eia"
    assert capabilities["eia_today_in_energy_rss"].requirement_tier == "optional"
    assert capabilities["gdelt_theme_search"].requirement_tier == "required_baseline"


def test_task_identity_is_stable_across_retry_and_mapping_order():
    first = _plan()
    second = build_discovery_plan(
        load_intelligence_policy(load_settings()),
        load_source_capabilities(),
        phase="pre-market",
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window={"end": WINDOW["end"], "start": WINDOW["start"]},
        available_credentials=frozenset(),
        required_holding_quote_requests=0,
        last_completed_scans={},
    )

    assert [task.task_id for task in first.tasks] == [task.task_id for task in second.tasks]
    assert all(task.max_attempts == 1 for task in first.tasks)


def test_alpha_vantage_uses_only_fixed_topics_when_credential_is_present():
    plan = _plan(credentials=frozenset({"ALPHAVANTAGE_API_KEY"}))
    alpha_tasks = [task for task in plan.tasks if task.provider == "alpha_vantage"]

    assert alpha_tasks
    assert {task.query["topics"] for task in alpha_tasks} <= {
        "economy_monetary",
        "technology",
        "energy_transportation",
        "manufacturing",
        "life_sciences",
        "retail_wholesale",
        "financial_markets",
        "earnings,mergers_and_acquisitions",
    }
    assert all(task.theme_id not in task.query.values() for task in alpha_tasks)


def test_planner_reserves_holding_and_adaptive_capacity_before_screens():
    plan = _plan(holding_quotes=7)

    assert plan.reserved_holding_quote_requests == 7
    assert plan.reserved_adaptive_requests == 12
    assert sum(plan.provider_request_totals.values()) + 7 + 12 <= 100
    assert all(
        total <= load_intelligence_policy(load_settings()).budget_for(provider, "pre-market")
        for provider, total in plan.provider_request_totals.items()
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda doc: doc["capabilities"][0].__setitem__("query_kind", "web_search"),
            "query kind",
        ),
        (
            lambda doc: doc["capabilities"][0]["allowed_hosts"].append("example.com"),
            "approved host",
        ),
        (
            lambda doc: doc.__setitem__("paid_fallback_enabled", True),
            "paid fallback",
        ),
        (
            lambda doc: doc["capabilities"].append(deepcopy(doc["capabilities"][0])),
            "duplicate capability",
        ),
        (
            lambda doc: doc["capabilities"][0].__setitem__("enabled", False),
            "required baseline",
        ),
        (
            lambda doc: doc["capabilities"][0]["query_pack"]["default"].__setitem__(
                "host", "data.sec.gov"
            ),
            "configured host",
        ),
        (
            lambda doc: doc["capabilities"][0]["query_pack"]["default"].__setitem__(
                "path", "/Archives/edgar/data"
            ),
            "configured path",
        ),
    ],
)
def test_source_registry_rejects_unsafe_or_inconsistent_capabilities(
    tmp_path, mutate, message
):
    path = _write_source_config(tmp_path, mutate)

    with pytest.raises(ValueError, match=message):
        load_source_capabilities(path)


def test_missing_credential_is_coverage_not_a_guessed_request():
    plan = _plan()

    assert "alpha_vantage_topic_news" in plan.coverage["missing_credentials"]
    assert all(task.capability_id != "alpha_vantage_topic_news" for task in plan.tasks)


def test_source_registry_rejects_a_credentialed_required_baseline(tmp_path):
    def credential_baseline(document):
        capability = next(
            item
            for item in document["capabilities"]
            if item["capability_id"] == "gdelt_theme_search"
        )
        capability["required_credential"] = "GDELT_API_KEY"

    path = _write_source_config(tmp_path, credential_baseline)

    with pytest.raises(ValueError, match="required baseline.*zero-key"):
        load_source_capabilities(path)


def test_planner_revalidates_that_required_baselines_are_zero_key():
    capabilities = dict(load_source_capabilities())
    capabilities["gdelt_theme_search"] = replace(
        capabilities["gdelt_theme_search"],
        required_credential="GDELT_API_KEY",
    )

    with pytest.raises(ValueError, match="required baseline.*zero-key"):
        build_discovery_plan(
            load_intelligence_policy(load_settings()),
            capabilities,
            phase="pre-market",
            run_id=RUN_ID,
            reference_version="sec:fixture-v1",
            requested_window=WINDOW,
            available_credentials=frozenset({"GDELT_API_KEY"}),
            required_holding_quote_requests=0,
            last_completed_scans={},
        )


def test_older_capability_theme_pair_wins_before_provider_priority():
    plan = _plan(scans={
        ("gdelt_theme_search", "macro_and_policy"): "2026-09-03T00:00:00+00:00",
        ("federal_register_document_search", "macro_and_policy"):
            "2026-09-01T00:00:00+00:00",
    })
    first_macro_task = next(
        task for task in plan.tasks if task.theme_id == "macro_and_policy"
    )

    assert first_macro_task.capability_id == "federal_register_document_search"


def test_reservations_that_cannot_fit_required_discovery_are_rejected():
    with pytest.raises(ValueError, match="reservations.*required discovery"):
        _plan(holding_quotes=79)


def test_plain_capability_mapping_preserves_queries_and_task_identity():
    policy = load_intelligence_policy(load_settings())
    registry = load_source_capabilities()
    expected = build_discovery_plan(
        policy,
        registry,
        phase="pre-market",
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window=WINDOW,
        available_credentials=frozenset(),
        required_holding_quote_requests=0,
        last_completed_scans={},
    )
    actual = build_discovery_plan(
        policy,
        dict(registry),
        phase="pre-market",
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window=WINDOW,
        available_credentials=frozenset(),
        required_holding_quote_requests=0,
        last_completed_scans={},
    )

    assert [task.task_id for task in actual.tasks] == [
        task.task_id for task in expected.tasks
    ]
    assert [dict(task.query) for task in actual.tasks] == [
        dict(task.query) for task in expected.tasks
    ]
