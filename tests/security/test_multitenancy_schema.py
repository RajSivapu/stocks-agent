from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

IDENTITY_TABLES = {
    "profiles",
    "app_admins",
    "user_consents",
    "notification_preferences",
    "analysis_schedules",
    "agent_connections",
    "telegram_links",
}

OWNER_TABLES = {
    "holdings",
    "analysis_runs",
    "transactions",
    "portfolio_commands",
    "telegram_updates",
    "suggestions",
    "suggestion_grades",
    "stock_observations",
    "daily_snapshots",
    "dry_powder",
    "radar",
    "lessons",
    "paper_watches",
    "market_gateway_requests",
    "decision_evaluations",
    "market_publications",
    "owner_investment_plans",
}


def test_private_and_exposed_schemas_exist(foundation_database):
    rows = foundation_database.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname IN ('app', 'api', 'machine')"
    ).fetchall()

    assert {row[0] for row in rows} == {"app", "api", "machine"}

    owners = foundation_database.execute(
        """
        SELECT n.nspname, r.rolname, r.rolcanlogin
        FROM pg_namespace n
        JOIN pg_roles r ON r.oid = n.nspowner
        WHERE n.nspname IN ('app', 'api', 'machine')
        """
    ).fetchall()
    assert {(schema, owner, can_login) for schema, owner, can_login in owners} == {
        ("app", "stock_agent_migration_owner", False),
        ("api", "stock_agent_migration_owner", False),
        ("machine", "stock_agent_migration_owner", False),
    }


def test_ordinary_roles_cannot_create_in_application_schemas(foundation_database):
    rows = foundation_database.execute(
        """
        SELECT n.nspname,
               has_schema_privilege('anon', n.oid, 'CREATE'),
               has_schema_privilege('authenticated', n.oid, 'CREATE')
        FROM pg_namespace n
        WHERE n.nspname IN ('app', 'api', 'machine', 'public')
        """
    ).fetchall()

    assert {row[0] for row in rows} == {"app", "api", "machine", "public"}
    assert all(not anon_create and not authenticated_create for _, anon_create, authenticated_create in rows)


def test_identity_tables_have_auth_backed_owners(foundation_database):
    rows = foundation_database.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'app' AND table_name = ANY(%s)
        """,
        (list(IDENTITY_TABLES),),
    ).fetchall()

    assert {row[0] for row in rows} == IDENTITY_TABLES
    foreign_key = foundation_database.execute(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'app' AND t.relname = 'profiles' AND c.contype = 'f'
        """
    ).fetchall()
    assert any("auth.users" in row[0] and "ON DELETE CASCADE" in row[0] for row in foreign_key)


def test_every_legacy_owner_table_has_nullable_migration_column(foundation_database):
    rows = foundation_database.execute(
        """
        SELECT table_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'owner_id'
        """
    ).fetchall()

    observed = {row[0]: (row[1], row[2]) for row in rows}
    assert set(observed) == OWNER_TABLES
    assert all(nullable == "YES" and default is None for nullable, default in observed.values())


def test_legacy_tables_have_owner_preserving_unique_candidates(foundation_database):
    rows = foundation_database.execute(
        """
        SELECT t.relname, array_agg(a.attname ORDER BY k.ordinality)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public'
          AND c.contype = 'u'
          AND t.relname = ANY(%s)
        GROUP BY t.relname, c.oid
        """,
        (list(OWNER_TABLES),),
    ).fetchall()

    observed = {(table_name, tuple(columns)) for table_name, columns in rows}
    natural_keys = {
        "holdings": "ticker",
        "telegram_updates": "telegram_update_id",
        "dry_powder": "month",
        "radar": "ticker",
        "market_gateway_requests": "request_id",
    }
    expected = {
        (table_name, ("owner_id", natural_keys.get(table_name, "id")))
        for table_name in OWNER_TABLES
    }
    assert expected <= observed


def test_identity_tables_do_not_default_to_a_global_owner(foundation_database):
    rows = foundation_database.execute(
        """
        SELECT table_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'app' AND column_name IN ('owner_id', 'id', 'user_id')
          AND table_name = ANY(%s)
        """,
        (list(IDENTITY_TABLES),),
    ).fetchall()

    assert rows
    assert all("auth.uid" not in (default or "") for _, default in rows)


def test_supabase_exposes_only_api_schema():
    config = tomllib.loads((ROOT / "supabase/config.toml").read_text())

    assert config["api"]["schemas"] == ["api"]
    assert "public" not in config["api"]["schemas"]


def test_foundation_migration_is_idempotent(foundation_database):
    migration = ROOT / "sql/migrations/20260905_multitenancy_foundation.sql"

    foundation_database.execute(migration.read_text())
