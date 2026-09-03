from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ops.backup.export_backup import collect_archive
from ops.backup.restore_backup import remap_archive_owners, restore_database
from ops.backup.verify_archive import DATASET_SPECS, verify_archive


BACKUP_FUNCTIONS = {
    "machine.backup_export_catalog(jsonb)",
    "machine.backup_export_dataset(jsonb)",
}


def _call_json(tenant_database, role: str, function: str, request: dict):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(f"SET LOCAL ROLE {role}")
        row = tenant_database.execute(
            f"SELECT {function}(%s::jsonb)", (json.dumps(request),)
        ).fetchone()
        return row[0]


def test_backup_role_is_execute_only_and_cannot_bypass_private_schemas(tenant_database):
    role = tenant_database.execute(
        """
        SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
        FROM pg_roles WHERE rolname = 'stock_agent_backup'
        """
    ).fetchone()
    assert role == (False, False, False, False, False)

    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("SET LOCAL ROLE stock_agent_backup")
        for relation in (
            "app.profiles",
            "app.transactions",
            "app.agent_connections",
            "auth.users",
            "vault.secrets",
            "vault.decrypted_secrets",
        ):
            with tenant_database.transaction(force_rollback=True):
                with pytest.raises(Exception, match="permission denied"):
                    tenant_database.execute(f"SELECT * FROM {relation}").fetchall()


def test_backup_role_has_only_the_two_enumerated_export_rpcs(tenant_database):
    executable = tenant_database.execute(
        """
        SELECT p.oid::regprocedure::text
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'machine'
          AND has_function_privilege('stock_agent_backup', p.oid, 'EXECUTE')
        """
    ).fetchall()
    assert {row[0] for row in executable} == BACKUP_FUNCTIONS


def test_backup_catalog_accounts_for_every_app_table(tenant_database):
    result = _call_json(
        tenant_database,
        "stock_agent_backup",
        "machine.backup_export_catalog",
        {"schema_version": 1},
    )
    actual_tables = {
        row[0]
        for row in tenant_database.execute(
            """
            SELECT c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'app' AND c.relkind = 'r'
            """
        ).fetchall()
    }
    declared = {item["name"] for item in result["tables"]}
    assert result["schema_version"] == 1
    assert actual_tables == declared
    assert {item["disposition"] for item in result["tables"]} <= {
        "include",
        "exclude_transient",
        "exclude_rebuildable",
    }


def test_backup_dataset_export_redacts_secrets_and_identity_is_minimal(tenant_database):
    connections = _call_json(
        tenant_database,
        "stock_agent_backup",
        "machine.backup_export_dataset",
        {"schema_version": 1, "dataset": "agent_connections"},
    )
    assert connections["dataset"] == "agent_connections"
    assert connections["rows"]
    for row in connections["rows"]:
        assert "inbound_token_digest" not in row
        assert "outbound_trigger_secret_id" not in row
        assert "trigger_url" not in row
        assert row["status"] == "disabled"
        assert row["last_handshake_at"] is None

    identities = _call_json(
        tenant_database,
        "stock_agent_backup",
        "machine.backup_export_dataset",
        {"schema_version": 1, "dataset": "identity_recovery"},
    )
    assert identities["columns"] == ["email", "owner_id"]
    assert identities["rows"]
    assert all(set(row) == {"email", "owner_id"} for row in identities["rows"])


def test_real_backup_snapshot_is_complete_deterministic_and_secret_free(tenant_database):
    archive = collect_archive(
        tenant_database,
        exported_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    verified = verify_archive(archive)
    assert set(verified["datasets"]) == set(DATASET_SPECS)
    assert verified["datasets"]["profiles"]["count"] == 2
    assert verified["identity_recovery"] == [
        {"owner_id": "11111111-1111-4111-8111-111111111111", "email": "a@example.com"},
        {"owner_id": "22222222-2222-4222-8222-222222222222", "email": "b@example.com"},
    ]


def test_verified_archive_restores_into_a_staging_transaction(tenant_database):
    archive = collect_archive(
        tenant_database,
        exported_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    identity_map = {
        row["owner_id"]: row["owner_id"] for row in archive["identity_recovery"]
    }
    remapped = remap_archive_owners(archive, identity_map)
    with tenant_database.transaction(force_rollback=True):
        receipt = restore_database(tenant_database, remapped_archive=remapped)
        assert receipt["status"] == "verified"
        assert receipt["requires_provider_reconnect"] is True
        assert tenant_database.execute(
            "SELECT count(*) FROM app.backup_restore_receipts WHERE status = 'verified'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "payload",
    (
        {"schema_version": 2},
        {"schema_version": 1, "extra": True},
    ),
)
def test_backup_catalog_fails_closed_on_bad_contract(tenant_database, payload):
    with pytest.raises(Exception, match="backup contract unavailable"):
        _call_json(
            tenant_database,
            "stock_agent_backup",
            "machine.backup_export_catalog",
            payload,
        )


def test_backup_catalog_fails_closed_on_unreviewed_table_or_column(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "CREATE TABLE app.unreviewed_backup_payload (id uuid PRIMARY KEY, plaintext_secret text)"
        )
        with pytest.raises(Exception, match="app table drift"):
            _call_json(
                tenant_database,
                "stock_agent_backup",
                "machine.backup_export_catalog",
                {"schema_version": 1},
            )

    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("ALTER TABLE app.profiles ADD COLUMN unreviewed_secret text")
        with pytest.raises(Exception, match="app column drift"):
            _call_json(
                tenant_database,
                "stock_agent_backup",
                "machine.backup_export_catalog",
                {"schema_version": 1},
            )


def test_other_runtime_roles_cannot_call_backup_exports(tenant_database):
    for role in (
        "anon",
        "authenticated",
        "service_role",
        "stock_agent_gateway",
        "stock_agent_scheduler",
        "stock_agent_telegram",
    ):
        with pytest.raises(Exception, match="permission denied"):
            _call_json(
                tenant_database,
                role,
                "machine.backup_export_catalog",
                {"schema_version": 1},
            )
