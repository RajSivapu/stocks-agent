import ast
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verify_market_intelligence_migration import evaluate_snapshot


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260907_market_intelligence.sql"
SCHEMA = ROOT / "sql" / "schema.sql"
VERIFIER = ROOT / "scripts" / "verify_market_intelligence_migration.py"

TABLES = (
    "market_intelligence_runs",
    "market_intelligence_run_events",
    "market_source_quota_reservations",
    "market_source_receipts",
    "market_source_items",
    "market_intelligence_run_items",
    "market_events",
    "market_event_relationships",
    "market_candidate_rankings",
    "market_evidence_packets",
    "market_reports",
    "market_learning_observations",
)
RPCS = (
    "start_market_intelligence_run(uuid,text,date,integer,jsonb)",
    "record_market_intelligence(uuid,uuid,jsonb)",
    "record_market_report(uuid,uuid,jsonb)",
    "record_market_learning(uuid,jsonb)",
)


def complete_snapshot():
    return {
        "tables": {
            table: {
                "rls_enabled": True,
                "append_only_trigger": f"{table}_append_only",
            }
            for table in TABLES
        },
        "functions": {
            signature: {
                "search_path": ["pg_catalog"],
                "public_execute": False,
                "gateway_execute": True,
            }
            for signature in RPCS
        },
        "unexpected_grants": [],
        "brokerage_columns": [],
        "behavior": {
            "new_run_not_duplicate": True,
            "duplicate_idempotency": True,
            "bounded_cache_returned": True,
            "over_quota_rejected": True,
            "mutation_rejected": True,
            "invalid_hash_rejected": True,
            "partial_write_rolled_back": True,
            "oversized_json_rejected": True,
            "wrong_run_reservation_rejected": True,
            "failed_receipt_recorded": True,
            "report_idempotency": True,
            "incomplete_packet_rejected": True,
            "learning_type_rejected": True,
            "rollback_clean": True,
        },
    }


def test_intelligence_tables_are_append_only_and_gateway_scoped():
    receipt = evaluate_snapshot(complete_snapshot())
    assert receipt["append_only_tables"] == 12
    assert receipt["rls_tables"] == 12
    assert receipt["gateway_only_rpcs"] == 4
    assert receipt["public_execute_grants"] == 0
    assert receipt["brokerage_columns"] == 0


def test_mutation_grant_fails_closed():
    snapshot = complete_snapshot()
    snapshot["unexpected_grants"] = ["anon:market_reports:INSERT"]
    with pytest.raises(RuntimeError, match="unexpected grant"):
        evaluate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("new_run_not_duplicate", "new run"),
        ("duplicate_idempotency", "duplicate idempotency"),
        ("bounded_cache_returned", "bounded cache"),
        ("over_quota_rejected", "over-quota"),
        ("mutation_rejected", "mutation"),
        ("invalid_hash_rejected", "invalid hash"),
        ("partial_write_rolled_back", "partial write"),
        ("oversized_json_rejected", "oversized JSON"),
        ("wrong_run_reservation_rejected", "wrong-run reservation"),
        ("failed_receipt_recorded", "failed receipt"),
        ("report_idempotency", "report idempotency"),
        ("incomplete_packet_rejected", "incomplete packet"),
        ("learning_type_rejected", "learning type"),
        ("rollback_clean", "rollback"),
    ),
)
def test_database_behavior_evidence_is_required(field, message):
    snapshot = complete_snapshot()
    snapshot["behavior"][field] = False
    with pytest.raises(RuntimeError, match=message):
        evaluate_snapshot(snapshot)


def test_missing_trigger_rls_or_gateway_scope_fails_closed():
    missing_trigger = complete_snapshot()
    missing_trigger["tables"][TABLES[0]]["append_only_trigger"] = None
    with pytest.raises(RuntimeError, match="append-only"):
        evaluate_snapshot(missing_trigger)

    missing_rls = complete_snapshot()
    missing_rls["tables"][TABLES[0]]["rls_enabled"] = False
    with pytest.raises(RuntimeError, match="RLS"):
        evaluate_snapshot(missing_rls)

    public_rpc = complete_snapshot()
    public_rpc["functions"][RPCS[0]]["public_execute"] = True
    with pytest.raises(RuntimeError, match="PUBLIC execute"):
        evaluate_snapshot(public_rpc)

    wrong_search_path = complete_snapshot()
    wrong_search_path["functions"][RPCS[0]]["search_path"] = ["public", "pg_catalog"]
    with pytest.raises(RuntimeError, match="search_path"):
        evaluate_snapshot(wrong_search_path)


def test_schema_declares_complete_bounded_append_only_ledgers_and_rpcs():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        for table in TABLES:
            assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
            assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in sql
            assert f"{table}_append_only" in sql
        for signature in RPCS:
            name = signature.split("(", 1)[0]
            raw_arguments = signature.removesuffix(")").split("(", 1)[1]
            type_names = {
                "uuid": "UUID",
                "text": "TEXT",
                "date": "DATE",
                "integer": "INT",
                "jsonb": "JSONB",
            }
            display_signature = f"{name}({', '.join(type_names[value] for value in raw_arguments.split(','))})"
            assert f"CREATE OR REPLACE FUNCTION public.{name}(" in sql
            assert (
                f"REVOKE ALL ON FUNCTION public.{display_signature} "
                "FROM PUBLIC, anon, authenticated;"
            ) in sql
            assert f"GRANT EXECUTE ON FUNCTION public.{display_signature} TO service_role;" in sql
        assert sql.count("SECURITY DEFINER\nSET search_path = pg_catalog") >= len(RPCS)
        assert "ON DELETE RESTRICT" in sql
        assert "octet_length(packet::text) <= 98304" in sql
        assert "candidate_count BETWEEN 0 AND 12" in sql
        assert "evidence_count BETWEEN 0 AND 96" in sql
        assert "char_length(normalized_text) <= 2000" in sql
        assert "char_length(canonical_content) <= 4096" in sql
        assert "extensions.digest(convert_to(v_row->>'canonical_content','UTF8'),'sha256')" in sql
        assert "reserved_requests BETWEEN 1 AND 100" in sql
        assert "alpha vantage daily quota exceeded" in sql
        assert "LIMIT 50" in sql
        assert "expires_at > statement_timestamp()" in sql
        assert "~ '^[0-9a-f]{64}$'" in sql
        for lock_key in (
            "market-intelligence-run:",
            "market-intelligence-completion:",
            "market-intelligence-report:",
            "market-intelligence-learning:",
        ):
            assert lock_key in sql
        assert "EXECUTE format(" not in sql


def test_migration_is_idempotent_and_schema_mirrors_it_verbatim():
    migration = MIGRATION.read_text()
    schema = SCHEMA.read_text()
    assert migration in schema
    assert "\\n+--" not in schema
    assert migration.count("CREATE TABLE IF NOT EXISTS public.") == len(TABLES)
    assert migration.count("DROP TRIGGER IF EXISTS") == len(TABLES)


def test_verifier_is_rollback_only_and_optimization_safe():
    source = VERIFIER.read_text()
    tree = ast.parse(source, filename=str(VERIFIER))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    for marker in (
        "--rollback",
        "connection.rollback()",
        "duplicate_idempotency",
        "over_quota_rejected",
        "mutation_rejected",
        "invalid_hash_rejected",
        "oversized_json_rejected",
        "wrong_run_reservation_rejected",
        "rollback_clean",
        "remaining_test_rows",
    ):
        assert marker in source


def test_fixture_is_complete_and_isolated():
    snapshot = complete_snapshot()
    clone = deepcopy(snapshot)
    clone["tables"].pop(TABLES[0])
    assert set(snapshot["tables"]) == set(TABLES)
    assert set(snapshot["functions"]) == set(RPCS)
