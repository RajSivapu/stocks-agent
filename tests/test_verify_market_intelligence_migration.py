import ast
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_market_intelligence_migration import (
    evaluate_snapshot,
    remove_report_fields,
)
from scripts import verify_portfolio_command_rpc as portfolio_command_verifier


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260907_market_intelligence.sql"
SCHEMA = ROOT / "sql" / "schema.sql"
VERIFIER = ROOT / "scripts" / "verify_market_intelligence_migration.py"
TRANSACTION_CHRONOLOGY = ROOT / "sql" / "migrations" / "20260909_transaction_chronology.sql"
PORTFOLIO_COMMAND_VERIFIER = ROOT / "scripts" / "verify_portfolio_command_rpc.py"

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
    "market_policy_comparisons",
    "market_learning_observations",
)
RPCS = (
    "start_market_intelligence_run(uuid,text,date,integer,jsonb)",
    "record_market_intelligence(uuid,uuid,jsonb)",
    "read_market_evidence_packet(uuid,uuid)",
    "read_market_report_decisions(uuid,uuid,jsonb)",
    "record_market_report(uuid,text,jsonb)",
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
        "table_grants": [],
        "function_grants": [
            {
                "signature": signature,
                "grantee": "service_role",
                "privilege": "EXECUTE",
                "is_owner": False,
            }
            for signature in RPCS
        ],
        "unexpected_grants": [],
        "brokerage_columns": [],
        "behavior": {
            "new_run_not_duplicate": True,
            "duplicate_idempotency": True,
            "bounded_cache_returned": True,
            "over_quota_rejected": True,
            "phase_quota_rejected": True,
            "mutation_rejected": True,
            "invalid_hash_rejected": True,
            "semantic_hashes_rejected": True,
            "partial_write_rolled_back": True,
            "oversized_json_rejected": True,
            "wrong_run_reservation_rejected": True,
            "ineligible_evidence_rejected": True,
            "url_host_rejected": True,
            "cross_provider_host_rejected": True,
            "social_provider_recorded": True,
            "failed_receipt_recorded": True,
            "report_idempotency": True,
            "report_source_provenance": True,
            "report_decision_packet_provenance": True,
            "report_comparison_provenance": True,
            "report_nested_provenance_required": True,
            "report_semantic_key": True,
            "theme_report_recorded": True,
            "incomplete_packet_rejected": True,
            "learning_type_rejected": True,
            "rollback_clean": True,
        },
    }


def test_intelligence_tables_are_append_only_and_gateway_scoped():
    receipt = evaluate_snapshot(complete_snapshot())
    assert receipt["append_only_tables"] == 13
    assert receipt["rls_tables"] == 13
    assert receipt["gateway_only_rpcs"] == 6
    assert receipt["public_execute_grants"] == 0
    assert receipt["brokerage_columns"] == 0


def test_mutation_grant_fails_closed():
    snapshot = complete_snapshot()
    snapshot["unexpected_grants"] = ["anon:market_reports:INSERT"]
    with pytest.raises(RuntimeError, match="unexpected grant"):
        evaluate_snapshot(snapshot)

@pytest.mark.parametrize("field", ["report_source_provenance", "report_decision_packet_provenance", "report_comparison_provenance", "report_nested_provenance_required", "report_semantic_key"])
def test_report_provenance_requires_actual_database_evidence(field):
    snapshot = complete_snapshot()
    snapshot["behavior"][field] = False
    with pytest.raises(RuntimeError, match="report"):
        evaluate_snapshot(snapshot)


def test_any_non_owner_table_or_function_grant_fails_closed():
    table_grant = complete_snapshot()
    table_grant["table_grants"] = [{
        "table": "market_reports",
        "grantee": "surprise_reader",
        "privilege": "SELECT",
        "is_owner": False,
    }]
    with pytest.raises(RuntimeError, match="unexpected grant"):
        evaluate_snapshot(table_grant)

    function_grant = complete_snapshot()
    function_grant["function_grants"].append({
        "signature": "record_market_report(uuid,text,jsonb)",
        "grantee": "surprise_executor",
        "privilege": "EXECUTE",
        "is_owner": False,
    })
    with pytest.raises(RuntimeError, match="unexpected grant"):
        evaluate_snapshot(function_grant)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("new_run_not_duplicate", "new run"),
        ("duplicate_idempotency", "duplicate idempotency"),
        ("bounded_cache_returned", "bounded cache"),
        ("over_quota_rejected", "over-quota"),
        ("phase_quota_rejected", "phase quota"),
        ("mutation_rejected", "mutation"),
        ("invalid_hash_rejected", "invalid hash"),
        ("semantic_hashes_rejected", "semantic hashes"),
        ("partial_write_rolled_back", "partial write"),
        ("oversized_json_rejected", "oversized JSON"),
        ("wrong_run_reservation_rejected", "wrong-run reservation"),
        ("ineligible_evidence_rejected", "ineligible evidence"),
        ("url_host_rejected", "URL host"),
        ("cross_provider_host_rejected", "cross-provider host"),
        ("social_provider_recorded", "social provider"),
        ("failed_receipt_recorded", "failed receipt"),
        ("report_idempotency", "report idempotency"),
        ("theme_report_recorded", "theme report"),
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
        assert "alpha vantage phase quota exceeded" in sql
        assert "public.market_canonical_jsonb" in sql
        assert sql.count("public.market_canonical_jsonb(") >= 8
        assert "v_row - ARRAY['id','content_hash']" in sql
        assert "public.market_canonical_jsonb(v_packet->'packet')" in sql
        assert "public.market_canonical_jsonb(p_report->'report')" in sql
        assert "public.market_canonical_jsonb(p_observation->'observation')" in sql
        assert sql.count("ineligible evidence item") >= 5
        assert sql.count("receipt.status IN ('succeeded','cache_hit')") >= 5
        assert "accepted source item requires successful receipt" in sql
        assert "ineligible evidence item" in sql
        assert "source URL host mismatch" in sql
        assert "WHEN 'gdelt' THEN v_source_host='api.gdeltproject.org'" in sql
        assert "WHEN 'sec_edgar' THEN v_source_host IN ('www.sec.gov','data.sec.gov')" in sql
        assert "'social'" in sql
        assert "'morning','urgent','weekly','monthly','theme','on-demand','intraday'" in sql
        assert "LIMIT 50" in sql
        assert "expires_at > statement_timestamp()" in sql
        assert "'retrieved_at',receipt.retrieved_at" in sql
        assert "~ '^[0-9a-f]{64}$'" in sql
        for lock_key in (
            "market-intelligence-run:",
            "market-intelligence-completion:",
            "market-intelligence-report:",
            "market-intelligence-learning:",
        ):
            assert lock_key in sql
        assert "EXECUTE format(" not in sql


def test_report_nested_provenance_arrays_are_explicit_and_null_safe():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        assert "p_report->'report' ?& ARRAY[\n       'source_ids','policy_decision_ids','comparison_ids'\n     ]" in sql
        for field in ("source_ids", "policy_decision_ids", "comparison_ids"):
            assert (
                f"jsonb_typeof(p_report->'report'->'{field}') IS DISTINCT FROM 'array'"
                in sql
            )
        assert "jsonb_array_length(p_report->'report'->'source_ids') = 0" in sql
        assert "jsonb_array_length(p_report->'report'->'policy_decision_ids') = 0" in sql
        assert "jsonb_array_length(p_report->'report'->'comparison_ids') > 96" in sql


@pytest.mark.parametrize("field", ("source_ids", "policy_decision_ids", "comparison_ids"))
def test_missing_report_probe_builder_removes_each_field_from_a_real_payload(field):
    report_body = {
        "sections": [{"title": "context"}],
        "source_ids": ["source-1"],
        "policy_decision_ids": ["decision-1"],
        "comparison_ids": [],
    }

    changed = remove_report_fields(report_body, {field})

    assert field not in changed
    assert changed["sections"] == [{"title": "context"}]
    assert set(changed) == {"sections", "source_ids", "policy_decision_ids", "comparison_ids"} - {field}
    assert field in report_body


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


def test_transaction_chronology_rejects_late_ledger_entries_without_rewriting_holdings():
    migration = TRANSACTION_CHRONOLOGY.read_text()
    schema = SCHEMA.read_text()

    assert migration in schema
    assert "PERFORM pg_advisory_xact_lock(hashtextextended(v_command.ticker, 0));" in migration
    assert "SELECT MAX(COALESCE(executed_on" in migration
    assert "v_executed_on < v_latest_transaction_on" in migration
    assert "'code', 'TRANSACTION_OUT_OF_ORDER'" in migration
    assert "'reason', 'transaction execution date precedes the recorded ledger; reconciliation is required'" in migration
    assert migration.index("v_executed_on < v_latest_transaction_on") < migration.rindex(
        "RETURN public.apply_portfolio_command_without_chronology"
    )
    assert "v_command.status = 'rejected'" in migration
    assert "v_command.result->>'code' = 'TRANSACTION_OUT_OF_ORDER'" in migration


@pytest.mark.parametrize("prerequisite", [
    "v_command.expires_at <= now()",
    "v_current_shares IS DISTINCT FROM v_command.expected_shares",
    "v_command.operation = 'sell' AND (NOT v_has_holding OR v_command.qty > v_holding.shares)",
    "v_command.operation = 'buy' AND NOT v_has_holding AND v_command.bucket IS NULL",
])
def test_chronology_defers_ineligible_commands_to_legacy_receipts(prerequisite):
    migration = TRANSACTION_CHRONOLOGY.read_text()
    # Delegation retains the legacy expiry / holding-changed / invalid-sell
    # receipt (and new-buy bucket error) rather than reclassifying the command.
    guard = re.search(
        r"IF " + re.escape(prerequisite) + r" THEN\s*"
        r"RETURN public\.apply_portfolio_command_without_chronology"
        r"\(p_command_id, p_chat_id, p_user_id\);\s*END IF;",
        migration,
    )
    assert guard is not None, f"missing legacy prerequisite delegation: {prerequisite}"
    assert guard.end() < migration.index("SELECT MAX(COALESCE(executed_on")
    assert migration.index("v_command.status = 'pending' AND v_command.operation IN ('buy', 'sell')") < guard.start()
    if prerequisite == "v_command.expires_at <= now()":
        assert guard.end() < migration.index("pg_advisory_xact_lock")
    else:
        assert migration.index("pg_advisory_xact_lock") < guard.start()
        assert migration.index("FROM public.holdings") < guard.start()
        assert "v_current_shares := CASE WHEN v_has_holding THEN v_holding.shares ELSE 0 END;" in migration
        assert "WHERE ticker = v_command.ticker\n    FOR UPDATE;" in migration


def test_portfolio_command_verifier_exercises_the_authoritative_chronology_fixture():
    source = PORTFOLIO_COMMAND_VERIFIER.read_text()

    assert "2026-09-01" in source
    assert "2026-09-03" in source
    assert "2026-09-02" in source
    assert "qty=10, price=100" in source
    assert "qty=5, price=110" in source
    assert "qty=10, price=200" in source
    assert 'late_buy["code"] == "TRANSACTION_OUT_OF_ORDER"' in source
    assert "Decimal(str(holding[\"shares\"])) == Decimal(\"5\")" in source
    assert "Decimal(str(sell[\"realized_pnl\"])) == Decimal(\"50\")" in source


def test_chronology_verifier_reloads_authoritative_state_after_rejection():
    class Query:
        def __init__(self, rows):
            self.rows = rows
            self.order_columns = []

        def select(self, _columns):
            return self

        def eq(self, _column, _value):
            return self

        def order(self, column):
            self.order_columns.append(column)
            return self

        def execute(self):
            rows = sorted(self.rows, key=lambda row: tuple(row[column] for column in self.order_columns))
            return SimpleNamespace(data=rows)

    class FakeSupabase:
        def __init__(self, transactions):
            self.transactions = transactions

        def table(self, name):
            return Query({
                "holdings": [{"shares": "5"}],
                "portfolio_commands": [{"realized_pnl": "50"}],
                "transactions": self.transactions,
            }[name])

    buy_id = "ffffffff-ffff-4fff-bfff-ffffffffffff"
    sell_id = "00000000-0000-4000-8000-000000000000"
    transactions = [
        {"id": buy_id, "executed_on": "2026-09-01", "side": "buy", "qty": "10", "price": "100"},
        {"id": sell_id, "executed_on": "2026-09-03", "side": "sell", "qty": "5", "price": "110"},
    ]

    # A date tie must return the same ID order regardless of database row order.
    same_day = [dict(row, executed_on="2026-09-03") for row in transactions]
    for rows in (same_day, list(reversed(same_day))):
        ordered = portfolio_command_verifier._transactions(FakeSupabase(rows))
        assert [row["id"] for row in ordered] == [sell_id, buy_id]

    # UUID order is deliberately opposite to execution chronology.
    for rows in (transactions, list(reversed(transactions))):
        portfolio_command_verifier._require_late_rejection_preserves_accounting(
            FakeSupabase(rows), sell_command_id=sell_id
        )

    for row_index, field, value in (
        (0, "executed_on", "2026-09-02"),
        (0, "qty", "9"),
        (1, "price", "111"),
    ):
        mutated_transactions = deepcopy(transactions)
        mutated_transactions[row_index][field] = value
        with pytest.raises(RuntimeError, match="late Buy changed authoritative transactions"):
            portfolio_command_verifier._require_late_rejection_preserves_accounting(
                FakeSupabase(mutated_transactions), sell_command_id="sell-command"
            )
