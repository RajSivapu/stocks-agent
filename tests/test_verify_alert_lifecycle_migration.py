import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260905_owner_alert_lifecycle.sql"
SCHEMA = ROOT / "sql" / "schema.sql"
VERIFIER = ROOT / "scripts" / "verify_alert_lifecycle_migration.py"

TABLES = (
    "market_alert_drafts",
    "market_alert_rules",
    "market_alert_rule_versions",
    "market_alert_events",
    "market_alert_actions",
)
RPCS = (
    "create_market_alert_drafts(UUID, JSONB)",
    "apply_market_alert_action(UUID, TEXT, BIGINT, BIGINT, BIGINT, INT, TIMESTAMPTZ)",
    "record_market_alert_evaluations(UUID, JSONB)",
    "create_market_alert_publication(UUID, UUID, DATE, TEXT, TEXT, TEXT, UUID[], UUID)",
    "link_market_alert_publication(UUID[], UUID)",
)


def test_alert_lifecycle_schema_is_owner_only_append_only_and_bounded():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        for table in TABLES:
            assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
            assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in sql
        for ledger in ("market_alert_rule_versions", "market_alert_events", "market_alert_actions"):
            assert f"{ledger}_append_only" in sql
        assert "UNIQUE (telegram_update_id)" in sql
        assert "interval '24 hours'" in sql
        assert "jsonb_array_length(p_drafts) > 5" in sql
        assert "octet_length(rule_snapshot::text) <= 32768" in sql
        assert "publication_id UUID REFERENCES public.market_publications" in sql
        assert "CHECK (version > 0)" in sql
        assert "CHECK (cooldown_seconds BETWEEN 60 AND 604800)" in sql
        assert "CHECK (fire_limit BETWEEN 1 AND 100)" in sql


def test_alert_lifecycle_rpcs_are_fixed_path_and_service_role_only():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        for signature in RPCS:
            name = signature.split("(", 1)[0]
            assert f"CREATE OR REPLACE FUNCTION public.{name}(" in sql
            assert (
                f"REVOKE ALL ON FUNCTION public.{signature} "
                "FROM PUBLIC, anon, authenticated;"
            ) in sql
            assert f"GRANT EXECUTE ON FUNCTION public.{signature} TO service_role;" in sql
        assert sql.count("SECURITY DEFINER\nSET search_path = pg_catalog") >= len(RPCS)
        assert "EXECUTE format(" not in sql


def test_alert_publication_rpc_is_request_bound_and_links_before_delivery():
    sql = MIGRATION.read_text()
    for marker in (
        "alert publication request unavailable",
        "alert publication target mismatch",
        "alert event publication mismatch",
        "alert draft publication mismatch",
        "template_version,rendered_body,rendered_hash,status",
    ):
        assert marker in sql


def test_owner_action_rpc_rejects_expiry_replay_stale_version_and_wrong_owner():
    sql = MIGRATION.read_text()
    for marker in (
        "draft expired",
        "telegram update replay mismatch",
        "stale alert version",
        "alert owner mismatch",
        "invalid alert transition",
        "invalid snooze window",
    ):
        assert marker in sql
    assert "FOR UPDATE" in sql
    assert "p_action IN ('arm','dismiss')" in sql
    assert "p_action IN ('pause','resume','snooze','acknowledge','dismiss')" in sql


def test_verifier_is_rollback_only_and_does_not_use_python_asserts():
    source = VERIFIER.read_text()
    tree = ast.parse(source, filename=str(VERIFIER))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    for marker in (
        "ROLLBACK",
        "duplicate_update_rejected",
        "wrong_owner_rejected",
        "stale_version_rejected",
        "expired_draft_rejected",
        "draft_publication_linked",
        "remaining_test_rows",
    ):
        assert marker in source
