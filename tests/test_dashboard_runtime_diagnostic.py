from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from scripts.dashboard_runtime_diagnostic import diagnose_dashboard_runtime


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40
PASSWORD = "private-password-that-must-never-leak"
DATABASE_URL = (
    f"postgresql://stock_agent_dashboard_runtime.{PROJECT_REF}:{PASSWORD}"
    "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, projection=None):
        self.projection = projection or {"intelligence_version": 2}
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement, *_args):
        self.statements.append(statement)
        if statement.startswith("SET "):
            return None
        if "current_user" in statement:
            return Result({
                "database_user": "stock_agent_dashboard_runtime",
                "transaction_read_only": "on",
            })
        if "read_owner_intelligence_v2" in statement:
            return Result({"projection": self.projection})
        raise AssertionError(statement)


def environment():
    return {"DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps({
        "DASHBOARD_DATABASE_URL": DATABASE_URL,
    })}


def test_diagnostic_authenticates_exact_candidate_and_emits_only_bounded_stage_evidence():
    connection = Connection()
    options = {}

    def connector(value, **kwargs):
        assert value == DATABASE_URL
        options.update(kwargs)
        return connection

    receipt = diagnose_dashboard_runtime(
        environment(), PROJECT_REF, MAIN_SHA,
        connector=connector,
        collector=lambda _connection: {"application_function_execute": []},
        evaluator=lambda _snapshot: {
            "status": "verified", "write_privileges": 0,
            "application_function_execute": 0, "owned_objects": 0,
        },
        projection_validator=lambda projection: projection,
    )

    assert receipt["format"] == "stocks-dashboard-runtime-diagnostic-v1"
    assert receipt["main_sha"] == MAIN_SHA
    assert receipt["credential"] == {"status": "valid"}
    assert receipt["connection"] == {"status": "connected"}
    assert receipt["authority"]["status"] == "verified"
    assert receipt["identity"] == {"status": "verified"}
    assert receipt["projection"] == {"status": "verified"}
    assert len(receipt["receipt_sha256"]) == 64
    assert options["connect_timeout"] == 15
    assert any("REPEATABLE READ READ ONLY" in statement for statement in connection.statements)
    assert any("statement_timeout" in statement for statement in connection.statements)
    assert any("lock_timeout" in statement for statement in connection.statements)
    rendered = json.dumps(receipt, sort_keys=True)
    assert PASSWORD not in rendered and DATABASE_URL not in rendered and PROJECT_REF not in rendered


def test_diagnostic_suppresses_private_connection_error_and_stops_after_connection_stage():
    def connector(*_args, **_kwargs):
        raise psycopg.OperationalError(f"server rejected {PASSWORD}")

    receipt = diagnose_dashboard_runtime(environment(), PROJECT_REF, MAIN_SHA, connector=connector)

    assert receipt["connection"] == {"status": "failed", "error_code": "connection_error"}
    assert receipt["authority"] == {"status": "not_run"}
    assert receipt["identity"] == {"status": "not_run"}
    assert receipt["projection"] == {"status": "not_run"}
    assert PASSWORD not in json.dumps(receipt)


@pytest.mark.parametrize("invalid_value", [123, True])
def test_diagnostic_writes_a_receipt_for_a_non_string_credential_value(invalid_value):
    invalid_environment = {"DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps({
        "DASHBOARD_DATABASE_URL": invalid_value,
    })}

    receipt = diagnose_dashboard_runtime(invalid_environment, PROJECT_REF, MAIN_SHA)

    assert receipt["credential"] == {
        "status": "failed", "error_code": "credential_source_invalid",
    }
    assert receipt["connection"] == {"status": "not_run"}
    assert len(receipt["receipt_sha256"]) == 64


def test_diagnostic_records_exact_authority_reason_and_bounded_difference_samples():
    other_schemas = {f"extension_{index:03d}": {"USAGE"} for index in range(80)}
    snapshot = {
        "role": {}, "privilege_role_state": {}, "memberships": [],
        "privilege_memberships": [], "privilege_members": [], "runtime_members": [],
        "database_privileges": {"CONNECT", "TEMPORARY"},
        "schema_privileges": {"USAGE"},
        "other_schema_privileges": other_schemas,
        "table_privileges": {}, "sequence_privileges": {}, "column_privileges": {},
        "application_function_execute": [], "owned_objects": [], "policies": {},
    }

    def evaluator(_snapshot):
        raise RuntimeError("unexpected dashboard schema privilege outside public")

    receipt = diagnose_dashboard_runtime(
        environment(), PROJECT_REF, MAIN_SHA,
        connector=lambda *_args, **_kwargs: Connection(),
        collector=lambda _connection: snapshot,
        evaluator=evaluator,
        projection_validator=lambda projection: projection,
    )

    authority = receipt["authority"]
    assert authority["status"] == "failed"
    assert authority["error_code"] == "unexpected_schema_privilege_outside_public"
    summary = authority["snapshot"]
    assert summary["other_schema_privileges"]["count"] == 80
    assert len(summary["other_schema_privileges"]["sample"]) == 32
    assert len(summary["other_schema_privileges"]["sha256"]) == 64
    assert len(json.dumps(receipt).encode()) < 32_768


def test_diagnostic_identifies_the_catalog_query_that_timed_out_without_error_text():
    def collector(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT procedure.oid::regprocedure::text FROM pg_catalog.pg_proc procedure "
                "WHERE pg_catalog.has_function_privilege(%s, procedure.oid, 'EXECUTE')",
                ("stock_agent_dashboard_runtime",),
            )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            raise psycopg.errors.QueryCanceled(f"timed out near {PASSWORD}")

    class TimedOutConnection(Connection):
        def cursor(self, **_kwargs):
            return Cursor()

    receipt = diagnose_dashboard_runtime(
        environment(), PROJECT_REF, MAIN_SHA,
        connector=lambda *_args, **_kwargs: TimedOutConnection(),
        collector=collector,
    )

    assert receipt["authority"] == {
        "status": "failed",
        "error_code": "query_timeout",
        "query_stage": "function_privileges",
    }
    assert PASSWORD not in json.dumps(receipt)


def test_diagnostic_preserves_one_argument_policy_query_with_a_literal_percent():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *args):
            calls.append(args)

        def fetchall(self):
            return []

    class PolicyConnection(Connection):
        def cursor(self, **_kwargs):
            return Cursor()

    def collector(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_catalog.pg_policies "
                "WHERE policyname LIKE 'owner_dashboard_select_%'"
            )
            cursor.fetchall()
        return {"application_function_execute": []}

    receipt = diagnose_dashboard_runtime(
        environment(), PROJECT_REF, MAIN_SHA,
        connector=lambda *_args, **_kwargs: PolicyConnection(),
        collector=collector,
        evaluator=lambda _snapshot: {"status": "verified"},
        projection_validator=lambda projection: projection,
    )

    assert receipt["authority"] == {"status": "verified"}
    assert len(calls) == 1 and len(calls[0]) == 1
    assert calls[0][0].endswith("LIKE 'owner_dashboard_select_%'")


def test_authority_mismatch_with_oversized_function_signatures_always_writes_a_bounded_receipt():
    long_functions = [
        f"extensions.function_{index:02d}(" + ",".join(["very_long_type_name"] * 1000) + ")"
        for index in range(32)
    ]
    snapshot = {
        "role": {}, "privilege_role_state": {}, "memberships": [],
        "privilege_memberships": [], "privilege_members": [], "runtime_members": [],
        "database_privileges": {"CONNECT", "TEMPORARY"},
        "schema_privileges": {"USAGE"}, "other_schema_privileges": {},
        "table_privileges": {}, "sequence_privileges": {}, "column_privileges": {},
        "application_function_execute": long_functions, "owned_objects": [], "policies": {},
    }

    receipt = diagnose_dashboard_runtime(
        environment(), PROJECT_REF, MAIN_SHA,
        connector=lambda *_args, **_kwargs: Connection(),
        collector=lambda _connection: snapshot,
        evaluator=lambda _snapshot: (_ for _ in ()).throw(
            RuntimeError("dashboard executable function allowlist differs")
        ),
        projection_validator=lambda projection: projection,
    )

    rendered = json.dumps(receipt, sort_keys=True).encode()
    assert len(rendered) < 32_768
    function_summary = receipt["authority"]["snapshot"]["function_execute"]
    assert function_summary["unexpected_count"] == 32
    assert all(len(value.encode()) <= 256 for value in function_summary["unexpected_sample"])
    assert PASSWORD.encode() not in rendered


def test_runtime_diagnostic_workflow_is_manual_protected_and_never_prints_the_secret():
    workflow = Path(".github/workflows/production-dashboard-runtime-diagnostic.yml").read_text()

    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "environment: owner-dashboard-production" in workflow
    assert "refs/heads/main" in workflow and "GITHUB_SHA" in workflow and "MAIN_SHA" in workflow
    assert "Owner dashboard verification" in workflow
    assert ".github/workflows/owner-dashboard-ci.yml" in workflow
    assert "DASHBOARD_PRIOR_MANAGED_SECRETS_JSON: ${{ secrets.DASHBOARD_PRIOR_MANAGED_SECRETS_JSON }}" in workflow
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in workflow
    assert "dashboard_runtime_diagnostic.py" in workflow
    assert "production-dashboard-runtime-diagnostic-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "dashboard-runtime-diagnostic.json" in workflow
    assert "retention-days: 30" in workflow
    assert "managed_isolated_restore.py" not in workflow
    assert "deploy_owner_dashboard_api.py" not in workflow
    for action in ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@"):
        pinned = [line for line in workflow.splitlines() if action in line]
        assert pinned and all(len(line.rsplit("@", 1)[1].strip()) == 40 for line in pinned)
