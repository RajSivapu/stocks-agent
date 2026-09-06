import hashlib
import json
from pathlib import Path

import pytest


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40
PRIOR_SHA = "b" * 40
EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _catalog():
    from scripts.inspect_production_schema_baseline import (
        AUTHORIZATION_ROLES, _CATALOG_FIELDS, _validate_catalog,
    )

    catalog = {name: [] for name in _CATALOG_FIELDS}
    catalog["relations"] = [
        {"schema": "public", "name": "holdings", "kind": "r", "row_security": True,
         "force_row_security": False, "owner": "postgres"},
        {"schema": "public", "name": "owner_plans", "kind": "p", "row_security": False,
         "force_row_security": False, "owner": "postgres"},
    ]
    catalog["columns"] = [
        {"schema": "public", "relation": "holdings", "name": "ticker", "position": 1,
         "type": "text", "not_null": True, "has_default": False,
         "default_sha256": "1" * 64, "identity": "", "generated": ""},
        {"schema": "public", "relation": "holdings", "name": "shares", "position": 2,
         "type": "numeric", "not_null": True, "has_default": False,
         "default_sha256": "1" * 64, "identity": "", "generated": ""},
        {"schema": "public", "relation": "owner_plans", "name": "id", "position": 1,
         "type": "uuid", "not_null": True, "has_default": False,
         "default_sha256": "1" * 64, "identity": "", "generated": ""},
    ]
    catalog["authorization_capabilities"] = [{
        "server_version_num": 170000, "membership_options_supported": True,
    }]
    catalog["roles"] = [{
        "name": role, "exists": False, "login": False, "inherit": False,
        "superuser": False, "bypassrls": False, "createrole": False,
        "createdb": False, "replication": False, "member_of": [],
    } for role in AUTHORIZATION_ROLES]
    return _validate_catalog(catalog)


def _inventory(main_sha=PRIOR_SHA):
    from scripts.inspect_production_schema_baseline import RELATION_PRESENCE, canonical_json

    receipt = {
        "format": "stocks-production-schema-inventory-v1",
        "main_sha": main_sha,
        "production_binding_sha256": hashlib.sha256(
            ("stocks-production-schema-inventory-v1\\0" + PROJECT_REF).encode()
        ).hexdigest(),
        "relation_presence": {
            f"{schema}.{name}": name == "holdings"
            for schema, name in RELATION_PRESENCE
        },
        "catalog": _catalog(),
        "root_algorithm": "sha256-sorted-row-hashes-v1",
        "protected_roots": [
            {"relation": "holdings", "count": 0, "root_sha256": EMPTY_ROOT},
            {"relation": "owner_plans", "count": 0, "root_sha256": EMPTY_ROOT},
        ],
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    return receipt


def test_prior_inventory_requires_exact_digest_head_and_fresh_state(tmp_path):
    from scripts.reconcile_production_schema import inventories_match, validate_prior_inventory

    path = tmp_path / "schema-inventory.json"
    path.write_text(json.dumps(_inventory()))
    prior = validate_prior_inventory(path, PROJECT_REF, PRIOR_SHA)
    assert inventories_match(prior, _inventory(MAIN_SHA))

    changed = _inventory(MAIN_SHA)
    changed["protected_roots"][0]["count"] = 1
    assert not inventories_match(prior, changed)
    path.write_text(json.dumps({**_inventory(), "receipt_sha256": "0" * 64}))
    with pytest.raises(RuntimeError, match="digest"):
        validate_prior_inventory(path, PROJECT_REF, PRIOR_SHA)


def test_atomic_snapshot_queries_every_preexisting_table_once_and_validates_server_roots():
    from scripts.reconcile_production_schema import capture_legacy_snapshot

    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return [{
            "snapshot": {"holdings": [], "owner_plans": []},
            "protected_roots": _inventory()["protected_roots"],
        }]

    snapshot = capture_legacy_snapshot(request, PROJECT_REF, _inventory())
    assert snapshot["tables"] == {"holdings": [], "owner_plans": []}
    assert len(calls) == 1
    assert calls[0][1] == f"/v1/projects/{PROJECT_REF}/database/query/read-only"
    query = calls[0][2]["query"]
    assert query.count("FROM public.holdings AS row") == 1
    assert query.count("FROM public.owner_plans AS row") == 1
    assert "jsonb_agg(row_json ORDER BY row_json::text)" in query
    assert "LIMIT 10001" in query

    def wrong_root(_method, _path, _payload=None):
        roots = [dict(item) for item in _inventory()["protected_roots"]]
        roots[0]["root_sha256"] = "9" * 64
        return [{"snapshot": {"holdings": [], "owner_plans": []}, "protected_roots": roots}]
    with pytest.raises(RuntimeError, match="snapshot roots"):
        capture_legacy_snapshot(wrong_root, PROJECT_REF, _inventory())


def test_snapshot_is_authenticated_encrypted_and_sidecar_contains_no_rows_or_secrets(tmp_path):
    from cryptography.fernet import Fernet, InvalidToken
    from scripts.reconcile_production_schema import write_encrypted_snapshot

    artifact = tmp_path / "legacy-snapshot.enc"
    payload = {"tables": {"holdings": [{"ticker": "SECRET-TICKER", "shares": 1}]},
               "protected_roots": _inventory()["protected_roots"]}
    key = Fernet.generate_key()
    sidecar = write_encrypted_snapshot(
        payload, artifact, key, prior_inventory_run_id="123",
        inventory_receipt_sha256="2" * 64,
    )
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert b"SECRET-TICKER" not in artifact.read_bytes()
    assert set(sidecar) == {
        "format", "prior_inventory_run_id", "inventory_receipt_sha256", "artifact_sha256",
        "plaintext_sha256", "protected_roots_sha256", "table_count", "row_count",
    }
    assert "SECRET-TICKER" not in artifact.with_suffix(".enc.receipt.json").read_text()
    altered = bytearray(artifact.read_bytes()); altered[len(altered) // 2] ^= 1
    with pytest.raises(InvalidToken):
        Fernet(key).decrypt(bytes(altered))


def test_writer_identity_requires_exact_postgres_superuser_and_write_transaction():
    from scripts.reconcile_production_schema import verify_writer_identity

    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return [{"role": "postgres", "transaction_read_only": "off", "database": "postgres", "superuser": True}]
    verify_writer_identity(request, PROJECT_REF)
    assert calls[0][1] == f"/v1/projects/{PROJECT_REF}/database/query"
    for field, value in (("role", "other"), ("transaction_read_only", "on"), ("superuser", False)):
        def unsafe(_method, _path, _payload=None, field=field, value=value):
            row = {"role": "postgres", "transaction_read_only": "off", "database": "postgres", "superuser": True}
            row[field] = value
            return [row]
        with pytest.raises(RuntimeError, match="writer identity"):
            verify_writer_identity(unsafe, PROJECT_REF)


def test_mutation_is_one_transaction_with_finite_guards_projected_roots_and_truthful_ledgers(tmp_path):
    from scripts.reconcile_production_schema import build_mutation_sql, load_reconciliation_sql

    path = tmp_path / "sql/reconciliation/20261004_production_schema_reconciliation.sql"
    path.parent.mkdir(parents=True)
    path.write_text("CREATE TABLE public.new_final_state(id bigint);\n")
    reconciliation = load_reconciliation_sql(tmp_path)
    query = build_mutation_sql(_inventory(), reconciliation)

    assert query.count("BEGIN;") == 1 and query.rstrip().endswith("COMMIT;")
    assert "SET LOCAL statement_timeout = '120s'" in query
    assert "SET LOCAL lock_timeout = '10s'" in query
    assert "pg_advisory_xact_lock(hashtextextended('stock_agent_protected_release', 0))" in query
    assert "jsonb_build_object('ticker', row.ticker, 'shares', row.shares)" in query
    assert path.read_text().strip() in query
    assert "supabase_migrations.schema_migrations" in query
    assert "public.stock_agent_release_migration_ledger" in query
    assert reconciliation["sha256"] in query
    assert query.index("CREATE TEMP TABLE") < query.index(path.read_text().strip())
    assert query.index(path.read_text().strip()) < query.index("post_projection_guard")


@pytest.mark.parametrize(
    ("states", "expected", "calls"),
    [
        (["committed"], "committed_after_unknown", 1),
        (["proven_uncommitted", "committed"], "committed_after_retry", 2),
    ],
)
def test_writer_exception_is_reconciled_before_at_most_one_retry(states, expected, calls):
    from scripts.reconcile_production_schema import execute_with_uncertainty

    attempts = []
    def writer():
        attempts.append(True)
        raise RuntimeError("transport")
    observed = iter(states)
    assert execute_with_uncertainty(writer, lambda: next(observed)) == {
        "status": expected, "attempts": calls,
    }
    assert len(attempts) == calls


def test_ambiguous_writer_outcome_never_retries():
    from scripts.reconcile_production_schema import execute_with_uncertainty

    attempts = []
    with pytest.raises(RuntimeError, match="ambiguous"):
        execute_with_uncertainty(
            lambda: attempts.append(True) or (_ for _ in ()).throw(RuntimeError("lost")),
            lambda: "ambiguous",
        )
    assert len(attempts) == 1


def test_success_receipt_is_bounded_allowlisted_and_contains_no_rows_or_project_ref(tmp_path):
    from scripts.reconcile_production_schema import write_reconciliation_receipt

    path = tmp_path / "schema-reconciliation-receipt.json"
    receipt = write_reconciliation_receipt(path, {
        "main_sha": MAIN_SHA, "ci_workflow_run_id": "456", "prior_inventory_run_id": "123",
        "prior_inventory_receipt_sha256": "1" * 64, "reconciliation_path": "sql/reconciliation/20261004_production_schema_reconciliation.sql",
        "reconciliation_sha256": "2" * 64, "snapshot_artifact_sha256": "3" * 64,
        "snapshot_plaintext_sha256": "4" * 64, "before_projected_roots_sha256": "5" * 64,
        "after_projected_roots_sha256": "5" * 64, "after_inventory_receipt_sha256": "6" * 64,
        "transaction_status": "committed", "writer_attempts": 1, "table_count": 2,
        "row_count": 0, "expected_relation_count": 31, "ignored": {"rows": [{"ticker": "VTI"}], "project": PROJECT_REF},
    })
    assert set(receipt) == {
        "format", "main_sha", "ci_workflow_run_id", "prior_inventory_run_id",
        "prior_inventory_receipt_sha256", "reconciliation_path", "reconciliation_sha256",
        "snapshot_artifact_sha256", "snapshot_plaintext_sha256", "before_projected_roots_sha256",
        "after_projected_roots_sha256", "after_inventory_receipt_sha256", "transaction_status",
        "writer_attempts", "table_count", "row_count", "expected_relation_count", "receipt_sha256",
    }
    assert "VTI" not in path.read_text() and PROJECT_REF not in path.read_text()
    assert path.stat().st_size < 16 * 1024


def test_reconciliation_workflow_is_manual_current_main_bound_and_contains_no_unrelated_operations():
    workflow = Path(".github/workflows/production-schema-reconciliation.yml").read_text()
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "prior_inventory_run_id:" in workflow and "ci_workflow_run_id:" in workflow
    assert "environment: owner-dashboard-production" in workflow
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert "actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38" in workflow
    assert "actions/upload-artifact@65462800fd760344b1a7b4382951275a0abb4808" in workflow
    assert "retention-days: 90" in workflow
    assert 'actions/runs/$CI_WORKFLOW_RUN_ID' in workflow
    assert 'actions/runs/$PRIOR_INVENTORY_RUN_ID' in workflow
    assert ".github/workflows/production-schema-inventory.yml" in workflow
    assert "SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}" in workflow
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in workflow
    assert "RELEASE_RECOVERY_KEY: ${{ secrets.RELEASE_RECOVERY_KEY }}" in workflow
    assert "POSTGRES_URL" not in workflow and "SUPAVISOR" not in workflow
    forbidden = ("telegram", "auth", "sites", "owner-dashboard-release.yml", "managed-isolated-restore")
    assert not any(item in workflow.lower() for item in forbidden)
