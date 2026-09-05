import copy
import hashlib
import json
import shlex
import sys
import io
import tarfile

import pytest

from scripts.export_recovery_bundle import export_recovery_bundle
from scripts.verify_recovery_bundle import verify_recovery_bundle


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recovery_records():
    run = "11111111-1111-4111-8111-111111111111"
    packet_id = "22222222-2222-4222-8222-222222222222"
    report_id = "33333333-3333-4333-8333-333333333333"
    packet = {"candidates": [], "evidence": [], "coverage": {}, "limitations": [], "policy_version": 1}
    report = {"summary": "Suggestion only.", "packet_hash": digest(packet)}
    records = {
        "holdings": [{"ticker": "VTI", "shares": "2", "average_cost": "100"}],
        "transactions": [{"id": "1", "ticker": "VTI", "quantity": "2", "price": "100"}],
        "commands": [{"id": "44444444-4444-4444-8444-444444444444", "status": "applied"}],
        "runs": [{"id": run, "status": "completed", "phase": "post-market"}],
        "packets": [{"id": packet_id, "run_id": run, "packet_hash": digest(packet), "packet": packet}],
        "reports": [{"id": report_id, "run_id": run, "packet_id": packet_id, "report_hash": digest(report),
                     "report": report, "rendered_hash": hashlib.sha256(b"Suggestion only.").hexdigest(), "rendered_text": "Suggestion only."}],
        "publications": [{"report_id": report_id, "idempotency_key": "a" * 64, "status": "delivered", "telegram_message_ids": [7],
                          "telegram_accepted_at": "2026-09-05T20:00:00Z", "suppression_reason": None}],
        "roles": [{"role": "stock_agent_dashboard", "login": False, "superuser": False, "bypass_rls": False,
                   "memberships": [], "grants": ["SELECT:public.holdings"]}],
        "schema_version": [{"version": "20260926", "sha256": "d" * 64}],
    }
    records["holdings"][0].update(bucket="core", opened_at="2026-09-01", notes=None, stop=None, target=None, high_water_price=None,
                                  stop_alert_active=False, hold_override_until=None, stop_near_alert_active=False, target_near_alert_active=False, target_alert_active=False)
    records["transactions"][0].update(ts="2026-09-01T12:00:00Z", side="buy", source="owner", executed_on="2026-09-01")
    records["commands"][0].update(telegram_update_id="1", chat_id="2", user_id="3", operation="buy", ticker="VTI", qty="2", price="100",
                                  executed_on="2026-09-01", bucket="core", expected_shares="0", stop=None, preview={}, confirmation_message_id="4",
                                  expires_at="2026-09-01T12:15:00Z", applied_at="2026-09-01T12:00:00Z", realized_pnl=None, result={}, error=None,
                                  created_at="2026-09-01T11:59:00Z", updated_at="2026-09-01T12:00:00Z")
    records["runs"][0].update(started_at="2026-09-05T19:30:00Z", finished_at="2026-09-05T20:00:00Z", scheduled_phase="post-market",
                              scheduled_market_date="2026-09-05", gateway_request_id="55555555-5555-4555-8555-555555555555", telegram_message_ids=[7])
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


@pytest.mark.parametrize("mutation", [
    lambda db: db.records["roles"][0].update(password="secret"),
    lambda db: db.records.update(reports=[]),
    lambda db: db.records.update(publications=[]),
    lambda db: db.records["reports"][0].update(packet_id="55555555-5555-4555-8555-555555555555"),
    lambda db: db.records["packets"][0]["packet"].update(policy_version=99),
    lambda db: db.records["publications"][0].update(report_id="55555555-5555-4555-8555-555555555555"),
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


def test_export_rejects_report_without_its_outbox_receipt(tmp_path, commands):
    source = FakeDatabase()
    source.records["publications"] = []
    source.reported_counts["publications"] = 0
    with pytest.raises((ValueError, RuntimeError), match="publication"):
        export_recovery_bundle(source, tmp_path / "bundle.enc", **commands)


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
    with pytest.raises((ValueError, RuntimeError), match="decrypt"):
        verify_recovery_bundle(artifact, restore, production_source=source)
    raw = bytearray(artifact.read_bytes()); raw[len(raw) // 2] ^= 1; artifact.write_bytes(raw)
    with pytest.raises(RuntimeError):
        verify_recovery_bundle(artifact, restore, production_source=source, decrypt_command=commands["decrypt_command"])
    assert not artifact.exists()


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
