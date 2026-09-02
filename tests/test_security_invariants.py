import ast
from pathlib import Path
import re


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
        assert "decision_mode TEXT NOT NULL DEFAULT 'discretionary'" in sql
        assert "decision_mode = 'owner_plan' AND bucket = 'core'" in sql


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


def test_gateway_entrypoint_uses_only_pinned_dependencies_and_scoped_secrets():
    source = (
        ROOT / "supabase" / "functions" / "market-briefing-gateway" / "index.ts"
    ).read_text()
    assert 'from "npm:@supabase/supabase-js@2.112.4"' in source
    assert set(re.findall(r'requiredEnvironment\("([A-Z0-9_]+)"\)', source)) == {
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "MARKET_AGENT_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_ID",
    }
    config = (ROOT / "supabase" / "config.toml").read_text()
    assert '[functions."market-briefing-gateway"]' in config
    assert 'verify_jwt = false' in config


def test_gateway_repository_uses_only_fixed_tables_and_named_rpcs():
    source = (
        ROOT / "supabase" / "functions" / "market-briefing-gateway" / "_shared" / "repository.ts"
    ).read_text()
    tables = set(re.findall(r'\.from\("([a-z_]+)"\)', source))
    assert tables == {
        "analysis_runs",
        "decision_evaluations",
        "dry_powder",
        "holdings",
        "lessons",
        "market_gateway_requests",
        "market_policy_config",
        "market_publications",
        "owner_investment_plans",
        "paper_watches",
        "portfolio_commands",
        "radar",
        "stock_observations",
        "suggestion_grades",
        "suggestions",
        "transactions",
    }
    assert set(re.findall(r'\.rpc\("([a-z_]+)"', source)) == {
        "apply_market_artifacts",
        "apply_market_decision_bundle",
        "claim_market_gateway_request",
        "claim_market_publication",
        "complete_market_gateway_request",
        "finish_market_publication",
        "get_due_market_decisions",
        "start_market_analysis_run",
        "upsert_market_outcome_grades",
    }
    assert not re.search(r"client\.from\((?!\")[^)]+\)", source)
    assert not re.search(r"client\.rpc\((?!\")[^)]+\)", source)


def test_gateway_authentication_precedes_body_parsing():
    source = (
        ROOT / "supabase" / "functions" / "market-briefing-gateway" / "_shared" / "handler.ts"
    ).read_text()
    handler_start = source.index("return async (request: Request)")
    secret_check = source.index("await secureEqual", handler_start)
    body_read = source.index("await readBody(request)", handler_start)
    assert secret_check < body_read
    assert "access-control-allow-origin" not in source.lower()


def test_cloud_market_skills_have_only_bounded_gateway_authority():
    skill_names = (
        "market-briefing",
        "equity-research",
        "earnings-review",
        "paper-watch",
        "reconcile-trade",
    )
    skills = {
        name: (ROOT / "skills" / name / "SKILL.md").read_text()
        for name in skill_names
    }
    for skill in skills.values():
        assert "lib.telegram" not in skill
        assert "lib.db" not in skill
        assert "from lib import db" not in skill
        assert "SUPABASE_SERVICE_ROLE_KEY" not in skill
    for name in ("market-briefing", "equity-research", "earnings-review", "paper-watch"):
        assert "scripts/market_gateway.py" in skills[name]
    for name in ("equity-research", "earnings-review", "paper-watch"):
        assert "phase: on-demand" in skills[name]
        assert "status: suppressed" in skills[name]
        assert "no Telegram" in skills[name]
    assert "paper_watch_create" in skills["paper-watch"]
    assert "paper_watch_close" in skills["paper-watch"]
    assert "/buy" in skills["reconcile-trade"]
    assert "/sell" in skills["reconcile-trade"]
    assert "local-admin" in skills["reconcile-trade"]


def test_cloud_market_support_scripts_do_not_restore_privileged_paths():
    preload = (ROOT / "scripts" / "run_preload.py").read_text()
    assert "local-admin-only" in preload
    assert "cloud Routine" not in preload
    assert "Cloud Routine" not in preload
    assert not hasattr(__import__("lib.db", fromlist=["insert_suggestion"]), "insert_suggestion")


def test_market_briefing_eval_covers_gateway_failure_pressure_cases():
    evaluation = (ROOT / "docs" / "eval" / "market-briefing-eval.yaml").read_text()
    required = (
        "stale_plan_reuse",
        "prompt_injected_source_text",
        "impossible_prices",
        "oversized_position",
        "policy_downgrade_or_veto",
        "on_demand_suppression",
        "duplicate_request_id",
        "renderer_smuggling",
        "database_failure",
        "definitive_telegram_failure",
        "ambiguous_delivery",
    )
    for case_name in required:
        assert f"name: {case_name}" in evaluation


def test_owner_plan_migration_is_rls_protected_and_stale_safe():
    migration = ROOT / "sql" / "migrations" / "20260903_owner_investment_plans.sql"
    assert migration.exists()
    sql = migration.read_text()
    schema = (ROOT / "sql" / "schema.sql").read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS public.owner_investment_plans",
        "ALTER TABLE public.owner_investment_plans ENABLE ROW LEVEL SECURITY",
        "expected_plan_updated_at",
        "operation IN ('buy', 'sell', 'stop', 'plan', 'cancel_plan')",
        "GREATEST(1, v_plan.amount * 0.02)",
        "v_command.expected_plan_updated_at",
        "REVOKE ALL ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) FROM PUBLIC, anon, authenticated",
        "GRANT EXECUTE ON FUNCTION public.apply_portfolio_command(UUID, BIGINT, BIGINT) TO service_role",
    ):
        assert fragment in sql
        assert fragment in schema
    assert sql in schema


def test_telegram_plan_handler_uses_policy_allowlist_and_bounded_listing():
    source = (ROOT / "supabase" / "functions" / "telegram-portfolio" / "index.ts").read_text()
    assert 'from("market_policy_config").select("config")' in source
    assert 'planTickerAllowed(String(command.ticker), await activePolicy())' in source
    assert "Only an approved broad Core ETF can use a recurring plan. Nothing changed." in source
    listing = source[source.index("async function investmentPlansText"):source.index("function previewText")]
    assert '.eq("active", true).order("next_due_on").limit(20)' in listing
    assert "planPreviewText(command)" in source
    assert "planResultText(result)" in source


def test_outcome_migration_is_bounded_idempotent_and_service_role_only():
    migration = ROOT / "sql" / "migrations" / "20260904_outcome_evaluation.sql"
    assert migration.exists()
    sql = migration.read_text()
    schema = (ROOT / "sql" / "schema.sql").read_text()
    for fragment in (
        "duplicate suggestion grades require review before outcome migration",
        "idx_suggestion_grades_suggestion_horizon",
        "CREATE OR REPLACE FUNCTION public.get_due_market_decisions(p_limit INT)",
        "CREATE OR REPLACE FUNCTION public.upsert_market_outcome_grades(p_grades JSONB)",
        "jsonb_array_length(p_grades) > 150",
        "v_existing.coverage_status = 'complete'",
        "REVOKE ALL ON FUNCTION public.get_due_market_decisions(INT) FROM PUBLIC, anon, authenticated",
        "REVOKE ALL ON FUNCTION public.upsert_market_outcome_grades(JSONB) FROM PUBLIC, anon, authenticated",
    ):
        assert fragment in sql
        assert fragment in schema
    assert sql in schema
