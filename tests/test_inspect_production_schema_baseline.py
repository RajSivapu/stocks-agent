import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40
RECOVERY_RELATIONS = (
    "analysis_runs", "decision_evaluations", "holdings", "market_collection_checkpoint_history",
    "market_collection_checkpoints", "market_evidence_packets", "market_gateway_requests",
    "market_intelligence_collection_completions", "market_intelligence_run_events", "market_intelligence_runs",
    "market_policy_comparisons", "market_policy_config", "market_publications", "market_report_publications",
    "market_report_request_origins", "market_reports", "market_run_terminal_outcomes",
    "market_source_quota_reservations", "portfolio_cash_ledger_state", "portfolio_command_acknowledgements",
    "portfolio_commands", "reconciled_cash_snapshots", "stock_agent_release_migration_ledger", "transactions",
)
POST_20260909_MARKERS = (
    "market_checkpoint_receipt_lineage", "market_intelligence_context_inputs", "market_intelligence_quote_attempts",
    "market_run_source_item_provenance", "market_scheduled_phase_deadlines", "market_source_item_provenance",
)


def _presence(catalog=None):
    public_relations = {item["name"] for item in (catalog or _catalog())["relations"]}
    result = {f"public.{name}": name in public_relations for name in (*RECOVERY_RELATIONS, *POST_20260909_MARKERS)}
    result["supabase_migrations.schema_migrations"] = False
    return result


def _catalog():
    return {
        "relations": [
            {"schema": "public", "name": "dry_powder", "kind": "r", "row_security": False,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "holdings", "kind": "r", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "owner_investment_plans", "kind": "p", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "market_intelligence_runs", "kind": "r", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "portfolio_commands", "kind": "r", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "suggestion_grades", "kind": "r", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "suggestions", "kind": "r", "row_security": True,
             "force_row_security": False, "owner": "postgres"},
            {"schema": "public", "name": "unexpected_audit_state", "kind": "r", "row_security": False,
             "force_row_security": False, "owner": "postgres"},
        ],
        "columns": [{"schema": "public", "relation": "holdings", "name": "ticker", "position": 1,
                     "type": "text", "not_null": True, "has_default": False, "default_sha256": "a" * 64,
                     "identity": "", "generated": ""}],
        "constraints": [{"schema": "public", "relation": "holdings", "name": "holdings_pkey",
                         "kind": "p", "definition_sha256": "b" * 64}],
        "functions": [{"schema": "public", "identity": "public.normalize_ticker(text)", "language": "plpgsql",
                       "kind": "f", "owner": "postgres", "volatility": "i", "security_definer": False,
                       "definition_sha256": "c" * 64}],
        "indexes": [{"schema": "public", "relation": "holdings", "name": "holdings_pkey",
                     "definition_sha256": "d" * 64, "valid": True, "ready": True, "live": True}],
        "triggers": [],
        "policies": [{"schema": "public", "relation": "holdings", "name": "public_reader", "command": "r", "permissive": True,
                      "roles": ["PUBLIC"], "using_sha256": "e" * 64, "check_sha256": "e" * 64}],
        "acls": [{"object_kind": "relation", "schema": "public", "object": "holdings",
                  "grantee": "dashboard", "grantor": "postgres", "privilege": "SELECT", "grantable": False}],
        "column_acls": [{"schema": "public", "relation": "holdings", "column": "ticker", "grantee": "dashboard",
                         "grantor": "postgres", "privilege": "SELECT", "grantable": False}],
        "schema_acls": [{"schema": "public", "owner": "postgres", "grantee": "dashboard", "grantor": "postgres",
                         "privilege": "USAGE", "grantable": False}],
        "schema_acl_state": [{"schema": "public", "owner": "postgres", "acl_state": "empty", "acl_sha256": "a" * 64}],
        "default_privileges": [{"scope": "public", "owner": "postgres", "object_type": "r",
                                "grantee": "dashboard", "grantor": "postgres", "privilege": "SELECT", "grantable": False}],
        "default_acl_sets": [{"scope": "public", "owner": "postgres", "object_type": "r",
                              "acl_sha256": "f" * 64, "is_empty": True}],
        "authorization_capabilities": [{"server_version_num": 170000, "membership_options_supported": True}],
        "roles": [
            {"name": name, "exists": True, "login": login, "inherit": inherit, "superuser": False,
             "bypassrls": False, "createrole": False, "createdb": False, "replication": False,
             "member_of": members}
            for name, login, inherit, members in (
                ("stock_agent_dashboard", False, False, []),
                ("stock_agent_dashboard_runtime", True, True, ["stock_agent_dashboard"]),
                ("stock_agent_release_reader", False, False, []),
                ("stock_agent_release_reader_runtime", False, False, ["stock_agent_release_reader"]),
            )
        ],
        "memberships": [{"member": "stock_agent_dashboard_runtime", "role": "stock_agent_dashboard",
                         "grantor": "postgres", "admin_option": False, "inherit_option": True, "set_option": True}],
    }


def _roots(catalog=None):
    return [
        {"relation": relation, "count": 0, "root_sha256": hashlib.sha256(b"").hexdigest()}
        for relation in sorted(item["name"] for item in (catalog or _catalog())["relations"] if item["kind"] in {"r", "p"})
    ]


def test_generated_roots_accept_an_empty_public_table():
    from scripts.inspect_production_schema_baseline import _roots_query, _validate_roots

    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local server binaries and a non-root user")

    def run(name, *args, sql=None):
        return subprocess.run(
            [binaries[name], *args], input=sql, text=True, capture_output=True,
            check=True, timeout=60,
        ).stdout

    # Keep the private Unix socket path below PostgreSQL's platform length limit.
    with tempfile.TemporaryDirectory(prefix="inventory-empty-", dir="/tmp") as directory:
        data = str(Path(directory) / "data")
        run("initdb", "-D", data, "-U", "postgres", "--auth=trust", "--no-locale", "--encoding=UTF8")
        run("pg_ctl", "-D", data, "-l", str(Path(directory) / "server.log"),
            "-o", f"-F -k {directory} -c listen_addresses=''", "-w", "start")
        try:
            connection = ("-X", "-h", directory, "-p", "5432", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-qAt")
            run("psql", *connection, "-U", "postgres", sql="""
                CREATE SCHEMA extensions;
                CREATE EXTENSION pgcrypto WITH SCHEMA extensions;
                CREATE TABLE public.empty_root_probe (value text);
                CREATE ROLE supabase_read_only_user LOGIN BYPASSRLS;
                ALTER ROLE supabase_read_only_user SET statement_timeout = '30s';
                GRANT USAGE ON SCHEMA extensions TO supabase_read_only_user;
                GRANT SELECT ON public.empty_root_probe TO supabase_read_only_user;
            """)
            result = json.loads(run("psql", *connection, "-U", "supabase_read_only_user", sql=(
                "BEGIN READ ONLY; SELECT row_to_json(result) FROM ("
                + _roots_query(("empty_root_probe",)) + ") AS result; ROLLBACK;"
            )))
        finally:
            run("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")

    assert result["root_identity"]["transaction_read_only"] == "on"
    assert result["root_identity"]["statement_timeout_ms"] == 30000
    assert result["protected_roots"] == [{
        "relation": "empty_root_probe", "count": 0,
        "root_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }]
    assert _validate_roots(result["protected_roots"], ("empty_root_probe",)) == result["protected_roots"]


class FakeReadOnlyApi:
    def __init__(self, *, identity=None, catalog=None, roots=None):
        self.identity = identity or {"role": "supabase_read_only_user", "transaction_read_only": "on", "database": "postgres"}
        self.catalog = _catalog() if catalog is None else catalog
        self.roots = _roots(self.catalog) if roots is None else roots
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        assert method == "POST"
        assert path == f"/v1/projects/{PROJECT_REF}/database/query/read-only"
        query = payload["query"]
        if "protected_roots" in query:
            return [{"root_identity": {"role": "supabase_read_only_user", "transaction_read_only": "on", "row_security": "on",
                                        "bypassrls": True, "statement_timeout_ms": 30000},
                     "catalog": self.catalog, "relation_presence": _presence(self.catalog), "protected_roots": self.roots}]
        if "current_user AS role" in query:
            return [self.identity]
        if "catalog" in query:
            return [{"catalog": self.catalog, "relation_presence": _presence(self.catalog)}]
        raise AssertionError(query)


def test_inspection_uses_only_read_only_path_and_proves_read_only_identity_first():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    api = FakeReadOnlyApi()
    receipt = inspect_production_schema(api, PROJECT_REF, MAIN_SHA)

    assert receipt["main_sha"] == MAIN_SHA
    assert [path for _method, path, _payload in api.calls] == [
        f"/v1/projects/{PROJECT_REF}/database/query/read-only",
    ] * 3
    assert "current_user AS role" in api.calls[0][2]["query"]
    assert "transaction_read_only" in api.calls[0][2]["query"]
    assert "/database/query\"" not in "\n".join(call[1] for call in api.calls)


def test_inspection_roots_every_validated_public_base_or_partitioned_table_and_fingerprints_authorization():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    api = FakeReadOnlyApi()
    receipt = inspect_production_schema(api, PROJECT_REF, MAIN_SHA)

    assert [item["relation"] for item in receipt["protected_roots"]] == sorted(
        {"dry_powder", "holdings", "owner_investment_plans", "market_intelligence_runs", "portfolio_commands",
         "suggestion_grades", "suggestions", "unexpected_audit_state"}
    )
    roots_query = api.calls[2][2]["query"]
    assert "public.dry_powder" in roots_query
    assert "public.owner_investment_plans" in roots_query
    assert "public.unexpected_audit_state" in roots_query
    roles = {item["name"]: item for item in receipt["catalog"]["roles"]}
    assert roles["stock_agent_dashboard_runtime"]["member_of"] == ["stock_agent_dashboard"]
    assert receipt["catalog"]["policies"][0]["roles"] == ["PUBLIC"]
    catalog_query = api.calls[1][2]["query"]
    assert "pg_get_indexdef" in catalog_query
    assert "default_sha256" in catalog_query
    assert "relrowsecurity" in catalog_query and "relforcerowsecurity" in catalog_query
    assert "WHEN role_oid = 0 THEN 'PUBLIC'" in catalog_query
    assert "THEN 's'::\"char\"" in catalog_query
    assert "default_acl_sets" in catalog_query
    assert "schema_acl_state" in catalog_query
    assert "rolpassword" not in catalog_query


def test_inspection_captures_full_authorization_and_uses_bounded_full_visibility_root_snapshot():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    api = FakeReadOnlyApi()
    receipt = inspect_production_schema(api, PROJECT_REF, MAIN_SHA)

    assert receipt["catalog"]["column_acls"][0]["column"] == "ticker"
    assert receipt["catalog"]["schema_acls"][0]["owner"] == "postgres"
    assert receipt["catalog"]["schema_acl_state"][0]["acl_state"] == "empty"
    assert receipt["catalog"]["default_privileges"][0]["scope"] == "public"
    assert receipt["catalog"]["memberships"][0]["inherit_option"] is True
    assert receipt["catalog"]["functions"][0]["owner"] == "postgres"
    assert receipt["catalog"]["indexes"][0]["live"] is True
    assert receipt["catalog"]["default_acl_sets"][0]["is_empty"] is True
    assert receipt["catalog"]["authorization_capabilities"][0]["membership_options_supported"] is True
    root_query = api.calls[2][2]["query"]
    assert "set_config('row_security'" not in root_query
    assert "set_config('statement_timeout'" not in root_query
    assert "statement_timeout" in root_query and "rolbypassrls" in root_query
    assert "jsonb_agg(to_jsonb(row)" not in root_query
    assert "bit_xor" not in root_query
    assert "sum(" not in root_query
    assert "string_agg" in root_query and "LIMIT 10001" in root_query


def test_inspection_rejects_roots_without_full_visibility_or_matching_catalog_snapshot():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    class FilteredRootApi(FakeReadOnlyApi):
        def __call__(self, method, path, payload=None):
            response = super().__call__(method, path, payload)
            if "protected_roots" in payload["query"]:
                response[0]["root_identity"]["bypassrls"] = False
            return response

    with pytest.raises(RuntimeError, match="root visibility"):
        inspect_production_schema(FilteredRootApi(), PROJECT_REF, MAIN_SHA)

    class ChangedCatalogApi(FakeReadOnlyApi):
        def __call__(self, method, path, payload=None):
            response = super().__call__(method, path, payload)
            if "protected_roots" in payload["query"]:
                response[0]["catalog"]["relations"][0]["owner"] = "changed_owner"
            return response

    with pytest.raises(RuntimeError, match="snapshot"):
        inspect_production_schema(ChangedCatalogApi(), PROJECT_REF, MAIN_SHA)


def test_inspection_preserves_unsupported_membership_options_as_unknown_not_false():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    catalog = _catalog()
    catalog["authorization_capabilities"][0] = {"server_version_num": 150000, "membership_options_supported": False}
    catalog["memberships"][0]["inherit_option"] = None
    catalog["memberships"][0]["set_option"] = None
    receipt = inspect_production_schema(FakeReadOnlyApi(catalog=catalog), PROJECT_REF, MAIN_SHA)
    assert receipt["catalog"]["memberships"][0]["inherit_option"] is None


def test_schema_acl_state_is_present_without_privilege_rows_and_owner_changes_the_receipt():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    first = inspect_production_schema(FakeReadOnlyApi(), PROJECT_REF, MAIN_SHA)
    changed = _catalog()
    changed["schema_acls"] = []
    changed["schema_acl_state"][0]["owner"] = "new_owner"
    second = inspect_production_schema(FakeReadOnlyApi(catalog=changed), PROJECT_REF, MAIN_SHA)
    assert second["catalog"]["schema_acl_state"] == [{"schema": "public", "owner": "new_owner", "acl_state": "empty", "acl_sha256": "a" * 64}]
    assert second["receipt_sha256"] != first["receipt_sha256"]


def test_inspection_rejects_inconsistent_post_marker_presence_and_dynamic_root_sets(monkeypatch):
    import scripts.inspect_production_schema_baseline as inspector

    class BadPresenceApi(FakeReadOnlyApi):
        def __call__(self, method, path, payload=None):
            response = super().__call__(method, path, payload)
            if "catalog" in payload["query"]:
                response[0]["relation_presence"]["public.market_source_item_provenance"] = True
            return response

    with pytest.raises(RuntimeError, match="presence"):
        inspector.inspect_production_schema(BadPresenceApi(), PROJECT_REF, MAIN_SHA)

    missing = _roots()
    missing.pop()
    with pytest.raises(RuntimeError, match="root"):
        inspector.inspect_production_schema(FakeReadOnlyApi(roots=missing), PROJECT_REF, MAIN_SHA)

    monkeypatch.setattr(inspector, "MAX_ROOT_RELATIONS", 2)
    with pytest.raises(RuntimeError, match="root"):
        inspector.inspect_production_schema(FakeReadOnlyApi(), PROJECT_REF, MAIN_SHA)


def test_inspection_rejects_writer_style_or_non_read_only_identity_before_catalog():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    api = FakeReadOnlyApi(identity={"role": "postgres", "transaction_read_only": "off", "database": "postgres"})
    with pytest.raises(RuntimeError, match="read-only identity"):
        inspect_production_schema(api, PROJECT_REF, MAIN_SHA)
    assert len(api.calls) == 1


def test_inspection_fails_closed_on_malformed_catalog_and_root_metadata():
    from scripts.inspect_production_schema_baseline import inspect_production_schema

    unexpected = _catalog()
    unexpected["relations"] = [{"schema": "private", "name": "holdings", "kind": "r"}]
    with pytest.raises(RuntimeError, match="catalog"):
        inspect_production_schema(FakeReadOnlyApi(catalog=unexpected), PROJECT_REF, MAIN_SHA)

    duplicate = _catalog()
    duplicate["relations"] *= 2
    with pytest.raises(RuntimeError, match="duplicate"):
        inspect_production_schema(FakeReadOnlyApi(catalog=duplicate), PROJECT_REF, MAIN_SHA)

    leaked_shape = _catalog()
    leaked_shape["relations"] = [{"schema": "public", "name": "holdings", "kind": "r", "rows": []}]
    with pytest.raises(RuntimeError, match="catalog"):
        inspect_production_schema(FakeReadOnlyApi(catalog=leaked_shape), PROJECT_REF, MAIN_SHA)

    unbounded_membership = _catalog()
    unbounded_membership["roles"][0]["member_of"] = [f"role_{index:03d}" for index in range(65)]
    with pytest.raises(RuntimeError, match="catalog"):
        inspect_production_schema(FakeReadOnlyApi(catalog=unbounded_membership), PROJECT_REF, MAIN_SHA)

    invalid_root = _roots()
    invalid_root[0]["root_sha256"] = "not-a-digest"
    with pytest.raises(RuntimeError, match="root"):
        inspect_production_schema(FakeReadOnlyApi(roots=invalid_root), PROJECT_REF, MAIN_SHA)


def test_inspection_is_deterministic_bounded_and_never_serializes_rows_or_project_ref(monkeypatch):
    import scripts.inspect_production_schema_baseline as inspector

    first = inspector.inspect_production_schema(FakeReadOnlyApi(), PROJECT_REF, MAIN_SHA)
    second = inspector.inspect_production_schema(FakeReadOnlyApi(), PROJECT_REF, MAIN_SHA)
    rendered = json.dumps(first, sort_keys=True)
    assert first == second
    assert PROJECT_REF not in rendered
    assert "database" not in rendered
    assert "production_binding_sha256" in first
    assert first["receipt_sha256"] == hashlib.sha256(
        inspector.canonical_json({key: value for key, value in first.items() if key != "receipt_sha256"}).encode()
    ).hexdigest()

    monkeypatch.setattr(inspector, "MAX_CATALOG_ROWS", 0)
    with pytest.raises(RuntimeError, match="catalog"):
        inspector.inspect_production_schema(FakeReadOnlyApi(), PROJECT_REF, MAIN_SHA)


def test_schema_inventory_workflow_is_manual_protected_and_read_only():
    workflow = Path(".github/workflows/production-schema-inventory.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "ci_workflow_run_id:" in workflow
    assert "CI_WORKFLOW_RUN_ID: ${{ inputs.ci_workflow_run_id }}" in workflow
    assert "schedule:" not in workflow and "workflow_run:" not in workflow
    assert "environment: owner-dashboard-production" in workflow
    assert "refs/heads/main" in workflow and "GITHUB_SHA" in workflow and "MAIN_SHA" in workflow
    assert '[[ "$CI_WORKFLOW_RUN_ID" =~ ^[0-9]+$ ]]' in workflow
    assert 'actions/runs/$CI_WORKFLOW_RUN_ID' in workflow
    assert "owner-dashboard-ci.yml/runs?head_sha=$MAIN_SHA" not in workflow
    assert ".repository.full_name" in workflow
    assert ".head_sha" in workflow
    assert ".status" in workflow and ".conclusion" in workflow
    assert ".path" in workflow and ".name" in workflow
    assert ".event" in workflow and ".head_branch" in workflow
    assert "Owner dashboard verification" in workflow
    assert ".github/workflows/owner-dashboard-ci.yml" in workflow
    assert "--require-hashes --only-binary=:all: -r requirements.lock" in workflow
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in workflow
    assert "vars.SUPABASE_PROJECT_REF" not in workflow
    assert "inspect_production_schema_baseline.py" in workflow
    assert "production-schema-inventory-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "retention-days: 90" in workflow
    assert "managed_isolated_restore.py" not in workflow
    assert "restore_gateway_after_release_failure.py" not in workflow
    assert "SUPABASE_ACCESS_TOKEN" in workflow
    for action in ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@"):
        pinned = [line for line in workflow.splitlines() if action in line]
        assert pinned and all(len(line.rsplit("@", 1)[1].strip()) == 40 for line in pinned)
