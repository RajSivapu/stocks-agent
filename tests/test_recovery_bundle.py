import copy
import hashlib
import json
import shlex
import sys
import io
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import tempfile

import psycopg
from psycopg.rows import dict_row
import pytest

from scripts.export_recovery_bundle import _validated_records, export_recovery_bundle, decrypt_verified
from scripts.protected_evidence import RECOVERY_SQL
from scripts.verify_recovery_bundle import restore_recovery_records, verify_recovery_bundle


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recovery_records():
    run = "11111111-1111-4111-8111-111111111111"
    packet_id = "22222222-2222-4222-8222-222222222222"
    report_id = "33333333-3333-4333-8333-333333333333"
    command_id = "44444444-4444-4444-8444-444444444444"
    evaluation_request = "55555555-5555-4555-8555-555555555555"
    publication_id = "66666666-6666-4666-8666-666666666666"
    delivery_lease = "77777777-7777-4777-8777-777777777777"
    started_event = "88888888-8888-4888-8888-888888888888"
    completion_event = "99999999-9999-4999-8999-999999999999"
    report_request = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    reservation = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    source_receipt = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    packet = {"candidates": [], "evidence": [], "coverage": {}, "limitations": [], "policy_version": 1}
    report = {"summary": "Suggestion only.", "packet_hash": digest(packet)}
    records = {
        "holdings": [{"ticker": "VTI", "shares": "2", "average_cost": "100"}],
        "transactions": [{"id": "1", "ticker": "VTI", "quantity": "2", "price": "100"}],
        "commands": [{"id": command_id, "status": "applied"}],
        "command_acknowledgements": [{
            "command_id": command_id, "telegram_update_id": "1", "status": "uncertain",
            "result": {"ok": True}, "error": "ACKNOWLEDGEMENT_LEASE_EXPIRED",
            "lease_token": None, "lease_expires_at": None, "attempt_count": 1,
            "created_at": "2026-09-01T12:00:00Z", "updated_at": "2026-09-01T12:06:00Z",
        }],
        "runs": [{"id": run, "status": "running", "phase": "post-market"}],
        "gateway_requests": [{
            "request_id": evaluation_request, "operation": "evaluate_and_publish", "run_id": run,
            "status": "claimed", "lease_token": delivery_lease, "attempt_count": 1,
            "response": None, "response_digest": None, "created_at": "2026-09-05T19:30:00Z",
            "claimed_at": "2026-09-05T19:30:00Z", "finished_at": None,
        }, {
            "request_id": report_request, "operation": "record_report", "run_id": None,
            "status": "completed", "lease_token": delivery_lease, "attempt_count": 1,
            "response": {"report_id": report_id, "report_hash": digest(report),
                         "rendered_hash": hashlib.sha256(b"Suggestion only.").hexdigest()},
            "response_digest": None, "created_at": "2026-09-05T19:57:00Z",
            "claimed_at": "2026-09-05T19:57:00Z", "finished_at": "2026-09-05T19:59:00Z",
        }],
        "policies": [{"version": 1, "config": {"intelligence": {}}, "active": True,
                      "created_at": "2026-09-01T00:00:00Z", "activated_at": "2026-09-01T00:00:00Z"}],
        "intelligence_runs": [{
            "id": run, "phase": "post-market", "market_date": "2026-09-05", "policy_version": 1,
            "reservation_plan": {"reservations": []}, "request_window": {
                "start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z",
                "timezone": "America/Chicago", "market_date": "2026-09-05", "phase": "post-market",
            }, "created_at": "2026-09-05T19:30:00Z",
        }],
        "intelligence_run_events": [
            {"id": started_event, "run_id": run, "status": "started", "detail": {},
             "created_at": "2026-09-05T19:30:00Z"},
            {"id": completion_event, "run_id": run, "status": "completed",
             "detail": {"packet_id": packet_id}, "created_at": "2026-09-05T19:45:00Z"},
        ],
        "source_quota_reservations": [{
            "id": reservation, "run_id": run, "provider": "gdelt", "market_date": "2026-09-05",
            "phase": "post-market", "reserved_requests": 1, "cache_keys": ["d" * 64],
            "created_at": "2026-09-05T19:30:00Z",
        }],
        "collection_checkpoints": [{
            "run_id": run, "cache_key": "d" * 64,
            "request_window": {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z",
                               "timezone": "America/Chicago", "market_date": "2026-09-05", "phase": "post-market"},
            "source_receipt_id": source_receipt,
            "payload": {"receipt": {"provider": "gdelt", "reservation_id": reservation,
                                      "status": "succeeded", "request_cost": 1}, "items": []},
            "created_at": "2026-09-05T19:40:00Z",
        }],
        "collection_checkpoint_history": [],
        "collection_completions": [{
            "completion_id": completion_event, "run_id": run, "payload": {"receipts": []},
            "receipt": {"packet_id": packet_id, "packet_hash": digest(packet)},
            "created_at": "2026-09-05T19:45:00Z",
        }],
        "packets": [{"id": packet_id, "run_id": run, "policy_version": 1, "status": "completed",
                     "candidate_count": 0, "evidence_count": 0, "packet_hash": digest(packet), "packet": packet,
                     "created_at": "2026-09-05T19:45:00Z"}],
        "reports": [{"id": report_id, "run_id": run, "packet_id": packet_id, "idempotency_key": "a" * 64,
                     "market_date": "2026-09-05", "kind": "weekly", "report_hash": digest(report),
                     "report": report, "rendered_hash": hashlib.sha256(b"Suggestion only.").hexdigest(),
                     "rendered_text": "Suggestion only.", "created_at": "2026-09-05T19:58:00Z"}],
        "report_origins": [{
            "request_id": report_request, "run_id": run, "scheduled_phase": "post-market",
            "market_date": "2026-09-05", "requested_kind": "weekly",
            "requested_report_id": report_id, "requested_packet_id": packet_id,
            "requested_idempotency_key": "a" * 64, "requested_report_hash": digest(report),
            "created_at": "2026-09-05T19:57:00Z",
        }],
        "publications": [{"report_id": report_id, "idempotency_key": "a" * 64, "status": "delivered", "telegram_message_ids": [7],
                          "telegram_accepted_at": "2026-09-05T20:00:00Z", "suppression_reason": None,
                          "attempt_count": 1, "lease_token": None, "lease_expires_at": None, "error": None,
                          "created_at": "2026-09-05T19:59:00Z", "updated_at": "2026-09-05T20:00:00Z"}],
        "evaluation_publications": [{
            "id": publication_id, "idempotency_key": evaluation_request, "run_id": run,
            "market_date": "2026-09-05", "phase": "post-market", "kind": "brief",
            "template_version": 2, "rendered_body": "Sending suggestion-only brief.",
            "rendered_hash": hashlib.sha256(b"Sending suggestion-only brief.").hexdigest(),
            "status": "sending", "telegram_message_ids": [], "attempt_count": 1,
            "lease_token": delivery_lease, "sending_started_at": "2026-09-05T20:00:00Z",
            "delivered_at": None, "telegram_accepted_at": None, "error": None,
            "created_at": "2026-09-05T19:59:00Z", "updated_at": "2026-09-05T20:00:00Z",
        }],
        "cash_ledger_state": [{"singleton": True, "revision": "0", "updated_at": "2026-09-05T19:00:00Z"}],
        "cash_snapshots": [],
        "run_terminal_outcomes": [],
        "decision_evaluations": [{
            "id": "abcdef01-1111-4111-8111-111111111111", "request_id": evaluation_request,
            "run_id": run, "candidate_id": "abcdef02-1111-4111-8111-111111111111", "policy_version": 1,
            "input_digest": "a" * 64, "raw_action": "watch", "final_action": "watch", "policy_status": "approved",
            "reason_codes": [], "explanations": [], "normalized": {}, "evidence": [], "analyst": {}, "checker": {},
            "created_at": "2026-09-05T19:55:00Z",
        }],
        "policy_comparisons": [{
            "id": "abcdef03-1111-4111-8111-111111111111", "run_id": run, "packet_id": packet_id,
            "evaluation_id": "abcdef01-1111-4111-8111-111111111111", "comparison": {"advisory": True},
            "created_at": "2026-09-05T19:56:00Z",
        }],
        "roles": [
            {"role": "stock_agent_dashboard", "login": False, "inherit": False, "superuser": False, "bypass_rls": False,
             "memberships": [], "grants": ["SELECT:public.holdings"]},
            {"role": "stock_agent_dashboard_runtime", "login": True, "inherit": True, "superuser": False, "bypass_rls": False,
             "memberships": ["stock_agent_dashboard"], "grants": []},
        ],
        "schema_version": [{"version": "20260926", "statements": ["SELECT 1"],
                            "sha256": hashlib.sha256(b"SELECT 1").hexdigest()}],
        "release_migration_ledger": [],
    }
    records["holdings"][0].update(bucket="core", opened_at="2026-09-01", notes=None, stop=None, target=None, high_water_price=None,
                                  stop_alert_active=False, hold_override_until=None, stop_near_alert_active=False, target_near_alert_active=False, target_alert_active=False)
    records["transactions"][0].update(ts="2026-09-01T12:00:00Z", side="buy", source="owner", executed_on="2026-09-01")
    records["commands"][0].update(telegram_update_id="1", chat_id="2", user_id="3", operation="buy", ticker="VTI", qty="2", price="100",
                                  executed_on="2026-09-01", bucket="core", expected_shares="0", stop=None,
                                  amount=None, cadence=None, next_due_on=None, expected_plan_updated_at=None,
                                  preview={}, confirmation_message_id="4",
                                  expires_at="2026-09-01T12:15:00Z", applied_at="2026-09-01T12:00:00Z", realized_pnl=None, result={}, error=None,
                                  created_at="2026-09-01T11:59:00Z", updated_at="2026-09-01T12:00:00Z")
    records["runs"][0].update(
        started_at="2026-09-05T19:30:00Z", finished_at=None, data_as_of="2026-09-05T19:30:00Z",
        source_status={}, symbols=[], write_counts={}, telegram_message_ids=[], summary=None, error=None,
        scheduled_phase="post-market", scheduled_market_date="2026-09-05",
        gateway_request_id="55555555-5555-4555-8555-555555555555",
    )
    return records


class FakeDatabase:
    def __init__(self, records=None, *, project="p" * 20, connection="production-server/database", isolated=False):
        self.records = copy.deepcopy(records if records is not None else recovery_records())
        self.identity_value = {"project_ref": project, "connection_id": connection, "read_only": True, "isolated_guard": isolated}
        self.reported_counts = {name: len(rows) for name, rows in self.records.items()}

    def identity(self):
        return dict(self.identity_value)

    def read_records(self):
        return copy.deepcopy(self.records)

    def counts(self):
        return dict(self.reported_counts)


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK10_TEST_KEY", "12" * 32)
    script = tmp_path / "crypt.py"
    script.write_text("""import os,sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
key=bytes.fromhex(os.environ['TASK10_TEST_KEY'])
source=Path(sys.argv[2]).read_bytes()
if sys.argv[1]=='encrypt':
 nonce=os.urandom(12); result=nonce+AESGCM(key).encrypt(nonce,source,b'stocks-recovery')
else:
 result=AESGCM(key).decrypt(source[:12],source[12:],b'stocks-recovery')
Path(sys.argv[3]).write_bytes(result)
""")
    prefix = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    return {"encrypt_command": prefix + " encrypt {input} {output}", "decrypt_command": prefix + " decrypt {input} {output}"}


def restored(source):
    return FakeDatabase(source.records, project="r" * 20, connection="restore-server/database", isolated=True)


def test_recovery_exports_canonical_data_and_queries_isolated_restore(tmp_path, commands):
    source = FakeDatabase()
    artifact = export_recovery_bundle(source, tmp_path / "bundle.enc", **commands)
    sidecar = json.loads(artifact.with_suffix(".enc.receipt.json").read_text())
    assert "VTI" not in json.dumps(sidecar)
    assert verify_recovery_bundle(artifact, restored(source), production_source=source, decrypt_command=commands["decrypt_command"])["status"] == "verified"


def test_recovery_payload_carries_identity_delivery_and_release_state(tmp_path, commands):
    artifact = export_recovery_bundle(FakeDatabase(), tmp_path / "bundle.enc", **commands)
    with tempfile.TemporaryDirectory(prefix="recovery-payload-test-") as temporary:
        _manifest, records = decrypt_verified(
            artifact, commands["decrypt_command"], Path(temporary).resolve(),
        )
    assert records["intelligence_runs"][0]["id"] == records["runs"][0]["id"]
    assert {"idempotency_key", "market_date", "kind"}.issubset(records["reports"][0])
    assert records["command_acknowledgements"][0]["attempt_count"] == 1
    publication = records["evaluation_publications"][0]
    assert (publication["status"], publication["attempt_count"], publication["lease_token"]) == (
        "sending", 1, "77777777-7777-4777-8777-777777777777",
    )
    assert records["schema_version"][0]["statements"] == ["SELECT 1"]
    assert records["release_migration_ledger"] == []
    assert set(records) >= {
        "intelligence_run_events", "source_quota_reservations", "collection_checkpoints",
        "collection_checkpoint_history", "collection_completions", "report_origins",
        "cash_ledger_state", "cash_snapshots", "run_terminal_outcomes",
    }


@pytest.mark.parametrize("change", [
    lambda row: row.update(path="../escape.sql"),
    lambda row: row.update(version="20260101"),
    lambda row: row.update(sha256="not-a-hash"),
    lambda row: row.update(applied_at="not-a-timestamp"),
    lambda row: row.update(statements=["SELECT 1"]),
    lambda row: row.update(password="forbidden"),
])
def test_private_migration_ledger_requires_exact_no_secret_identity(change):
    from scripts.deploy_owner_dashboard_api import migration_statements_sha256
    records = recovery_records()
    row = {"path": "sql/migrations/20260926_recovery.sql", "version": "20260926",
           "sha256": migration_statements_sha256(["SELECT 1"]), "applied_at": "2026-09-05T20:00:00Z"}
    records["release_migration_ledger"] = [row]
    assert _validated_records(records)["release_migration_ledger"] == [row]
    change(row)
    with pytest.raises(ValueError):
        _validated_records(records)


def test_private_and_native_migration_ledgers_must_agree():
    records = recovery_records()
    records["release_migration_ledger"] = [{
        "path": "sql/migrations/20260926_recovery.sql", "version": "20260926",
        "sha256": "a" * 64, "applied_at": "2026-09-05T20:00:00Z",
    }]
    with pytest.raises(ValueError, match="migration.*diverge"):
        _validated_records(records)


def test_recovery_accepts_historical_cash_snapshots_below_the_current_ledger_revision():
    records = recovery_records()
    records["cash_ledger_state"][0]["revision"] = "2"
    records["cash_snapshots"] = [
        {"id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "as_of": "2026-09-05T18:00:00Z",
         "fresh_through": "2026-09-05T18:15:00Z", "ledger_watermark": "1",
         "core_available": "100", "growth_available": "50", "speculative_available": "25",
         "created_at": "2026-09-05T18:00:00Z"},
        {"id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "as_of": "2026-09-05T19:00:00Z",
         "fresh_through": "2026-09-05T19:15:00Z", "ledger_watermark": "2",
         "core_available": "90", "growth_available": "50", "speculative_available": "25",
         "created_at": "2026-09-05T19:00:00Z"},
    ]
    assert len(_validated_records(records)["cash_snapshots"]) == 2


def test_recovery_accepts_report_persisted_before_its_outbox_row(tmp_path, commands):
    source = FakeDatabase()
    source.records["publications"] = []
    source.reported_counts["publications"] = 0
    assert export_recovery_bundle(source, tmp_path / "report-crash.enc", **commands).is_file()


def test_restore_refreshes_the_isolated_reader_snapshot_before_reconciliation(tmp_path, commands):
    production = FakeDatabase()
    artifact = export_recovery_bundle(production, tmp_path / "refresh.enc", **commands)

    class SnapshotReader(FakeDatabase):
        def __init__(self):
            super().__init__(records={name: [] for name in recovery_records()})
            self.refreshed = False

        def identity(self):
            return {"project_ref": "r" * 20, "connection_id": "restore-db", "read_only": True,
                    "isolated_guard": True}

        def refresh_snapshot(self):
            self.records = copy.deepcopy(production.records)
            self.reported_counts = {key: len(value) for key, value in self.records.items()}
            self.refreshed = True

    reader = SnapshotReader()

    class Target:
        def identity(self):
            return {"project_ref": "r" * 20, "connection_id": "restore-db", "read_only": False,
                    "restore_capable": True, "isolated_guard": True}

        def restore_records(self, _records):
            return None

    result = verify_recovery_bundle(
        artifact, reader, production_source=production,
        decrypt_command=commands["decrypt_command"], restore_target=Target(),
    )
    assert result["restore_applied"] is True
    assert reader.refreshed is True


def test_verifier_applies_actual_isolated_postgres_restore_and_retains_uncertain_delivery(tmp_path, commands):
    from scripts.deploy_owner_dashboard_api import (
        apply_release_migrations, candidate_migration_manifest, normalize_migration_statements,
    )
    from psycopg.rows import tuple_row
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="recovery-postgres-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        subprocess.run(
            [binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"],
            check=True, capture_output=True,
        )
        subprocess.run(
            [binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
             "-o", f"-k {root} -h '' -p {port}", "-w", "start"],
            check=True, capture_output=True,
        )
        connections = []
        try:
            admin_dsn = f"host={root} port={port} dbname=postgres"
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
                admin.execute("CREATE DATABASE recovery_source")
                admin.execute("CREATE DATABASE recovery_restore")
            repo = Path(__file__).resolve().parents[1]
            schema = (repo / "sql/schema.sql").read_text()
            audited_base = "432d647ef911ff63da427097f02a852e18038b62"
            baseline_schema = subprocess.check_output(["git", "show", f"{audited_base}:sql/schema.sql"], cwd=repo, text=True)
            for database in ("recovery_source", "recovery_restore"):
                connection = psycopg.connect(
                    f"host={root} port={port} dbname={database}", autocommit=True, row_factory=dict_row,
                )
                connections.append(connection)
                connection.execute("CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'")
                connection.execute("CREATE SCHEMA supabase_migrations; CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY, statements text[])")
                connection.execute(baseline_schema if database == "recovery_source" else schema)
                connection.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='stock_agent_dashboard_runtime') THEN CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT PASSWORD NULL NOSUPERUSER NOBYPASSRLS; END IF; END $$")
                connection.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")

            class DatabaseSource:
                def __init__(self, connection, project, isolated=False):
                    self.connection, self.project, self.isolated = connection, project, isolated

                def identity(self):
                    return {"project_ref": self.project, "connection_id": self.connection.info.dbname,
                            "read_only": True, "isolated_guard": self.isolated}

                def read_records(self):
                    return {name: [dict(row) for row in self.connection.execute(sql).fetchall()]
                            for name, sql in RECOVERY_SQL.items()}

                def counts(self):
                    return {name: self.connection.execute(f"SELECT count(*) FROM ({sql}) records").fetchone()["count"]
                            for name, sql in RECOVERY_SQL.items()}

            source = DatabaseSource(connections[0], "p" * 20)
            restored_source = DatabaseSource(connections[1], "r" * 20, isolated=True)
            records = recovery_records()
            manifest = candidate_migration_manifest()
            baseline = set(subprocess.check_output(
                ["git", "ls-tree", "--name-only", "432d647ef911ff63da427097f02a852e18038b62", "sql/migrations/"],
                cwd=repo, text=True).splitlines())
            # Start with the native prefix; execute the actual additive release
            # suffix before exporting its immutable private ledger evidence.
            for item in manifest:
                if item["path"] in baseline:
                    connections[0].execute("INSERT INTO supabase_migrations.schema_migrations VALUES (%s,%s)",
                        (item["version"], normalize_migration_statements((repo / item["path"]).read_text())))
            with connections[0].transaction(), connections[0].cursor(row_factory=tuple_row) as cursor:
                upgrade = apply_release_migrations(cursor)
            assert upgrade["applied"] == [item for item in manifest if item["path"] not in baseline]
            assert upgrade["skipped"] == [item for item in manifest if item["path"] in baseline]
            # Seed business fixtures in the genuinely upgraded source; never
            # manufacture, clear, or replace its native/private migration receipts.
            from scripts.verify_recovery_bundle import _RESTORE_TABLES
            for dataset, table, renames in _RESTORE_TABLES:
                if dataset == "release_migration_ledger":
                    continue
                for source_row in records[dataset]:
                    row = {renames.get(key, key): value for key, value in source_row.items()}
                    if dataset == "runs":
                        row["gateway_request_id"] = None
                    connections[0].execute(
                        f"INSERT INTO public.{table} SELECT * FROM json_populate_record(NULL::public.{table}, %s::json)",
                        (json.dumps(row),))
            for row in records["runs"]:
                connections[0].execute("UPDATE analysis_runs SET gateway_request_id=%s WHERE id=%s",
                    (row["gateway_request_id"], row["id"]))
            from scripts.protected_evidence import READ_TABLES
            assert "stock_agent_release_migration_ledger" in READ_TABLES
            for connection in connections:
                assert connection.execute("SELECT has_table_privilege('stock_agent_release_reader_runtime', 'public.stock_agent_release_migration_ledger', 'SELECT') AS allowed").fetchone()["allowed"]
                assert not connection.execute("SELECT has_table_privilege('stock_agent_release_reader_runtime', 'public.stock_agent_release_migration_ledger', 'INSERT,UPDATE,DELETE') AS allowed").fetchone()["allowed"]
            connections[0].execute("SET ROLE stock_agent_release_reader_runtime")
            try:
                ledger = connections[0].execute(RECOVERY_SQL["release_migration_ledger"]).fetchall()
                assert len(ledger) == len(manifest)
                assert all(set(row) == {"path", "version", "sha256", "applied_at"} for row in ledger)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connections[0].execute("DELETE FROM public.stock_agent_release_migration_ledger")
            finally:
                connections[0].execute("RESET ROLE")
            artifact = export_recovery_bundle(source, tmp_path / "actual.enc", **commands)

            class Target:
                def identity(self):
                    return {**restored_source.identity(), "read_only": False, "restore_capable": True}

                def restore_records(self, records):
                    restore_recovery_records(connections[1], records, isolated_guard=True)

            result = verify_recovery_bundle(
                artifact, restored_source, production_source=source,
                decrypt_command=commands["decrypt_command"], restore_target=Target(),
            )
            restored_delivery = connections[1].execute(
                "SELECT status,attempt_count,lease_token::text FROM market_publications"
            ).fetchone()
            assert result["restore_applied"] is True
            before = restored_source.read_records()
            with connections[1].transaction(), connections[1].cursor(row_factory=tuple_row) as cursor:
                retry = apply_release_migrations(cursor)
            assert retry == {"applied": [], "skipped": manifest, "candidate": manifest}
            assert restored_source.read_records() == before
            assert before["release_migration_ledger"] == source.read_records()["release_migration_ledger"]
            assert tuple(restored_delivery.values()) == (
                "sending", 1, "77777777-7777-4777-8777-777777777777",
            )
            for table in (
                "market_intelligence_run_events", "market_source_quota_reservations",
                "market_collection_checkpoints", "market_intelligence_collection_completions",
                "market_report_request_origins",
            ):
                assert connections[1].execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"] >= 1
        finally:
            for connection in connections:
                connection.close()
            subprocess.run(
                [binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True,
            )


@pytest.mark.parametrize("mutation", [
    lambda db: db.records["roles"][0].update(password="secret"),
    lambda db: db.records.update(reports=[]),
    lambda db: db.records.update(publications=[]),
    lambda db: db.records["reports"][0].update(packet_id="55555555-5555-4555-8555-555555555555"),
    lambda db: db.records["packets"][0]["packet"].update(policy_version=99),
    lambda db: db.records["publications"][0].update(report_id="55555555-5555-4555-8555-555555555555"),
    lambda db: db.records.update(intelligence_runs=[]),
    lambda db: db.records["command_acknowledgements"][0].update(command_id="55555555-5555-4555-8555-555555555555"),
    lambda db: db.records["evaluation_publications"][0].update(lease_token=None),
    lambda db: db.records["schema_version"][0]["statements"].append("SELECT 2"),
    lambda db: db.records.pop("release_migration_ledger"),
    lambda db: db.records.update(cash_ledger_state=[]),
    lambda db: db.reported_counts.update(holdings=2),
])
def test_export_rejects_incomplete_or_unreconciled_sources(tmp_path, commands, mutation):
    source = FakeDatabase(); mutation(source)
    with pytest.raises((ValueError, RuntimeError)):
        export_recovery_bundle(source, tmp_path / "bundle.enc", **commands)
    assert not (tmp_path / "bundle.enc").exists()


@pytest.mark.parametrize("mutation", [
    lambda db: db.identity_value.update(project_ref=""),
    lambda db: db.identity_value.update(project_ref="p" * 20),
    lambda db: db.identity_value.update(connection_id=""),
    lambda db: db.identity_value.update(connection_id="production-server/database"),
    lambda db: db.identity_value.update(isolated_guard=False),
    lambda db: db.identity_value.update(read_only=False),
    lambda db: db.records.update(packets=[]),
    lambda db: db.records["roles"][0].update(login=True),
    lambda db: db.records["schema_version"][0].update(sha256="e" * 64),
    lambda db: db.records.pop("release_migration_ledger"),
])
def test_recovery_rejects_fake_isolation_or_incomplete_restore(tmp_path, commands, mutation):
    production = FakeDatabase()
    artifact = export_recovery_bundle(production, tmp_path / "bundle.enc", **commands)
    restore = restored(production); mutation(restore)
    with pytest.raises((ValueError, RuntimeError)):
        verify_recovery_bundle(artifact, restore, production_source=production, decrypt_command=commands["decrypt_command"])


@pytest.mark.parametrize("dataset,field,value", [("transactions", "side", "sell"), ("commands", "result", {"transaction_id": "99"}), ("holdings", "stop", "50")])
def test_restore_reconciles_ledger_semantics_not_only_ids(tmp_path, commands, dataset, field, value):
    production = FakeDatabase(); restore = restored(production)
    artifact = export_recovery_bundle(production, tmp_path / "bundle.enc", **commands)
    restore.records[dataset][0][field] = value
    with pytest.raises(RuntimeError, match="restored"):
        verify_recovery_bundle(artifact, restore, production_source=production, decrypt_command=commands["decrypt_command"])


def test_recovery_never_accepts_caller_json_as_database_authority(tmp_path, commands):
    with pytest.raises((ValueError, RuntimeError), match="source"):
        export_recovery_bundle(recovery_records(), tmp_path / "bundle.enc", **commands)


def test_copy_commands_are_not_encryption_and_leave_no_external_artifact(tmp_path):
    command = f"{shlex.quote(sys.executable)} -c 'import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])' {{input}} {{output}}"
    with pytest.raises(RuntimeError, match="encrypt|plaintext|authenticated"):
        export_recovery_bundle(FakeDatabase(), tmp_path / "bundle.enc", encrypt_command=command, decrypt_command=command)
    assert not (tmp_path / "bundle.enc").exists()
    assert not (tmp_path / "bundle.enc.receipt.json").exists()


@pytest.mark.parametrize("failure", ["encrypt", "decrypt"])
def test_encryption_failures_remove_external_artifact(tmp_path, commands, failure):
    failing = f"{shlex.quote(sys.executable)} -c 'import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(b\"partial\");sys.exit(1)' {{input}} {{output}}"
    commands[f"{failure}_command"] = failing
    with pytest.raises(RuntimeError):
        export_recovery_bundle(FakeDatabase(), tmp_path / "bundle.enc", **commands)
    assert not (tmp_path / "bundle.enc").exists()


def test_recovery_rejects_ciphertext_tamper_and_missing_decrypt_command(tmp_path, commands):
    source = FakeDatabase(); restore = restored(source)
    artifact = export_recovery_bundle(source, tmp_path / "bundle.enc", **commands)
    receipt = artifact.with_suffix(".enc.receipt.json")
    original_artifact = artifact.read_bytes()
    original_receipt = receipt.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match="decrypt"):
        verify_recovery_bundle(artifact, restore, production_source=source)
    assert artifact.read_bytes() == original_artifact
    assert receipt.read_bytes() == original_receipt
    raw = bytearray(artifact.read_bytes()); raw[len(raw) // 2] ^= 1; artifact.write_bytes(raw)
    tampered_artifact = artifact.read_bytes()
    with pytest.raises(RuntimeError):
        verify_recovery_bundle(artifact, restore, production_source=source, decrypt_command=commands["decrypt_command"])
    assert artifact.read_bytes() == tampered_artifact
    assert receipt.read_bytes() == original_receipt


def test_recovery_rejects_sidecar_tamper_without_mutating_either_input(tmp_path, commands):
    source = FakeDatabase(); restore = restored(source)
    artifact = export_recovery_bundle(source, tmp_path / "bundle.enc", **commands)
    receipt = artifact.with_suffix(".enc.receipt.json")
    artifact_bytes = artifact.read_bytes()
    receipt.write_bytes(b'{"invalid":true}\n')
    receipt_bytes = receipt.read_bytes()
    with pytest.raises(RuntimeError):
        verify_recovery_bundle(
            artifact, restore, production_source=source,
            decrypt_command=commands["decrypt_command"],
        )
    assert artifact.read_bytes() == artifact_bytes
    assert receipt.read_bytes() == receipt_bytes


def test_database_source_rejects_fake_project_identity_before_connecting(monkeypatch):
    from scripts import protected_evidence as evidence
    calls = []
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **kw: calls.append(True))
    with pytest.raises((ValueError, RuntimeError), match="project|read-only"):
        with evidence.PostgresReadOnlySource("postgresql://postgres:password@db.pppppppppppppppppppp.supabase.co/postgres", "r" * 20, isolated_guard=True, production_project_ref="p" * 20):
            pass
    assert calls == []


def test_recovery_self_test_rejects_decryptor_ignoring_modified_ciphertext(tmp_path, commands):
    # Preserve a valid decryption as a local test fixture, then make the decryptor replay it.
    original = commands["decrypt_command"]
    replay = tmp_path / "replay.py"
    replay.write_text("""import pathlib,subprocess,sys
source,output=sys.argv[1:3]
cache=pathlib.Path(sys.argv[3])
if not cache.exists():
 subprocess.run(sys.argv[4:]+[source,str(cache)],check=True)
pathlib.Path(output).write_bytes(cache.read_bytes())
""")
    args = shlex.split(original.replace(" {input} {output}", ""))
    commands["decrypt_command"] = " ".join(map(shlex.quote, [sys.executable, str(replay)])) + " {input} {output} " + " ".join(map(shlex.quote, [str(tmp_path / "plain-fixture.tar"), *args]))
    with pytest.raises(RuntimeError, match="self-test"):
        export_recovery_bundle(FakeDatabase(), tmp_path / "bundle.enc", **commands)
    assert not (tmp_path / "bundle.enc").exists()


@pytest.mark.parametrize("member", ["../escape", "/tmp/escape", "payload/data/../../escape", "payload/data/reports.ndjson"])
def test_recovery_archive_never_extracts_unlisted_or_traversing_paths(tmp_path, member):
    from scripts.export_recovery_bundle import read_payload
    archive = tmp_path / "hostile.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(member); info.size = 4
        tar.addfile(info, io.BytesIO(b"data"))
    with pytest.raises(RuntimeError, match="payload"):
        read_payload(archive)
    assert not (tmp_path / "escape").exists()


def test_export_requires_exact_destination_without_traversal(tmp_path, commands):
    with pytest.raises(ValueError, match="paths"):
        export_recovery_bundle(FakeDatabase(), tmp_path / ".." / "bad.enc", **commands)
