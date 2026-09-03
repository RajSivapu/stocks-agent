from __future__ import annotations


API_VIEWS = {
    "profile",
    "today",
    "holdings",
    "transactions",
    "plans",
    "recommendations",
    "runs",
    "connections",
    "telegram_status",
    "settings",
}

OWNER_TABLES = {
    "profiles",
    "app_admins",
    "user_consents",
    "notification_preferences",
    "analysis_schedules",
    "agent_connections",
    "telegram_links",
    "single_owner_migration_receipts",
    "owner_policy_overrides",
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


def test_every_private_owner_table_forces_rls(tenant_database):
    rows = tenant_database.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
        """
    ).fetchall()
    observed = {name: (rls, forced) for name, rls, forced in rows}

    assert set(observed) == OWNER_TABLES
    assert all(rls and forced for rls, forced in observed.values())


def test_api_schema_is_an_exact_invoker_view_allowlist(tenant_database):
    rows = tenant_database.execute(
        """
        SELECT c.relname, c.relkind, coalesce(c.reloptions, ARRAY[]::text[])
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'api'
        """
    ).fetchall()

    assert {name for name, _, _ in rows} == API_VIEWS
    assert all(kind == "v" for _, kind, _ in rows)
    assert all("security_invoker=true" in options for _, _, options in rows)
    assert tenant_database.execute(
        """
        SELECT count(*) = 0
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'api'
        """
    ).fetchone()[0]


def test_policies_are_authenticated_and_bind_both_owner_checks(tenant_database):
    rows = tenant_database.execute(
        """
        SELECT tablename, roles, cmd, coalesce(qual, ''), coalesce(with_check, '')
        FROM pg_policies
        WHERE schemaname = 'app'
        """
    ).fetchall()

    assert rows
    for _table, roles, command, using, check in rows:
        assert roles == ["authenticated"]
        assert "auth.uid() IS NOT NULL" in (using + check)
        if command in ("INSERT", "UPDATE"):
            assert "auth.uid()" in check
        if command in ("SELECT", "UPDATE", "DELETE"):
            assert "auth.uid()" in using


def test_base_tables_and_sensitive_columns_are_not_directly_exposed(tenant_database):
    assert not tenant_database.execute(
        "SELECT has_table_privilege('authenticated', 'app.agent_connections', 'SELECT')"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT has_column_privilege('authenticated', 'app.agent_connections', 'inbound_token_digest', 'SELECT')"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT has_column_privilege('authenticated', 'app.agent_connections', 'outbound_trigger_secret_id', 'SELECT')"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT has_column_privilege('authenticated', 'app.telegram_links', 'telegram_chat_id', 'SELECT')"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT has_column_privilege('authenticated', 'app.telegram_links', 'telegram_user_id', 'SELECT')"
    ).fetchone()[0]


def test_unexpected_public_functions_are_not_executable(tenant_database):
    rows = tenant_database.execute(
        """
        SELECT p.oid::regprocedure::text
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND (
            has_function_privilege('anon', p.oid, 'EXECUTE')
            OR has_function_privilege('authenticated', p.oid, 'EXECUTE')
          )
        """
    ).fetchall()

    assert rows == []
