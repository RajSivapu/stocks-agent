import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_recovery_bundle import recovery_records


class FakeManagementHttp:
    def __init__(self, records):
        self.records = copy.deepcopy(records)
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path.endswith("/database/query/read-only"):
            query = payload["query"]
            if "current_user" in query:
                return [{"role": "supabase_read_only_user", "transaction_read_only": "on", "database": "postgres"}]
            return [{"snapshot": {"datasets": self.records}}]
        if path.endswith("/database/query"):
            return []
        raise AssertionError(path)


def test_management_source_uses_only_read_only_endpoint_and_caches_canonical_snapshot():
    from scripts.managed_isolated_restore import ManagedReadOnlyRecoverySource

    http = FakeManagementHttp(recovery_records())
    source = ManagedReadOnlyRecoverySource(http, "p" * 20)

    assert source.identity()["management_role"] == "supabase_read_only_user"
    assert source.read_records() == recovery_records()
    assert source.counts() == {name: len(rows) for name, rows in recovery_records().items()}
    assert source.read_records() == recovery_records()
    assert [path for _method, path, _payload in http.calls] == [
        f"/v1/projects/{'p' * 20}/database/query/read-only",
        f"/v1/projects/{'p' * 20}/database/query/read-only",
    ]


def test_management_source_rejects_any_non_read_only_identity_before_snapshot():
    from scripts.managed_isolated_restore import ManagedReadOnlyRecoverySource

    http = FakeManagementHttp(recovery_records())
    def unsafe(_method, _path, _payload=None):
        return [{"role": "postgres", "transaction_read_only": "off", "database": "postgres"}]

    with pytest.raises(RuntimeError, match="read-only identity"):
        ManagedReadOnlyRecoverySource(unsafe, "p" * 20).read_records()
    assert len(http.calls) == 0


def test_management_api_outbound_requests_identify_the_managed_restore_client(monkeypatch):
    import scripts.managed_isolated_restore as managed

    seen = {}
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"{}"
    def opener(request, *, timeout):
        seen["request"] = request
        assert timeout == 30
        return Response()

    monkeypatch.setattr(managed, "urlopen", opener)
    assert managed.SupabaseManagementApi("token")("GET", "/v1/projects") == {}
    headers = {key.lower(): value for key, value in seen["request"].headers.items()}
    assert headers["user-agent"] == "stocks-agent-managed-restore/1"
    assert headers["accept"] == "application/json"


def test_direct_script_help_imports_scripts_package_without_pythonpath():
    script = Path(__file__).parents[1] / "scripts" / "managed_isolated_restore.py"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(script), "--help"], env=environment,
                            text=True, capture_output=True)
    assert result.returncode == 0
    assert "--cleanup-only" in result.stdout


def test_cleanup_only_main_uses_mocked_transport_without_printing_environment_secrets(tmp_path, monkeypatch, capsys):
    import scripts.managed_isolated_restore as managed

    identity = tmp_path / "cleanup.json"
    key = "cleanup-key-not-for-output"
    provisioner_class = managed.ManagedProjectProvisioner
    provisioner_class(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                      workflow_run_id="42", workflow_attempt="3", cleanup_key=key.encode())
    def transport(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if method == "GET" and path == "/v1/projects":
            return []
        raise AssertionError((method, path))
    monkeypatch.setattr(managed, "SupabaseManagementApi", lambda _token: transport)
    monkeypatch.setattr(managed, "ManagedProjectProvisioner", lambda *args, **kwargs: provisioner_class(
        *args, **kwargs, max_cleanup_checks=1, sleep=lambda _seconds: None))
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "token-not-for-output")
    monkeypatch.setenv("RELEASE_RECOVERY_KEY", key)
    monkeypatch.setattr(sys, "argv", ["managed_isolated_restore.py", "--cleanup-only", "--production-project-ref", "p" * 20,
                                       "--output-dir", str(tmp_path), "--cleanup-identity", str(identity),
                                       "--workflow-run-id", "42", "--workflow-attempt", "3"])
    assert managed.main() == 0
    output = capsys.readouterr().out
    assert "token-not-for-output" not in output and key not in output


def test_snapshot_sql_uses_postgres_text_literals_for_dataset_keys():
    from scripts.managed_isolated_restore import _snapshot_sql

    query = _snapshot_sql()
    assert "'holdings',COALESCE" in query
    assert '"holdings",COALESCE' not in query


def test_managed_snapshot_contains_every_discovery_dataset_and_exact_source_table():
    from scripts.managed_isolated_restore import _snapshot_sql

    query = _snapshot_sql()
    expected = {
        "reference_manifests": "public.market_reference_manifests",
        "security_reference_revisions": "public.market_security_reference_revisions",
        "reference_chunk_receipts": "public.market_reference_chunk_receipts",
        "reference_finalization_seals": "public.market_reference_finalization_seals",
        "reference_snapshot_memberships": "public.market_reference_snapshot_memberships",
        "reference_run_bindings": "public.market_reference_run_bindings",
        "reference_predecessor_pins": "public.market_reference_predecessor_pins",
        "reference_transfer_requests": "public.market_reference_transfer_requests",
        "reference_transfer_responses": "public.market_reference_transfer_responses",
        "discovery_stage_tasks": "public.market_discovery_stage_tasks",
        "theme_episode_revisions": "public.market_theme_episode_revisions",
        "exposure_facts": "public.market_exposure_facts",
        "research_nominations": "public.market_research_nominations",
    }
    for dataset, table in expected.items():
        assert f"'{dataset}',COALESCE" in query
        assert table in query


def test_discovery_restore_registry_is_in_foreign_key_dependency_order():
    from scripts.verify_recovery_bundle import _RESTORE_TABLES

    datasets = [dataset for dataset, _table, _renames in _RESTORE_TABLES]
    discovery = [
        "reference_chunk_receipts",
        "reference_manifests",
        "security_reference_revisions",
        "reference_finalization_seals",
        "reference_snapshot_memberships",
        "reference_run_bindings",
        "reference_predecessor_pins",
        "reference_transfer_requests",
        "reference_transfer_responses",
        "discovery_stage_tasks",
        "theme_episode_revisions",
        "exposure_facts",
        "research_nominations",
    ]
    assert [datasets.index(name) for name in discovery] == sorted(datasets.index(name) for name in discovery)
    assert datasets.index("intelligence_runs") < datasets.index("reference_chunk_receipts")


def test_restore_target_refuses_caller_owned_or_production_project_and_never_uses_read_only_write_path():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    http = FakeManagementHttp(recovery_records())
    with pytest.raises(RuntimeError, match="created by this run"):
        ManagedRestoreTarget(http, "r" * 20, "p" * 20, created_project_ref=None)
    with pytest.raises(RuntimeError, match="differ"):
        ManagedRestoreTarget(http, "p" * 20, "p" * 20, created_project_ref="p" * 20)

    target = ManagedRestoreTarget(http, "r" * 20, "p" * 20, created_project_ref="r" * 20)
    target.execute("SELECT 1")
    assert http.calls[-1][1] == f"/v1/projects/{'r' * 20}/database/query"
    assert "/read-only" not in http.calls[-1][1]


def test_provisioner_creates_only_one_new_project_in_production_org_region_after_single_slot_preflight():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    calls = []
    def api(method, path, payload=None):
        calls.append((method, path, payload))
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}
        if path == "/v1/projects":
            if method == "GET":
                return [{"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}]
            assert payload["organization_slug"] == "owner-org" and "organization_id" not in payload
            assert payload["region"] == "us-east-1"
            assert isinstance(payload["db_pass"], str) and len(payload["db_pass"]) >= 32
            return {"ref": "r" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, random_bytes=lambda size: b"a" * size)
    assert provisioner.create_and_wait() == "r" * 20
    assert all("aaaaaaaa" not in repr(call) for call in calls if call[1] != "/v1/projects")


def test_provisioner_refuses_to_create_when_the_owner_has_more_than_one_active_project():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}
        if path == "/v1/projects" and method == "GET":
            return [
                {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"},
                {"ref": "x" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"},
            ]
        raise AssertionError("project creation must not happen")

    with pytest.raises(RuntimeError, match="one active project"):
        ManagedProjectProvisioner(api, "p" * 20).create_and_wait()


def test_provisioner_fails_closed_on_unknown_inventory_status_before_project_creation():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path == "/v1/projects" and method == "GET":
            return [
                {"ref": "p" * 20, "name": "production", "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"},
                {"ref": "x" * 20, "name": "foreign", "organization_id": "org-2", "organization_slug": "other", "region": "us-east-1", "status": "UNKNOWN"},
            ]
        raise AssertionError("project creation must not happen")

    with pytest.raises(RuntimeError, match="inventory"):
        ManagedProjectProvisioner(api, "p" * 20).create_and_wait()


def test_restore_receipt_is_bounded_and_never_serializes_secret_values_or_rows(tmp_path):
    from scripts.managed_isolated_restore import write_restore_receipt
    from scripts.export_recovery_bundle import REQUIRED_RECOVERY_RECORDS

    path = tmp_path / "receipt.json"
    write_restore_receipt(path, {
        "main_sha": "a" * 40, "main_tree": "b" * 40,
        "started_at": "2026-09-05T20:00:00Z", "completed_at": "2026-09-05T20:01:00Z",
        "production_project_ref": "p" * 20, "restore_project_ref": "r" * 20,
        "before_root_hash": "c" * 64, "after_root_hash": "c" * 64,
            "restore": {"status": "verified", "isolated": True, "restore_applied": True,
                        "record_set_count": len(REQUIRED_RECOVERY_RECORDS)},
        "migration_retry": {"applied": [], "skipped": ["schema"]},
        "artifacts": {"first": {"path": "recovery/first.enc", "sha256": "d" * 64}},
        "workflow": {"run_id": "99", "attempt": "1"},
        "cleanup": {"attempted": True, "deleted": True, "retained_project_ref": None},
        "forbidden": {"password": "super-secret", "rows": [{"ticker": "VTI"}]},
    })
    receipt = json.loads(path.read_text())
    assert receipt["restore"]["record_set_count"] == len(REQUIRED_RECOVERY_RECORDS)
    assert receipt["before_root_hash"] == receipt["after_root_hash"]
    assert "super-secret" not in path.read_text() and "VTI" not in path.read_text()
    assert path.stat().st_size < 8 * 1024


def test_restore_target_preflight_rejects_nonempty_restore_state_before_any_insert():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    calls = []
    def api(method, path, payload=None):
        calls.append((method, path, payload))
        if payload and "restore_preflight" in payload["query"]:
            return [{"restore_preflight": {"tables_empty": False, "native_migrations_empty": True,
                                             "private_ledger_empty": True}}]
        return []

    target = ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20)
    with pytest.raises(RuntimeError, match="preflight"):
        target.preflight_empty()
    assert len(calls) == 2
    assert "CREATE SCHEMA IF NOT EXISTS supabase_migrations" in calls[0][2]["query"]
    assert calls[1][1] == f"/v1/projects/{'r' * 20}/database/query"


def test_restore_target_creates_empty_native_migration_ledger_before_preflight_without_relation_short_circuit():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    queries = []
    def api(_method, _path, payload=None):
        queries.append(payload["query"])
        if "restore_preflight" in payload["query"]:
            return [{"restore_preflight": {"tables_empty": True, "native_migrations_empty": True,
                                             "private_ledger_empty": True}}]
        return []

    ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20).preflight_empty()
    assert "CREATE TABLE IF NOT EXISTS supabase_migrations.schema_migrations(version text PRIMARY KEY,statements text[])" in queries[0]
    assert "to_regclass" not in queries[1]


def test_recovery_crypt_reads_key_only_from_environment_and_rejects_tampering(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "recovery_crypt.py"
    source, encrypted, recovered = (tmp_path / name for name in ("source", "bundle.enc", "recovered"))
    source.write_bytes(b"recovery fixture")
    environment = {**os.environ, "RELEASE_RECOVERY_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}
    assert subprocess.run([sys.executable, str(script), "encrypt", str(source), str(encrypted)], env=environment, capture_output=True).returncode == 0
    assert b"recovery fixture" not in encrypted.read_bytes()
    assert subprocess.run([sys.executable, str(script), "decrypt", str(encrypted), str(recovered)], env=environment, capture_output=True).returncode == 0
    assert recovered.read_bytes() == b"recovery fixture"
    encrypted.write_bytes(encrypted.read_bytes()[:-1] + b"x")
    assert subprocess.run([sys.executable, str(script), "decrypt", str(encrypted), str(tmp_path / "bad")], env=environment, capture_output=True).returncode != 0


def test_restore_target_proves_migration_retry_is_a_noop_without_running_a_migration_writer():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    def api(_method, _path, payload=None):
        assert payload and "migration_noop" in payload["query"]
        return [{"migration_noop": {"missing": 0, "extra": 0, "mismatch": 0}}]

    target = ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20)
    assert target.verify_migration_noop([{"version": "20260905", "statements": ["SELECT 1"]}]) == {
        "applied": [], "skipped": ["20260905"],
    }


def test_failed_cleanup_receipt_keeps_only_the_retained_temporary_identity(tmp_path):
    from scripts.managed_isolated_restore import write_failed_restore_receipt

    receipt = write_failed_restore_receipt(tmp_path / "failure.json", main_sha="a" * 40,
                                            main_tree="b" * 40, started_at="2026-09-05T20:00:00Z",
                                            completed_at="2026-09-05T20:01:00Z", production_ref="p" * 20,
                                            restore_ref="r" * 20,
                                            cleanup={"attempted": True, "deleted": False,
                                                     "retained_project_ref": "r" * 20, "error": "HTTPError"},
                                            error=RuntimeError("password=do-not-print"))
    data = json.loads(receipt.read_text())
    assert data["cleanup"]["retained_project_ref"] == "r" * 20
    assert data["status"] == "failed"
    assert "do-not-print" not in receipt.read_text()


def test_drill_resolves_a_relative_output_directory_before_exporter_contracts(tmp_path):
    from scripts.managed_isolated_restore import ManagedIsolatedRestoreDrill

    drill = ManagedIsolatedRestoreDrill(lambda *_args: [], "p" * 20, output_dir=Path("recovery"),
                                        repository=tmp_path, workflow_run_id="1", workflow_attempt="1")
    assert drill.output_dir.is_absolute()


def test_preflight_allows_schema_seeded_cash_singleton_but_requires_restore_tables_and_ledgers_empty():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    seen = []
    def api(_method, _path, payload=None):
        seen.append(payload["query"])
        return [{"restore_preflight": {"tables_empty": True, "native_migrations_empty": True,
                                        "private_ledger_empty": True}}]

    ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20).preflight_empty()
    assert "portfolio_cash_ledger_state" not in seen[0]


def test_role_recreation_preserves_dashboard_nologin_and_runtime_login_inherit_without_password():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    queries = []
    roles = [
        {"role": "stock_agent_dashboard", "login": False, "inherit": False, "superuser": False, "bypass_rls": False,
         "memberships": [], "grants": ["SELECT:public.holdings.ticker:grantable=false"]},
        {"role": "stock_agent_dashboard_runtime", "login": True, "inherit": True, "superuser": False, "bypass_rls": False,
         "memberships": ["stock_agent_dashboard"], "grants": []},
    ]
    def api(_method, _path, payload=None):
        queries.append(payload["query"])
        return roles if len(queries) == 2 else []

    target = ManagedRestoreTarget(api,
                                  "r" * 20, "p" * 20, created_project_ref="r" * 20)
    target.reproduce_role_shapes(roles)
    assert len(queries) == 2
    assert "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT NOSUPERUSER" in queries[0]
    assert "PASSWORD" not in queries[0]
    assert "ALTER ROLE" not in queries[0]
    assert "GRANT SELECT (ticker)" not in queries[0]
    assert "FROM pg_catalog.pg_roles" in queries[1]


def test_role_recreation_fails_closed_when_schema_role_grants_do_not_match_recovery():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    roles = [
        {"role": "stock_agent_dashboard", "login": False, "inherit": False, "superuser": False, "bypass_rls": False,
         "memberships": [], "grants": ["SELECT:public.holdings.ticker:grantable=false"]},
        {"role": "stock_agent_dashboard_runtime", "login": True, "inherit": True, "superuser": False, "bypass_rls": False,
         "memberships": ["stock_agent_dashboard"], "grants": []},
    ]
    actual = [dict(row) for row in roles]
    actual[0] = {**actual[0], "grants": []}
    calls = 0
    def api(_method, _path, payload=None):
        nonlocal calls
        calls += 1
        return actual if calls == 2 else []

    target = ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20)
    with pytest.raises(RuntimeError, match="role shapes do not match"):
        target.reproduce_role_shapes(roles)


def test_provisioner_uses_organization_slug_and_ignores_other_org_and_paused_projects_for_free_slot():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    calls = []
    def api(method, path, payload=None):
        calls.append((method, path, payload))
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org",
                    "region": "us-east-1", "status": "ACTIVE_HEALTHY"}
        if path == "/v1/projects" and method == "GET":
            return [
                {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"},
                {"ref": "x" * 20, "organization_id": "org-2", "organization_slug": "other", "region": "us-east-1", "status": "ACTIVE_HEALTHY"},
                {"ref": "y" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "PAUSED"},
            ]
        if path == "/v1/projects" and method == "POST":
            assert payload["organization_slug"] == "owner-org" and "organization_id" not in payload
            return {"ref": "r" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "INACTIVE"}
        if path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}
        raise AssertionError((method, path))

    assert ManagedProjectProvisioner(api, "p" * 20).create_and_wait() == "r" * 20


def test_cleanup_confirms_exact_delete_response_and_bounded_inventory_absence():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    inventory = [[{"ref": "r" * 20, "name": "temporary", "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}], []]
    def api(method, path, _payload=None):
        if method == "DELETE":
            return {"ref": "r" * 20}
        if method == "GET" and path == "/v1/projects":
            return inventory.pop(0)
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, max_cleanup_checks=2, sleep=lambda _seconds: None)
    provisioner.created_project_ref = "r" * 20
    assert provisioner.cleanup()["deleted"] is True


def test_cleanup_malformed_post_delete_inventory_retains_the_exact_project_ref():
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    def api(method, path, _payload=None):
        if method == "DELETE" and path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20}
        if method == "GET" and path == "/v1/projects":
            return [None]
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, max_cleanup_checks=1, sleep=lambda _seconds: None)
    provisioner.created_project_ref = "r" * 20
    assert provisioner.cleanup()["retained_project_ref"] == "r" * 20


def test_known_cleanup_ref_malformed_inventory_never_claims_exact_ref_absent(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    first = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                      workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    payload = json.loads(identity.read_text()); payload["restore_project_ref"] = "r" * 20; identity.write_text(json.dumps(payload))
    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if method == "GET" and path == "/v1/projects":
            return [None]
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                             workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    assert first.created_project_ref is None
    assert provisioner.cleanup()["retained_project_ref"] == "r" * 20


def test_known_cleanup_ref_deletes_coming_up_project_despite_unrelated_future_status(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                              workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    payload = json.loads(identity.read_text()); payload["restore_project_ref"] = "r" * 20; identity.write_text(json.dumps(payload))
    name = payload["name"]
    inventories = [[
        {"ref": "r" * 20, "name": name, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "COMING_UP"},
        {"ref": "x" * 20, "name": "unrelated", "organization_id": "org-2", "organization_slug": "other", "region": "us-east-1", "status": "FUTURE_STATE"},
    ], []]
    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if method == "GET" and path == "/v1/projects":
            return inventories.pop(0)
        if method == "DELETE" and path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20}
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                             workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32,
                                             sleep=lambda _seconds: None, max_cleanup_checks=2)
    assert provisioner.cleanup()["deleted"] is True


def test_unknown_cleanup_ref_discovers_coming_up_project_despite_unrelated_future_status(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    original = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                         workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    name = json.loads(identity.read_text())["name"]
    inventories = [[
        {"ref": "r" * 20, "name": name, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "COMING_UP"},
        {"ref": "x" * 20, "name": "unrelated", "organization_id": "org-2", "organization_slug": "other", "region": "us-east-1", "status": "FUTURE_STATE"},
    ], []]
    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if method == "GET" and path == "/v1/projects":
            return inventories.pop(0)
        if method == "DELETE" and path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20}
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                             workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32,
                                             sleep=lambda _seconds: None, max_cleanup_checks=2)
    assert original.created_project_ref is None
    assert provisioner.cleanup()["deleted"] is True


def test_cleanup_fails_closed_on_lost_delete_response_but_discovers_only_deterministic_run_name(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    provisioner = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                            workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    assert identity.is_file()
    saved = json.loads(identity.read_text())
    assert saved["name"].startswith("stocks-recovery-") and "k" * 4 not in identity.read_text()


def test_actual_migration_retry_invokes_release_migration_contract_and_requires_no_applied(monkeypatch):
    from scripts.managed_isolated_restore import ManagedRestoreTarget
    import scripts.deploy_owner_dashboard_api as deploy

    seen = []
    def apply(cursor):
        seen.append(cursor)
        return {"candidate": [{"path": "sql/migrations/202609010001_x.sql", "version": "202609010001", "sha256": "a" * 64}],
                "applied": [], "skipped": [{"path": "sql/migrations/202609010001_x.sql", "version": "202609010001", "sha256": "a" * 64}]}
    monkeypatch.setattr(deploy, "apply_release_migrations", apply)
    target = ManagedRestoreTarget(lambda _method, _path, _payload=None: [], "r" * 20, "p" * 20, created_project_ref="r" * 20)
    assert target.retry_release_migrations() == {"applied": [], "skipped": ["202609010001"]}
    assert len(seen) == 1


def test_actual_migration_retry_accepts_truthful_baseline_without_writing_historical_rows(tmp_path, monkeypatch):
    import hashlib
    import scripts.deploy_owner_dashboard_api as deploy
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    path = "sql/reconciliation/20261004_production_schema_reconciliation.sql"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text("SELECT 1;")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    monkeypatch.setattr(
        deploy, "RECONCILIATION_BASELINE_RAW_SHA256",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    discovery = next(item for item in deploy.candidate_migration_manifest()
                     if item["path"] == "sql/migrations/20261005_market_wide_discovery.sql")
    transfer = next(item for item in deploy.candidate_migration_manifest()
                    if item["path"] == "sql/migrations/20261006_reference_snapshot_transfer.sql")
    cursor = next(item for item in deploy.candidate_migration_manifest()
                  if item["path"] == "sql/migrations/20261007_discovery_cursor_context.sql")
    official = next(item for item in deploy.candidate_migration_manifest()
                    if item["path"] == "sql/migrations/20261008_official_source_completion_contract.sql")
    issuer_names = next(item for item in deploy.candidate_migration_manifest()
                        if item["path"] == "sql/migrations/20261009_reference_issuer_names.sql")
    enrichment = next(item for item in deploy.candidate_migration_manifest()
                      if item["path"] == "sql/migrations/20261010_bounded_adaptive_enrichment.sql")
    research_packet = next(item for item in deploy.candidate_migration_manifest()
                           if item["path"] == "sql/migrations/20261011_research_suitability_packet_contract.sql")
    theme_memory = next(item for item in deploy.candidate_migration_manifest()
                        if item["path"] == "sql/migrations/20261012_theme_memory_research_nominations.sql")
    runtime_completion = next(item for item in deploy.candidate_migration_manifest()
                              if item["path"] == "sql/migrations/20261013_v2_runtime_completion.sql")
    honest_empty = next(item for item in deploy.candidate_migration_manifest()
                        if item["path"] == "sql/migrations/20261014_honest_empty_report_persistence.sql")
    release_reader = next(item for item in deploy.candidate_migration_manifest()
                          if item["path"] == "sql/migrations/20261015_release_reader_source_tables.sql")
    dashboard_authority = next(item for item in deploy.candidate_migration_manifest()
                               if item["path"] == "sql/migrations/20261016_dashboard_runtime_authority_closure.sql")
    release_reader_authority = next(item for item in deploy.candidate_migration_manifest()
                                   if item["path"] == "sql/migrations/20261017_release_reader_extension_closure.sql")
    run_order = next(item for item in deploy.candidate_migration_manifest()
                     if item["path"] == "sql/migrations/20261018_analysis_context_binding_lifecycle.sql")
    active_intelligence_policy = next(
        item for item in deploy.candidate_migration_manifest()
        if item["path"] == "sql/migrations/20261019_active_intelligence_policy.sql"
    )
    reference_transfer_restart = next(
        item for item in deploy.candidate_migration_manifest()
        if item["path"] == "sql/migrations/20261020_reference_transfer_restart.sql"
    )
    retry_task_capacity_recovery = next(
        item for item in deploy.candidate_migration_manifest()
        if item["path"] == "sql/migrations/20261021_retry_task_capacity_recovery.sql"
    )
    queries = []

    def api(_method, _path, payload=None):
        query = payload["query"]
        queries.append(query)
        if query.startswith("SELECT path, version, sha256"):
            return [
                {"path": path, "version": "20261004", "sha256": hashlib.sha256(b'["SELECT 1"]').hexdigest()},
                    discovery, transfer, cursor, official, issuer_names, enrichment,
                    research_packet, theme_memory, runtime_completion, honest_empty, release_reader,
                    dashboard_authority, release_reader_authority, run_order,
                    active_intelligence_policy, reference_transfer_restart,
                    retry_task_capacity_recovery,
            ]
        if query.startswith("SELECT version, statements"):
            return [{"version": "20261004", "statements": ["SELECT 1"]}]
        if query.startswith("CREATE TABLE IF NOT EXISTS public.stock_agent_release_migration_ledger"):
            return []
        raise AssertionError("baseline retry attempted unexpected SQL")

    target = ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20)
    receipt = target.retry_release_migrations()

    assert receipt["applied"] == []
    assert receipt["skipped"] == [item["version"] for item in deploy.candidate_migration_manifest()]
    assert len(queries) == 3


def test_management_restore_resets_transaction_sequence_like_the_existing_postgres_target():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    queries = []
    def api(_method, _path, payload=None):
        queries.append(payload["query"])
        if "restore_preflight" in payload["query"]:
            return [{"restore_preflight": {"tables_empty": True, "native_migrations_empty": True,
                                            "private_ledger_empty": True}}]
        return []
    ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20).restore_records(recovery_records())
    assert "SELECT setval(pg_get_serial_sequence('public.transactions','id'),(SELECT max(id) FROM public.transactions),true)" in queries[-1]


def test_management_restore_serializes_discovery_in_dependency_order():
    from scripts.managed_isolated_restore import ManagedRestoreTarget

    queries = []
    def api(_method, _path, payload=None):
        queries.append(payload["query"])
        if "restore_preflight" in payload["query"]:
            return [{"restore_preflight": {"tables_empty": True, "native_migrations_empty": True,
                                            "private_ledger_empty": True}}]
        return []

    ManagedRestoreTarget(api, "r" * 20, "p" * 20, created_project_ref="r" * 20).restore_records(recovery_records())
    restore_sql = queries[-1]
    tables = [
        "market_reference_manifests",
        "market_security_reference_revisions",
        "market_discovery_stage_tasks",
        "market_theme_episode_revisions",
        "market_exposure_facts",
        "market_research_nominations",
    ]
    positions = [restore_sql.index(f"INSERT INTO public.{table}") for table in tables]
    assert positions == sorted(positions)


def test_restarted_cleanup_discovers_exact_run_name_after_lost_delete_response_and_proves_absence(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    original = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                         workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    name = json.loads(identity.read_text())["name"]
    inventories = [[{"ref": "r" * 20, "name": name, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}], []]
    def api(method, path, _payload=None):
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path == "/v1/projects" and method == "GET":
            return inventories.pop(0)
        if method == "DELETE":
            raise OSError("response lost")
        raise AssertionError((method, path))
    restarted = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                          workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32,
                                          sleep=lambda _seconds: None, max_cleanup_checks=2)
    assert original.created_project_ref is None
    assert restarted.cleanup()["deleted"] is True


def test_unknown_ref_cleanup_waits_for_inventory_visibility_then_binds_and_deletes(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    original = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                         workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    name = json.loads(identity.read_text())["name"]
    inventories = [
        [],
        [{"ref": "r" * 20, "name": name, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1", "status": "ACTIVE_HEALTHY"}],
        [],
    ]
    calls = []
    def api(method, path, _payload=None):
        calls.append((method, path))
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path == "/v1/projects" and method == "GET":
            return inventories.pop(0)
        if method == "DELETE" and path == f"/v1/projects/{'r' * 20}":
            return {"ref": "r" * 20}
        raise AssertionError((method, path))

    restarted = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                          workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32,
                                          sleep=lambda _seconds: None, max_cleanup_checks=3)
    assert original.created_project_ref is None
    assert restarted.cleanup()["deleted"] is True
    assert ("DELETE", f"/v1/projects/{'r' * 20}") in calls


def test_unknown_ref_cleanup_proves_stable_inventory_absence_only_after_full_window(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                              workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    inventories = [[], [], []]
    list_calls = 0
    def api(method, path, _payload=None):
        nonlocal list_calls
        if path == f"/v1/projects/{'p' * 20}":
            return {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path == "/v1/projects" and method == "GET":
            list_calls += 1
            return inventories.pop(0)
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, cleanup_identity_path=identity,
                                             workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32,
                                             sleep=lambda _seconds: None, max_cleanup_checks=3)
    assert provisioner.cleanup() == {"attempted": False, "deleted": True, "retained_project_ref": None}
    assert list_calls == 3


def test_recovery_role_contract_requires_exact_dashboard_and_runtime_login_inherit_shapes():
    from scripts.export_recovery_bundle import _validated_records

    records = recovery_records()
    assert _validated_records(records)["roles"] == records["roles"]
    records["roles"][1]["inherit"] = False
    with pytest.raises(ValueError, match="role"):
        _validated_records(records)


def test_known_persisted_ref_requires_a_full_inventory_and_exact_deterministic_name_before_delete(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    first = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                      workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    payload = json.loads(identity.read_text()); payload["restore_project_ref"] = "r" * 20; identity.write_text(json.dumps(payload))
    provisioner = ManagedProjectProvisioner(lambda method, path, _payload=None: (
        {"ref": "p" * 20, "organization_id": "org-1", "organization_slug": "owner-org", "region": "us-east-1"}
        if path.endswith("/" + "p" * 20) else {"not": "a-list"}), "p" * 20,
        cleanup_identity_path=identity, workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    result = provisioner.cleanup()
    assert result["deleted"] is False and result["retained_project_ref"] == "r" * 20


def test_loading_cleanup_identity_recomputes_and_rejects_a_tampered_run_name(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                              workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    payload = json.loads(identity.read_text()); payload["name"] = "stocks-recovery-forged"; identity.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="deterministic"):
        ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                  workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)


def test_workflow_has_separate_always_cleanup_job_for_runner_loss():
    import yaml

    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/managed-isolated-restore.yml").read_text())
    cleanup = workflow["jobs"]["cleanup"]
    assert cleanup["needs"] == "restore" and cleanup["if"] == "${{ always() }}"
    assert cleanup["environment"] == "owner-dashboard-production"
    assert cleanup["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    bind = next(step["run"] for step in workflow["jobs"]["restore"]["steps"] if step.get("name", "").startswith("Bind checkout"))
    assert '"${GITHUB_REF:-}" = "refs/heads/main"' in bind
    assert '"${GITHUB_SHA:-}" = "$MAIN_SHA"' in bind
    cleanup_bind = next(step for step in cleanup["steps"] if step.get("name", "").startswith("Bind cleanup"))
    cleanup_run = cleanup_bind["run"]
    assert cleanup_bind.get("env") == {"GH_TOKEN": "${{ github.token }}"}
    assert '"${GITHUB_REF:-}" = "refs/heads/main"' in cleanup_run
    assert "head_sha=$GITHUB_SHA" in cleanup_run
    assert "Owner dashboard verification" in cleanup_run and "conclusion == \"success\"" in cleanup_run
    secret_step = next(step for step in cleanup["steps"] if step.get("name", "").startswith("Derive and clean"))
    assert cleanup["steps"].index(cleanup_bind) < cleanup["steps"].index(secret_step)
    project_ref_sources = [step["env"]["SUPABASE_PROJECT_REF"] for job in workflow["jobs"].values()
                           for step in job["steps"] if "SUPABASE_PROJECT_REF" in step.get("env", {})]
    assert project_ref_sources and set(project_ref_sources) == {"${{ secrets.SUPABASE_PROJECT_REF }}"}
    assert "vars.SUPABASE_PROJECT_REF" not in (Path(__file__).parents[1] / ".github/workflows/managed-isolated-restore.yml").read_text()
