import copy
import hashlib
import json

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


def _presence():
    result = {f"public.{name}": name == "holdings" for name in (*RECOVERY_RELATIONS, *POST_20260909_MARKERS)}
    result["supabase_migrations.schema_migrations"] = False
    return result


def _catalog():
    return {
        "relations": [{"schema": "public", "name": "holdings", "kind": "r"}],
        "columns": [{"schema": "public", "relation": "holdings", "name": "ticker", "position": 1,
                     "type": "text", "not_null": True, "has_default": False,
                     "identity": "", "generated": ""}],
        "constraints": [{"schema": "public", "relation": "holdings", "name": "holdings_pkey",
                         "kind": "p", "definition_sha256": "b" * 64}],
        "functions": [{"schema": "public", "identity": "public.normalize_ticker(text)", "language": "plpgsql",
                       "volatility": "i", "security_definer": False, "definition_sha256": "c" * 64}],
        "triggers": [],
        "policies": [],
        "acls": [{"object_kind": "relation", "schema": "public", "object": "holdings",
                  "grantee": "dashboard", "grantor": "postgres", "privilege": "SELECT", "grantable": False}],
    }


def _roots():
    return [
        {"relation": relation, "count": 0, "root_sha256": hashlib.sha256(b"").hexdigest()}
        for relation in ("holdings", "transactions", "portfolio_commands")
    ]


class FakeReadOnlyApi:
    def __init__(self, *, identity=None, catalog=None, roots=None):
        self.identity = identity or {"role": "supabase_read_only_user", "transaction_read_only": "on", "database": "postgres"}
        self.catalog = _catalog() if catalog is None else catalog
        self.roots = _roots() if roots is None else roots
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
            return [{"catalog": self.catalog, "relation_presence": _presence()}]
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

