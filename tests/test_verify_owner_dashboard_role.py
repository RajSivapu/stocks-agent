from pathlib import Path

import pytest

from scripts.verify_owner_dashboard_role import (
    EXPECTED_COLUMNS,
    EXPECTED_FUNCTIONS,
    EXPECTED_PRIVILEGE_MEMBERS,
    EXPECTED_PRIVILEGE_ATTRIBUTES,
    EXPECTED_RUNTIME_ATTRIBUTES,
    collect_dashboard_privileges,
    evaluate_dashboard_privileges,
)


def valid_snapshot():
    return {
        "role": dict(EXPECTED_RUNTIME_ATTRIBUTES),
        "privilege_role_state": dict(EXPECTED_PRIVILEGE_ATTRIBUTES),
        "memberships": ["stock_agent_dashboard"],
        "privilege_memberships": [],
        "privilege_members": [dict(member) for member in EXPECTED_PRIVILEGE_MEMBERS],
        "runtime_members": [],
        "database_privileges": {"CONNECT", "TEMPORARY"},
        "schema_privileges": {"USAGE"},
        "other_schema_privileges": {},
        "table_privileges": {},
        "sequence_privileges": {},
        "column_privileges": {
            table: set(columns) for table, columns in EXPECTED_COLUMNS.items()
        },
        "application_function_execute": sorted(EXPECTED_FUNCTIONS),
        "owned_objects": [],
        "policies": {
            table: {"cmd": "SELECT", "roles": ["stock_agent_dashboard"]}
            for table in EXPECTED_COLUMNS
        },
    }


def test_valid_dashboard_privileges_return_a_bounded_receipt():
    result = evaluate_dashboard_privileges(valid_snapshot())
    assert result == {
        "status": "verified",
        "runtime_role": "stock_agent_dashboard_runtime",
        "privilege_role": "stock_agent_dashboard",
        "table_count": len(EXPECTED_COLUMNS),
        "write_privileges": 0,
        "application_function_execute": 0,
        "owned_objects": 0,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda value: value["role"].update(rolbypassrls=True), "bypass RLS"),
        (lambda value: value["privilege_role_state"].update(rolcanlogin=True), "privilege role"),
        (lambda value: value["memberships"].append("service_role"), "membership"),
        (lambda value: value["privilege_memberships"].append("service_role"), "privilege role membership"),
        (
            lambda value: value["privilege_members"].append({
                "member": "rogue_dashboard_reader",
                "admin_option": False,
                "inherit_option": True,
                "set_option": True,
            }),
            "privilege role members",
        ),
        (
            lambda value: value["privilege_members"][0].update(admin_option=True),
            "privilege role members",
        ),
        (lambda value: value["runtime_members"].append("rogue_dashboard_reader"), "incoming members"),
        (lambda value: value["database_privileges"].add("CREATE"), "database privilege"),
        (lambda value: value["schema_privileges"].add("CREATE"), "schema privilege"),
        (
            lambda value: value["other_schema_privileges"].update(extensions={"USAGE"}),
            "schema privilege outside public",
        ),
        (lambda value: value["table_privileges"].update({"holdings": {"UPDATE"}}), "table privilege"),
        (lambda value: value["sequence_privileges"].update({"holdings_id_seq": {"USAGE"}}), "sequence privilege"),
        (lambda value: value["application_function_execute"].append("apply_portfolio_command"), "function"),
        (lambda value: value["owned_objects"].append("public.holdings"), "ownership"),
        (lambda value: value["column_privileges"]["holdings"].add("notes"), "column"),
        (lambda value: value["policies"]["holdings"].update(cmd="ALL"), "policy"),
    ),
)
def test_privilege_verifier_rejects_every_authority_expansion(mutation, message):
    snapshot = valid_snapshot()
    mutation(snapshot)
    with pytest.raises(RuntimeError, match=message):
        evaluate_dashboard_privileges(snapshot)


def test_privilege_collector_executes_static_queries_without_empty_parameter_tuple():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *args):
            calls.append(args)

        def fetchall(self):
            return []

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    collect_dashboard_privileges(Connection())
    policy_calls = [args for args in calls if "pg_policies" in args[0]]
    assert len(policy_calls) == 1
    assert len(policy_calls[0]) == 1


def test_privilege_collector_excludes_only_superuser_incoming_edges_and_unreachable_objects():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *args):
            calls.append(args)

        def fetchall(self):
            return []

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    collect_dashboard_privileges(Connection())

    incoming_membership_queries = [
        args for args in calls
        if "pg_auth_members" in args[0] and "WHERE granted.rolname" in args[0]
    ]
    assert len(incoming_membership_queries) == 2
    assert all("NOT member.rolsuper" in args[0] for args in incoming_membership_queries)

    for privilege_function in (
        "has_table_privilege", "has_column_privilege", "has_sequence_privilege",
    ):
        query_calls = [args for args in calls if privilege_function in args[0]]
        assert len(query_calls) == 1
        query, parameters = query_calls[0]
        assert "has_schema_privilege" in query
        assert parameters == (
            "stock_agent_dashboard_runtime", "stock_agent_dashboard_runtime",
        )


def test_dashboard_migration_revokes_trigger_function_execution_and_future_public_defaults():
    migration = (
        Path(__file__).parents[1]
        / "sql/migrations/20260906_owner_dashboard_read_role.sql"
    ).read_text()
    assert "reject_decision_evaluation_mutation() FROM PUBLIC" in migration
    assert "reject_market_alert_ledger_mutation() FROM PUBLIC" in migration
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC" in migration


def test_dashboard_role_has_exact_intelligence_select_columns():
    receipt = evaluate_dashboard_privileges(valid_snapshot())
    assert receipt["write_privileges"] == 0
    assert "market_source_items" in EXPECTED_COLUMNS
    assert "raw_payload" not in EXPECTED_COLUMNS["market_source_items"]
    assert "normalized_text" not in EXPECTED_COLUMNS["market_source_items"]


def test_dashboard_authority_closure_revokes_public_trigger_helpers_and_is_consolidated():
    root = Path(__file__).parents[1]
    migration = (root / "sql/migrations/20261016_dashboard_runtime_authority_closure.sql").read_text()
    schema = (root / "sql/schema.sql").read_text()
    assert "initialize_market_intelligence_window()" in migration
    assert "reuse_market_source_item_if_immutable()" in migration
    assert migration.count("FROM PUBLIC, stock_agent_dashboard") == 2
    assert (
        "-- Consolidated from sql/migrations/20261016_dashboard_runtime_authority_closure.sql\n"
        + migration.rstrip()
        + "\n"
    ) in schema


def test_intelligence_dashboard_migration_is_exact_schema_mirror_and_revokes_first():
    root = Path(__file__).parents[1]
    migration = (root / "sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql").read_text()
    schema = (root / "sql/schema.sql").read_text()
    consolidated = (
        "-- Consolidated from sql/migrations/"
        "20260908_owner_dashboard_intelligence_read_role.sql\n"
        f"{migration.rstrip()}\n"
    )
    assert consolidated in schema
    assert migration.index("REVOKE ALL PRIVILEGES ON TABLE") < migration.index("GRANT SELECT")
    assert "GRANT EXECUTE" not in migration
