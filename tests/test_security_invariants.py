import ast
import json
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


def test_v1_intelligence_configuration_keeps_zero_cost_owner_only_boundaries():
    settings = json.loads((ROOT / "config" / "settings.json").read_text())
    intelligence = settings["intelligence"]

    assert intelligence["paid_fallback_enabled"] is False
    assert intelligence["runtime_model_api_enabled"] is False
    assert intelligence["automatic_policy_changes_enabled"] is False
    assert intelligence["suggestion_only"] is True
    assert intelligence["execution_allowed"] is False
    assert settings["guardrails"]["execution_allowed"] is False
    assert settings["access"]["friend_invitations_enabled"] is False
    assert settings["learning"]["self_tuning_enabled"] is False
    assert "benzinga" not in intelligence["providers"]
    assert "alpaca" not in intelligence["providers"]


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


def test_dashboard_role_tools_cannot_be_disabled_with_python_optimization():
    for name in (
        "provision_dashboard_runtime_role.py",
        "verify_owner_dashboard_role.py",
    ):
        path = ROOT / "scripts" / name
        tree = ast.parse(path.read_text(), filename=str(path))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_dashboard_role_migration_is_select_only_and_excludes_sensitive_tables():
    path = ROOT / "sql" / "migrations" / "20260906_owner_dashboard_read_role.sql"
    sql = path.read_text()
    assert "CREATE ROLE stock_agent_dashboard" in sql
    assert "NOBYPASSRLS" in sql
    assert "FOR SELECT TO stock_agent_dashboard" in sql
    assert "GRANT SELECT" in sql
    for forbidden in (
        "GRANT INSERT",
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT EXECUTE",
        "auth.users",
        "portfolio_commands",
        "telegram_updates",
        "vault.decrypted_secrets",
    ):
        assert forbidden not in sql


def test_gateway_migration_verifier_covers_the_full_release_chain():
    source = (ROOT / "scripts" / "verify_decision_gateway_migration.py").read_text()
    for migration in (
        "20260902_decision_safety_gateway.sql",
        "20260903_owner_investment_plans.sql",
        "20260904_outcome_evaluation.sql",
    ):
        assert migration in source
    for invariant in (
        "owner_investment_plans",
        "get_due_market_decisions(integer)",
        "upsert_market_outcome_grades(jsonb)",
        "_verify_outcome_grades",
    ):
        assert invariant in source


def test_webhook_acknowledges_non_owner_updates_without_processing_them():
    source = (ROOT / "supabase" / "functions" / "telegram-portfolio" / "index.ts").read_text()
    assert "return jsonResponse(200, { ok: true, ignored: true });" in source
    assert "return jsonResponse(403, { ok: false });" not in source


def test_alert_callbacks_use_the_owner_gate_fixed_rpc_and_telegram_receipt():
    source = (ROOT / "supabase" / "functions" / "telegram-portfolio" / "index.ts").read_text()
    owner_gate = source.index("if (!ownerMatches(chatId, userId")
    dispatch = source.index("await handleCallback(updateId as number")
    assert owner_gate < dispatch
    assert '"apply_market_alert_action"' in source
    assert "alertActionPayload(parsed, updateId, OWNER_CHAT_ID_NUMBER, OWNER_USER_ID_NUMBER)" in source
    assert "No brokerage order was placed or modified." in (
        ROOT / "supabase" / "functions" / "telegram-portfolio" / "alert-utils.mjs"
    ).read_text()


def test_telegram_eval_covers_owner_only_alert_transitions():
    evaluation = (ROOT / "docs" / "eval" / "telegram-portfolio-eval.yaml").read_text()
    for case_name in (
        "alert_wrong_owner",
        "alert_arm_once",
        "alert_stale_version",
        "alert_snooze_and_resume",
        "alert_no_brokerage_capability",
    ):
        assert f"name: {case_name}" in evaluation


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
        assert "WHEN lower(trim(action)) IN ('add slowly','add/dca','dca','dca/add slowly') THEN 'add'" in sql
        assert "WHEN lower(trim(action)) IN ('study','watch - alert at $285','watch - alert at $610','watch/add on pullback') THEN 'watch'" in sql
        assert "WHEN lower(trim(confidence)) IN ('low-medium','medium-high') THEN 'medium'" in sql
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
        "market_alert_drafts",
        "market_alert_events",
        "market_alert_rules",
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
            "create_market_alert_publication",
            "create_market_alert_drafts",
            "expire_market_alert_rules",
            "finish_market_alert_publication",
            "finish_market_publication",
        "get_due_market_decisions",
        "record_market_alert_evaluations",
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
    market_skill = skills["market-briefing"]
    for boundary in (
        "comparisons",
        "equal monthly contributions",
        "Hypothetical history is not a forecast",
        "never change or cancel an owner plan",
        "like-for-like",
        "diversifier",
        "peer",
    ):
        assert boundary in market_skill


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
        "vti_like_for_like_vs_diversifier",
        "comparison_history_is_server_computed",
        "comparison_cannot_change_owner_plan",
        "peer_list_requires_business_validation",
        "companion_substitute_rejected",
        "vxus_diversifier_companion",
        "satellite_not_recurring",
        "no_companion_qualified",
        "companion_history_is_server_computed",
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


def test_routine_documentation_exposes_only_scoped_cloud_credentials():
    routines = (ROOT / "routines" / "README.md").read_text()
    for forbidden in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_OWNER_CHAT_ID",
    ):
        assert forbidden not in routines
    env_heading = re.search(r"Environment (?:variables|values):\s*\n\n```text\n(.*?)```", routines, re.S)
    assert env_heading is not None
    env_block = env_heading.group(1)
    assert env_block.strip().splitlines() == [
        "SUPABASE_URL=https://<project-ref>.supabase.co",
        "MARKET_AGENT_SECRET=<dedicated-random-gateway-secret>",
        "FINNHUB_API_KEY=<read-only-key>",
    ]
    for readable_secret in (
        "ALPHAVANTAGE_API_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_BOT_TOKEN",
    ):
        assert readable_secret not in env_block
    assert "ALPHAVANTAGE_API_KEY" not in routines
    for required in (
        "narrowly scoped",
        "read-only",
        "personal",
        "evaluate_and_publish",
        "delivery_unknown",
        "status: suppressed",
    ):
        assert required in routines
    assert '{"alerts":"ok","gateway":"ok","finnhub":"ok","yahoo":"ok"}' in routines
    assert "sends no Telegram healthcheck or alert" in routines


def test_behavioral_evals_use_gateway_receipts_not_privileged_helpers():
    evaluation = (ROOT / "docs" / "eval" / "market-briefing-eval.yaml").read_text()
    assert "lib.telegram" not in evaluation
    assert "lib.db" not in evaluation
    assert "gateway" in evaluation.lower()
