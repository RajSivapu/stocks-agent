import pytest

from scripts.verify_owner_dashboard_role import (
    EXPECTED_COLUMNS,
    evaluate_dashboard_privileges,
)


def valid_snapshot():
    return {
        "role": {
            "rolname": "stock_agent_dashboard_runtime",
            "rolcanlogin": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolbypassrls": False,
        },
        "memberships": ["stock_agent_dashboard"],
        "schema_privileges": {"USAGE"},
        "table_privileges": {},
        "column_privileges": {
            table: set(columns) for table, columns in EXPECTED_COLUMNS.items()
        },
        "application_function_execute": [],
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
        (lambda value: value["memberships"].append("service_role"), "membership"),
        (lambda value: value["schema_privileges"].add("CREATE"), "schema privilege"),
        (lambda value: value["table_privileges"].update({"holdings": {"UPDATE"}}), "table privilege"),
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
