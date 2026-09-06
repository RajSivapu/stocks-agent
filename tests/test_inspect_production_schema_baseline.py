import copy
import hashlib
import json
from pathlib import Path

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
                       "volatility": "i", "security_definer": False, "definition_sha256": "c" * 64}],
        "indexes": [{"schema": "public", "relation": "holdings", "name": "holdings_pkey",
                     "definition_sha256": "d" * 64}],
        "triggers": [],
        "policies": [{"schema": "public", "relation": "holdings", "name": "public_reader", "command": "r",
                      "roles": ["PUBLIC"], "using_sha256": "e" * 64, "check_sha256": "e" * 64}],
        "acls": [{"object_kind": "relation", "schema": "public", "object": "holdings",
                  "grantee": "dashboard", "grantor": "postgres", "privilege": "SELECT", "grantable": False}],
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
    }


def _roots(catalog=None):
    return [
        {"relation": relation, "count": 0, "root_sha256": hashlib.sha256(b"").hexdigest()}
        for relation in sorted(item["name"] for item in (catalog or _catalog())["relations"] if item["kind"] in {"r", "p"})
    ]


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
        if "current_user AS role" in query:
            return [self.identity]
        if "protected_roots" in query:
            return [{"protected_roots": self.roots}]
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
    assert "rolpassword" not in catalog_query


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
    assert "schedule:" not in workflow and "workflow_run:" not in workflow
    assert "environment: owner-dashboard-production" in workflow
    assert "refs/heads/main" in workflow and "GITHUB_SHA" in workflow and "MAIN_SHA" in workflow
    assert "owner-dashboard-ci.yml/runs?head_sha=$MAIN_SHA" in workflow
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
