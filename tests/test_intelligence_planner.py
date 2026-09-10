from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from lib.config import load_settings
from lib.intelligence.planner import (
    bind_persisted_reference_task,
    build_discovery_plan,
    load_source_capabilities,
    rebind_discovery_plan_window,
)
from lib.intelligence.policy import load_intelligence_policy
from lib.intelligence.research_queue import adaptive_provider_reservations


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


def test_plan_can_be_rebound_to_the_durable_run_window_after_start():
    first = _plan()
    durable_window = {
        "start": "2026-09-04T14:30:00+00:00",
        "end": "2026-09-05T14:30:00+00:00",
    }
    expected = build_discovery_plan(
        load_intelligence_policy(load_settings()),
        load_source_capabilities(),
        phase="pre-market",
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window=durable_window,
        available_credentials=frozenset(),
        required_holding_quote_requests=0,
        last_completed_scans={},
    )

    rebound = rebind_discovery_plan_window(first, durable_window)

    assert rebound == expected
    assert rebound.tasks != first.tasks
    assert all(task.max_attempts == 1 for task in first.tasks)


def test_duplicate_run_reuses_the_original_reference_task_across_timestamp_precision():
    durable = rebind_discovery_plan_window(_plan(), {
        "start": "2026-09-05T11:30:00.123Z",
        "end": "2026-09-06T11:30:00.456Z",
    })
    reference = next(task for task in durable.tasks if task.stage == "reference")
    original_id = "22222222-2222-4222-8222-222222222222"
    persisted = {
        original_id: {
            "id": original_id,
            "stage": "reference",
            "provider": reference.provider,
            "capability_id": reference.capability_id,
            "query_kind": "universe",
            "requested_window": {
                "start": "2026-09-05T11:30:00.123987+00:00",
                "end": "2026-09-06T11:30:00.456789+00:00",
            },
        },
    }

    bound = bind_persisted_reference_task(durable, persisted)
    bound_reference = next(task for task in bound.tasks if task.stage == "reference")

    assert bound_reference.task_id == original_id
    assert bound_reference.task_id != reference.task_id
    assert bound_reference.window == persisted[original_id]["requested_window"]


def test_duplicate_run_never_reuses_a_reference_task_from_another_window():
    durable = rebind_discovery_plan_window(_plan(), {
        "start": "2026-09-05T11:30:00.123Z",
        "end": "2026-09-06T11:30:00.456Z",
    })
    reference = next(task for task in durable.tasks if task.stage == "reference")
    persisted = {
        "22222222-2222-4222-8222-222222222222": {
            "id": "22222222-2222-4222-8222-222222222222",
            "stage": "reference",
            "provider": reference.provider,
            "capability_id": reference.capability_id,
            "query_kind": "universe",
            "requested_window": {
                "start": "2026-09-05T11:31:00.123987+00:00",
                "end": "2026-09-06T11:31:00.456789+00:00",
            },
        },
    }

    assert bind_persisted_reference_task(durable, persisted) == durable


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


@pytest.mark.parametrize("phase", ["pre-market", "intraday", "post-market", "on-demand"])
def test_actual_plans_fit_reference_plus_adaptive_sec_ceiling_without_screen_requests(phase):
    policy = load_intelligence_policy(load_settings())
    plan = build_discovery_plan(
        policy,
        load_source_capabilities(),
        phase=phase,
        run_id=RUN_ID,
        reference_version="sec:fixture-v1",
        requested_window=WINDOW,
        available_credentials=frozenset(),
        required_holding_quote_requests=0,
        last_completed_scans={},
    )
    envelope = adaptive_provider_reservations(phase)
    aggregate_sec = plan.provider_request_totals.get("sec_edgar", 0) \
        + envelope["sec_issuer_submissions"] + envelope["sec_filing_document"]

    assert aggregate_sec == policy.budget_for("sec_edgar", phase)
    assert not any(task.query_kind == "screener" for task in plan.tasks)
    assert sum(plan.provider_request_totals.values()) \
        + plan.reserved_holding_quote_requests + plan.reserved_adaptive_requests <= 100


def test_screen_and_future_form4_capabilities_are_inactive_explicit_contracts():
    capabilities = load_source_capabilities()
    screen_ids = {
        "top_gainers", "top_losers", "most_active", "unusual_volume",
        "near_52w_high_quality", "oversold_quality", "insider_buying_clusters",
    }
    screens = {
        row.screen_id: row for row in capabilities.values()
        if row.query_kind == "screener"
    }

    assert set(screens) == screen_ids
    assert all(not row.enabled for row in screens.values())
    assert all(row.transport_contract["active"] is False for row in screens.values())
    assert all(row.reviewed_at == "2026-09-07" for row in screens.values())
    assert all(row.retention_class == "bounded_derived_lead" for row in screens.values())
    assert set(screens["top_gainers"].status_reasons) == {
        "automation_permission_unproven",
        "public_api_contract_unavailable",
        "robots_review_unverified",
    }
    assert screens["insider_buying_clusters"].status_reasons == (
        "form4_feed_capability_missing",
        "filing_index_capability_missing",
        "ownership_xml_capability_missing",
        "provider_budget_unreserved",
        "parser_contract_missing",
    )

    future_ids = {
        "sec_form4_recent_feed", "sec_form4_filing_index", "sec_form4_ownership_xml",
    }
    future = {capability_id: capabilities[capability_id] for capability_id in future_ids}
    assert all(not row.enabled and row.health == "unsupported" for row in future.values())
    assert {key: row.max_requests_per_run for key, row in future.items()} == {
        "sec_form4_recent_feed": 1,
        "sec_form4_filing_index": 2,
        "sec_form4_ownership_xml": 2,
    }
    assert all(row.transport_contract["all_opens_charged"] is True for row in future.values())
    assert all(row.transport_contract["max_response_bytes"] == 500_000 for row in future.values())
    assert all(row.transport_contract["min_interval_ms"] == 1_000 for row in future.values())
    assert all(row.transport_contract["max_elapsed_seconds"] == 60 for row in future.values())
    assert all(row.transport_contract["redirects"] is False for row in future.values())
    assert all(row.transport_contract["retries"] is False for row in future.values())
    assert all(row.transport_contract["cookies"] is False for row in future.values())
    assert all(row.transport_contract["crumbs"] is False for row in future.values())
    assert all(row.transport_contract["challenge_endpoints"] is False for row in future.values())


def test_disabled_yahoo_screen_cannot_be_enabled_without_a_reviewed_transport(tmp_path):
    def enable_disabled_screen(document):
        screen = next(
            row for row in document["capabilities"]
            if row["capability_id"] == "yahoo_top_gainers_screen"
        )
        screen["enabled"] = True
        screen["health"] = "enabled"

    path = _write_source_config(tmp_path, enable_disabled_screen)

    with pytest.raises(ValueError, match="inactive transport contract"):
        load_source_capabilities(path)


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
