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


def test_restore_receipt_is_bounded_and_never_serializes_secret_values_or_rows(tmp_path):
    from scripts.managed_isolated_restore import write_restore_receipt

    path = tmp_path / "receipt.json"
    write_restore_receipt(path, {
        "main_sha": "a" * 40, "main_tree": "b" * 40,
        "started_at": "2026-09-05T20:00:00Z", "completed_at": "2026-09-05T20:01:00Z",
        "production_project_ref": "p" * 20, "restore_project_ref": "r" * 20,
        "before_root_hash": "c" * 64, "after_root_hash": "c" * 64,
        "restore": {"status": "verified", "isolated": True, "restore_applied": True, "record_set_count": 26},
        "migration_retry": {"applied": [], "skipped": ["schema"]},
        "artifacts": {"first": {"path": "recovery/first.enc", "sha256": "d" * 64}},
        "workflow": {"run_id": "99", "attempt": "1"},
        "cleanup": {"attempted": True, "deleted": True, "retained_project_ref": None},
        "forbidden": {"password": "super-secret", "rows": [{"ticker": "VTI"}]},
    })
    receipt = json.loads(path.read_text())
    assert receipt["restore"]["record_set_count"] == 26
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
    assert len(calls) == 1
    assert calls[0][1] == f"/v1/projects/{'r' * 20}/database/query"


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
    target = ManagedRestoreTarget(lambda _method, _path, payload=None: queries.append(payload["query"]) or [],
                                  "r" * 20, "p" * 20, created_project_ref="r" * 20)
    target.reproduce_role_shapes([
        {"role": "stock_agent_dashboard", "login": False, "inherit": False, "superuser": False, "bypass_rls": False,
         "memberships": [], "grants": []},
        {"role": "stock_agent_dashboard_runtime", "login": True, "inherit": True, "superuser": False, "bypass_rls": False,
         "memberships": ["stock_agent_dashboard"], "grants": []},
    ])
    assert "ALTER ROLE stock_agent_dashboard NOLOGIN" in queries[-1]
    assert "ALTER ROLE stock_agent_dashboard_runtime LOGIN INHERIT PASSWORD NULL" in queries[-1]


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

    inventory = [[{"ref": "r" * 20}], []]
    def api(method, path, _payload=None):
        if method == "DELETE":
            return {"ref": "r" * 20}
        if method == "GET" and path == "/v1/projects":
            return inventory.pop(0)
        raise AssertionError((method, path))

    provisioner = ManagedProjectProvisioner(api, "p" * 20, max_cleanup_checks=2, sleep=lambda _seconds: None)
    provisioner.created_project_ref = "r" * 20
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


def test_restarted_cleanup_discovers_exact_run_name_after_lost_delete_response_and_proves_absence(tmp_path):
    from scripts.managed_isolated_restore import ManagedProjectProvisioner

    identity = tmp_path / "cleanup.json"
    original = ManagedProjectProvisioner(lambda *_args: [], "p" * 20, cleanup_identity_path=identity,
                                         workflow_run_id="42", workflow_attempt="3", cleanup_key=b"k" * 32)
    name = json.loads(identity.read_text())["name"]
    inventories = [[{"ref": "r" * 20, "name": name, "organization_slug": "owner-org", "region": "us-east-1"}], []]
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
