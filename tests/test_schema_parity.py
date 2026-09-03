from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from scripts import verify_schema_parity as parity


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_schema_is_exactly_regenerated_from_reviewed_sources():
    assert (ROOT / "sql/schema.sql").read_text(encoding="utf-8") == parity.render_canonical_schema()


def test_migration_versions_are_unique_and_dependency_sorted():
    names = [path.name for path in parity.MIGRATIONS]
    versions = [name.split("_", 1)[0] for name in names]
    assert len(versions) == len(set(versions))
    assert all(path.parent == ROOT / "supabase/migrations" for path in parity.MIGRATIONS)
    assert names == [
        "20260831_legacy_baseline.sql",
        "20260901_reliable_stock_agent.sql",
        "20260902_decision_safety_gateway.sql",
        "20260903_owner_investment_plans.sql",
        "20260904_outcome_evaluation.sql",
        "20260905000000_multitenancy_foundation.sql",
        "20260905500000_fresh_multitenancy.sql",
        "20260906000000_owner_api_and_machine_roles.sql",
        "20260907000000_ledger_projection_commands.sql",
        "20260908000000_portfolio_command_state_machine.sql",
        "20260908010000_app_api_limits.sql",
        "20260908020000_telegram_multitenancy.sql",
        "20260908030000_telegram_webhook_runtime.sql",
        "20260908040000_provider_runs_and_evidence.sql",
        "20260909000000_research_run_api.sql",
        "20260910000000_retention_recovery.sql",
    ]


def test_fresh_schema_catalog_matches_ordered_migration_catalog():
    result = parity.verify_disposable_catalog_parity()

    assert result["status"] == "passed"
    assert result["canonical_digest"] == result["migration_digest"]
    assert result["canonical_digest"] == result["upgrade_digest"]
    assert result["private_data"] is False


def test_fresh_install_bootstrap_refuses_any_legacy_data():
    with parity._cluster() as (port, _data):
        with parity._connect(port) as admin:
            for role in ("anon", "authenticated", "service_role"):
                admin.execute(f"CREATE ROLE {role} NOLOGIN")
            admin.execute("CREATE DATABASE unsafe_fresh_path TEMPLATE template0")
        with parity._connect(port, "unsafe_fresh_path") as connection:
            parity._platform_stubs(connection)
            connection.execute(parity.LEGACY_SCHEMA.read_text(encoding="utf-8"))
            connection.execute(parity.FOUNDATION_MIGRATION.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO public.holdings (ticker, shares, avg_cost) VALUES ('TSTSAFE', 1, 1)"
            )
            with pytest.raises(psycopg.Error, match="fresh bootstrap refuses non-empty"):
                connection.execute(parity.FRESH_BOOTSTRAP.read_text(encoding="utf-8"))


def test_canonical_schema_contains_every_deployable_migration_once():
    rendered = parity.render_canonical_schema()
    for path in parity.MIGRATIONS:
        relative = path.relative_to(ROOT).as_posix()
        assert rendered.count(f"-- BEGIN REVIEWED MIGRATION: {relative}") == 1
        assert rendered.count(f"-- END REVIEWED MIGRATION: {relative}") == 1


def test_schema_sources_and_generator_exclude_secret_or_platform_state():
    rendered = parity.render_canonical_schema().lower()
    assert "supabase_service_role_key=" not in rendered
    assert "postgresql://" not in rendered
    assert "telegram_bot_token=" not in rendered
    assert "copy auth.users" not in rendered
    assert "insert into auth.users" not in rendered
    assert "-- generated canonical fresh-install schema" in rendered
