import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = (
    ROOT / "sql" / "schema.sql",
    ROOT / "sql" / "migrations" / "20260901_reliable_stock_agent.sql",
)
GATEWAY_SQL_FILES = (
    ROOT / "sql" / "schema.sql",
    ROOT / "sql" / "migrations" / "20260902_decision_safety_gateway.sql",
)
GATEWAY_RPCS = (
    "activate_market_policy_config(INT)",
    "claim_market_gateway_request(UUID, TEXT, UUID)",
    "complete_market_gateway_request(UUID, UUID, TEXT, JSONB)",
    "start_market_analysis_run(UUID, UUID, TEXT)",
    "apply_market_artifacts(UUID, UUID, UUID, JSONB)",
    "apply_market_decision_bundle(UUID, UUID, UUID, INT, JSONB, JSONB, JSONB)",
    "import_legacy_suggestion(JSONB)",
    "claim_market_publication(UUID)",
    "finish_market_publication(UUID, UUID, TEXT, JSONB, TEXT)",
)


def test_privileged_portfolio_rpcs_are_schema_qualified_and_service_role_only():
    for path in SQL_FILES:
        sql = path.read_text()
        for function in ("apply_portfolio_command", "cancel_portfolio_command"):
            signature = f"public.{function}(UUID, BIGINT, BIGINT)"
            assert f"CREATE OR REPLACE FUNCTION public.{function}(" in sql
            assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated;" in sql
            assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role;" in sql


def test_live_rpc_verifier_cannot_be_disabled_with_python_optimization():
    for name in (
        "verify_portfolio_command_rpc.py",
        "verify_decision_gateway_migration.py",
    ):
        path = ROOT / "scripts" / name
        tree = ast.parse(path.read_text(), filename=str(path))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_webhook_acknowledges_non_owner_updates_without_processing_them():
    source = (ROOT / "supabase" / "functions" / "telegram-portfolio" / "index.ts").read_text()
    assert "return jsonResponse(200, { ok: true, ignored: true });" in source
    assert "return jsonResponse(403, { ok: false });" not in source


def test_gateway_tables_have_rls_append_only_evaluations_and_service_role_rpcs():
    for path in GATEWAY_SQL_FILES:
        sql = path.read_text()
        for table in (
            "market_gateway_requests",
            "market_policy_config",
            "decision_evaluations",
            "market_publications",
        ):
            assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in sql
        assert "decision_evaluations_append_only" in sql
        assert "decision evaluations are append-only" in sql
        for signature in GATEWAY_RPCS:
            assert (
                f"REVOKE ALL ON FUNCTION public.{signature} "
                "FROM PUBLIC, anon, authenticated;"
            ) in sql
            assert f"GRANT EXECUTE ON FUNCTION public.{signature} TO service_role;" in sql


def test_gateway_migration_preflights_legacy_values_before_normalizing_and_backfilling():
    for path in GATEWAY_SQL_FILES:
        sql = path.read_text()
        preflight = sql.index("legacy suggestion preflight failed")
        normalize = sql.index("UPDATE public.suggestions\nSET action")
        backfill = sql.index("legacy_unverified")
        not_null = sql.index("ALTER COLUMN evaluation_id SET NOT NULL")

        assert preflight < normalize < not_null
        assert backfill < not_null
        assert "WHEN 'trim' THEN 'reduce'" in sql
        assert "WHEN 'exit' THEN 'sell'" in sql
        assert "decision_source TEXT NOT NULL DEFAULT 'legacy'" in sql


def test_gateway_schema_records_server_owned_artifact_provenance():
    for path in GATEWAY_SQL_FILES:
        sql = path.read_text()
        for fragment in (
            "daily_snapshots ADD COLUMN IF NOT EXISTS run_id UUID",
            "lessons ADD COLUMN IF NOT EXISTS run_id UUID",
            "radar ADD COLUMN IF NOT EXISTS updated_run_id UUID",
            "paper_watches ADD COLUMN IF NOT EXISTS opened_run_id UUID",
            "paper_watches ADD COLUMN IF NOT EXISTS closed_run_id UUID",
            "analysis_runs ADD COLUMN IF NOT EXISTS gateway_request_id UUID UNIQUE",
            "suggestions ADD COLUMN IF NOT EXISTS invalidation_price NUMERIC",
        ):
            assert fragment in sql


def test_gateway_security_definer_functions_use_fixed_search_path_and_no_dynamic_sql():
    for path in GATEWAY_SQL_FILES:
        sql = path.read_text()
        assert sql.count("SECURITY DEFINER\nSET search_path = pg_catalog") >= len(GATEWAY_RPCS)
        assert "EXECUTE format(" not in sql
        assert "EXECUTE p_" not in sql
