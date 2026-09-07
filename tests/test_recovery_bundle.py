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
    manifest_id = "10000000-0000-4000-8000-000000000001"
    security_revision_id = "10000000-0000-4000-8000-000000000002"
    signals_task_id = "10000000-0000-4000-8000-000000000003"
    theme_episode_id = "10000000-0000-4000-8000-000000000004"
    enrich_task_id = "10000000-0000-4000-8000-000000000005"
    exposure_fact_id = "10000000-0000-4000-8000-000000000006"
    screen_task_id = "10000000-0000-4000-8000-000000000007"
    nomination_id = "10000000-0000-4000-8000-000000000008"
    reference_chunk_hash = "9" * 64
    reference_root_hash = hashlib.sha256(reference_chunk_hash.encode()).hexdigest()
    transfer_request_id = "10000000-0000-4000-8000-000000000009"
    current_pin_payload = {
        "capability_id": "sec_company_tickers_universe",
        "binding_role": "current",
        "manifest_id": manifest_id,
        "reference_status": "healthy",
        "reference_as_of": "2026-09-05T19:32:00Z",
    }
    transfer_envelope = {
        "dry_run": False, "operation": "pin_discovery_reference",
        "payload": current_pin_payload, "request_id": transfer_request_id,
        "run_id": run, "schema_version": 1,
    }
    transfer_encoded = json.dumps(
        transfer_envelope, sort_keys=True, separators=(",", ":")
    ).encode()
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
        "reference_manifests": [{
            "id": manifest_id, "run_id": run, "reference_version": "us-listed:v1",
            "revision": 1, "capability_version": 1, "taxonomy_version": 1,
            "source_hash": "1" * 64, "valid_from": "2026-09-05T19:30:00Z", "valid_to": None,
            "manifest": {"universe": "eligible_us_listed"}, "content_hash": "2" * 64,
            "created_at": "2026-09-05T19:31:00Z",
        }],
        "security_reference_revisions": [{
            "id": security_revision_id, "manifest_id": manifest_id, "run_id": run, "revision": 1,
            "security_id": "NASDAQ:TEST", "entity_id": "CIK:0000000001", "ticker": "TEST",
            "exchange": "NASDAQ", "instrument_type": "COMMON_STOCK", "eligible": True,
            "exclusion_reasons": [], "aliases": ["Test Corp"], "source_ids": ["nasdaq-listed"],
            "valid_from": "2026-09-05T19:30:00Z", "valid_to": None, "content_hash": "3" * 64,
            "created_at": "2026-09-05T19:31:00Z",
        }],
        "reference_chunk_receipts": [{
            "manifest_id": manifest_id, "run_id": run,
            "capability_id": "sec_company_tickers_universe", "chunk_index": -1,
            "chunk_count": 1, "entry_count": 0, "chunk_hash": reference_root_hash,
            "predecessor_manifest_id": None, "payload": {"manifest_id": manifest_id},
            "created_at": "2026-09-05T19:30:30Z",
        }, {
            "manifest_id": manifest_id, "run_id": run,
            "capability_id": "sec_company_tickers_universe", "chunk_index": 0,
            "chunk_count": 1, "entry_count": 1, "chunk_hash": reference_chunk_hash,
            "predecessor_manifest_id": None,
            "payload": {"manifest_id": manifest_id, "entries": [{"security_id": "NASDAQ:TEST"}]},
            "created_at": "2026-09-05T19:31:00Z",
        }],
        "reference_finalization_seals": [{
            "manifest_id": manifest_id, "run_id": run,
            "capability_id": "sec_company_tickers_universe",
            "predecessor_manifest_id": None, "chunk_count": 1,
            "security_count": 1, "root_hash": reference_root_hash,
            "finalized_at": "2026-09-05T19:31:30Z",
        }],
        "reference_snapshot_memberships": [{
            "manifest_id": manifest_id, "security_revision_id": security_revision_id,
            "security_id": "NASDAQ:TEST", "ordinal": 0,
            "created_at": "2026-09-05T19:31:31Z",
        }],
        "reference_run_bindings": [{
            "run_id": run, "capability_id": "sec_company_tickers_universe",
            "manifest_id": manifest_id, "reference_status": "healthy",
            "reference_as_of": "2026-09-05T19:32:00Z",
            "source_retrieved_at": "2026-09-05T19:30:00Z",
            "request_payload": {
                "capability_id": "sec_company_tickers_universe",
                "binding_role": "current",
                "manifest_id": manifest_id,
                "reference_status": "healthy",
                "reference_as_of": "2026-09-05T19:32:00Z",
            },
            "reference_age_seconds": 120, "created_at": "2026-09-05T19:32:00Z",
        }],
        "reference_predecessor_pins": [{
            "run_id": run, "capability_id": "sec_company_tickers_universe",
            "manifest_id": None, "reference_status": "reference_unavailable",
            "reference_as_of": "2026-09-05T19:29:00Z",
            "source_retrieved_at": None, "reference_age_seconds": None,
            "request_payload": {
                "capability_id": "sec_company_tickers_universe",
                "binding_role": "predecessor", "manifest_id": None,
                "reference_status": "reference_stale",
                "reference_as_of": "2026-09-05T19:29:00Z",
            },
            "created_at": "2026-09-05T19:29:00Z",
        }],
        "reference_transfer_requests": [{
            "request_id": transfer_request_id, "run_id": run,
            "operation": "pin_discovery_reference",
            "encoded_bytes": len(transfer_encoded),
            "request_hash": hashlib.sha256(transfer_encoded).hexdigest(),
            "request_payload": current_pin_payload,
            "created_at": "2026-09-05T19:32:00Z",
        }],
        "discovery_stage_tasks": [{
            "id": signals_task_id, "run_id": run, "stage": "signals", "capability_id": "gdelt_theme_search",
            "provider": "gdelt", "query_kind": "theme_search", "query_hash": "4" * 64,
            "dependency_ids": [], "requested_window": {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"},
            "state": "succeeded", "attempt_count": 1, "request_budget": 1,
            "result": {
                "cursor_key": "gdelt_theme_search:grid_modernization",
                "theme_id": "grid_modernization",
                "request_cursor": {
                    "provider": "gdelt", "capability_id": "gdelt_theme_search",
                    "completed_through": None, "active_window_start": None,
                    "active_window_end": None, "backlog_token": None, "page": 1,
                    "accepted_item_ids": [], "next_retry_phase": None,
                },
                "source_cursor": {
                    "provider": "gdelt", "capability_id": "gdelt_theme_search",
                    "completed_through": "2026-09-05T19:33:00Z",
                    "active_window_start": None, "active_window_end": None,
                    "backlog_token": None, "page": 1,
                    "accepted_item_ids": [], "next_retry_phase": None,
                },
                "checkpoint": {"cache_key": "4" * 64, "receipt": {"metadata": {}}},
            },
            "created_at": "2026-09-05T19:32:00Z", "updated_at": "2026-09-05T19:33:00Z",
        }, {
            "id": enrich_task_id, "run_id": run, "stage": "enrich", "capability_id": "sec_issuer_submissions",
            "provider": "sec_edgar", "query_kind": "issuer_submissions", "query_hash": "5" * 64,
            "dependency_ids": [signals_task_id], "requested_window": {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"},
            "state": "succeeded", "attempt_count": 1, "request_budget": 1,
            "result": {"exposure_fact_ids": [exposure_fact_id]},
            "created_at": "2026-09-05T19:34:00Z", "updated_at": "2026-09-05T19:35:00Z",
        }, {
            "id": screen_task_id, "run_id": run, "stage": "screen", "capability_id": "finnhub_basic_financials",
            "provider": "finnhub", "query_kind": "screener", "query_hash": "6" * 64,
            "dependency_ids": [enrich_task_id], "requested_window": {"start": "2026-09-05T12:00:00Z", "end": "2026-09-05T20:00:00Z"},
            "state": "succeeded", "attempt_count": 1, "request_budget": 1,
            "result": {"research_nomination_ids": [nomination_id]},
            "created_at": "2026-09-05T19:36:00Z", "updated_at": "2026-09-05T19:37:00Z",
        }],
        "theme_episode_revisions": [{
            "id": theme_episode_id, "run_id": run, "task_id": signals_task_id,
            "theme_id": "grid_modernization", "revision": 1,
            "episode": {"summary": "Grid investment signals"}, "source_ids": ["gdelt:1"],
            "valid_from": "2026-09-05T19:32:00Z", "valid_to": None, "content_hash": "7" * 64,
            "created_at": "2026-09-05T19:33:00Z",
        }],
        "exposure_facts": [{
            "id": exposure_fact_id, "run_id": run, "task_id": enrich_task_id,
            "security_revision_id": security_revision_id, "theme_episode_revision_id": theme_episode_id,
            "exposure_kind": "filing", "fact": {"summary": "Grid segment disclosure"},
            "source_ids": ["sec:1"], "valid_from": "2026-09-05T19:34:00Z", "valid_to": None,
            "content_hash": "8" * 64, "created_at": "2026-09-05T19:35:00Z",
        }],
        "research_nominations": [{
            "id": nomination_id, "run_id": run, "task_id": screen_task_id,
            "security_revision_id": security_revision_id, "theme_episode_revision_id": theme_episode_id,
            "exposure_fact_ids": [exposure_fact_id], "state": "nominated",
            "rationale": {"summary": "Research candidate only"},
            "created_at": "2026-09-05T19:37:00Z", "updated_at": "2026-09-05T19:37:00Z",
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
    assert sidecar["format"] == "stocks-agent-recovery-v5"
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
    cursor_result = next(
        row["result"] for row in records["discovery_stage_tasks"]
        if row["capability_id"] == "gdelt_theme_search"
    )
    assert cursor_result["cursor_key"] == "gdelt_theme_search:grid_modernization"
    assert cursor_result["source_cursor"]["completed_through"] == "2026-09-05T19:33:00Z"
    assert set(records) >= {
        "intelligence_run_events", "source_quota_reservations", "collection_checkpoints",
        "collection_checkpoint_history", "collection_completions", "report_origins",
        "cash_ledger_state", "cash_snapshots", "run_terminal_outcomes",
    }


def test_recovery_rejects_malformed_terminal_cursor_metadata():
    records = recovery_records()
    cursor = records["discovery_stage_tasks"][0]["result"]["source_cursor"]
    cursor["accepted_item_ids"] = ["duplicate", "duplicate"]

    with pytest.raises(ValueError, match="discovery task.*invalid content"):
        _validated_records(records)


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


@pytest.fixture
def reconciled_records(tmp_path, monkeypatch):
    import scripts.deploy_owner_dashboard_api as deploy

    path = "sql/reconciliation/20261004_production_schema_reconciliation.sql"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text("-- exact normalized baseline\n SELECT 1;\n")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    records = recovery_records()
    records["schema_version"][0]["version"] = "20261004"
    records["release_migration_ledger"] = [{
        "path": path, "version": "20261004", "sha256": hashlib.sha256(b'["SELECT 1"]').hexdigest(),
        "applied_at": "2026-09-06T20:00:00Z",
    }]
    return records


def test_recovery_accepts_the_exact_truthful_reconciliation_pair(reconciled_records):
    assert _validated_records(reconciled_records)["release_migration_ledger"] == reconciled_records["release_migration_ledger"]


INTELLIGENCE_DEPENDENT_DATASETS = (
    "intelligence_run_events",
    "source_quota_reservations",
    "collection_checkpoints",
    "collection_checkpoint_history",
    "collection_completions",
)

EMPTY_INTELLIGENCE_PACKET_REPORT_HISTORY = (
    "intelligence_runs",
    *INTELLIGENCE_DEPENDENT_DATASETS,
    "reference_manifests",
    "security_reference_revisions",
    "reference_chunk_receipts",
    "reference_finalization_seals",
    "reference_snapshot_memberships",
    "reference_run_bindings",
    "reference_predecessor_pins",
    "reference_transfer_requests",
    "discovery_stage_tasks",
    "theme_episode_revisions",
    "exposure_facts",
    "research_nominations",
    "packets",
    "reports",
    "report_origins",
    "publications",
    "policy_comparisons",
)


DISCOVERY_DATASETS = (
    "reference_manifests",
    "security_reference_revisions",
    "reference_chunk_receipts",
    "reference_finalization_seals",
    "reference_snapshot_memberships",
    "reference_run_bindings",
    "reference_predecessor_pins",
    "reference_transfer_requests",
    "discovery_stage_tasks",
    "theme_episode_revisions",
    "exposure_facts",
    "research_nominations",
)


def test_recovery_validates_complete_discovery_lineage_and_exact_fields():
    records = recovery_records()

    validated = _validated_records(records)

    assert {name: len(validated[name]) for name in DISCOVERY_DATASETS} == {
        "reference_manifests": 1,
        "security_reference_revisions": 1,
        "reference_chunk_receipts": 2,
        "reference_finalization_seals": 1,
        "reference_snapshot_memberships": 1,
        "reference_run_bindings": 1,
        "reference_predecessor_pins": 1,
        "reference_transfer_requests": 1,
        "discovery_stage_tasks": 3,
        "theme_episode_revisions": 1,
        "exposure_facts": 1,
        "research_nominations": 1,
    }


def _make_stale_current_inconsistent_with_predecessor(records):
    manifest_id = records["reference_manifests"][0]["id"]
    current = records["reference_run_bindings"][0]
    current.update(
        manifest_id=manifest_id,
        reference_status="reference_stale",
        request_payload={
            "capability_id": current["capability_id"],
            "binding_role": "current",
            "manifest_id": manifest_id,
            "reference_status": "reference_stale",
            "reference_as_of": current["reference_as_of"],
        },
    )
    assert records["reference_predecessor_pins"][0]["manifest_id"] is None


def test_recovery_rejects_stale_current_that_differs_from_predecessor_pin():
    records = recovery_records()
    _make_stale_current_inconsistent_with_predecessor(records)

    with pytest.raises(ValueError, match="binding dependency mismatch"):
        _validated_records(records)


def test_restore_rejects_stale_current_that_differs_from_predecessor_pin():
    records = recovery_records()
    _make_stale_current_inconsistent_with_predecessor(records)

    class UnusedConnection:
        def transaction(self):
            raise AssertionError("invalid recovery data reached restore mutation")

    with pytest.raises(ValueError, match="binding dependency mismatch"):
        restore_recovery_records(UnusedConnection(), records, isolated_guard=True)


def test_exact_recovery_verifier_rejects_inconsistent_restored_stale_lineage(
        tmp_path, commands):
    production = FakeDatabase()
    artifact = export_recovery_bundle(
        production, tmp_path / "stale-lineage.enc", **commands,
    )
    restore = restored(production)
    _make_stale_current_inconsistent_with_predecessor(restore.records)

    with pytest.raises(ValueError, match="binding dependency mismatch"):
        verify_recovery_bundle(
            artifact, restore, production_source=production,
            decrypt_command=commands["decrypt_command"],
        )


def _make_own_finalization_a_stale_predecessor(records):
    _make_stale_current_inconsistent_with_predecessor(records)
    manifest = records["reference_manifests"][0]
    manifest_id = manifest["id"]
    run_id = manifest["run_id"]
    capability = records["reference_finalization_seals"][0]["capability_id"]
    current = records["reference_run_bindings"][0]
    predecessor = records["reference_predecessor_pins"][0]
    assert current["run_id"] == predecessor["run_id"] == run_id
    predecessor.update(
        manifest_id=manifest_id,
        reference_status="reference_stale",
        reference_as_of=current["reference_as_of"],
        source_retrieved_at=current["source_retrieved_at"],
        reference_age_seconds=current["reference_age_seconds"],
        request_payload={
            "capability_id": capability,
            "binding_role": "predecessor",
            "manifest_id": None,
            "reference_status": "reference_stale",
            "reference_as_of": current["reference_as_of"],
        },
    )


def test_recovery_rejects_own_finalization_as_stale_predecessor():
    records = recovery_records()
    _make_own_finalization_a_stale_predecessor(records)

    with pytest.raises(ValueError, match="predecessor dependency mismatch"):
        _validated_records(records)


def test_ordered_restore_rejects_own_finalization_as_stale_predecessor():
    records = recovery_records()
    _make_own_finalization_a_stale_predecessor(records)

    class UnusedConnection:
        def transaction(self):
            raise AssertionError("invalid recovery data reached ordered restore")

    with pytest.raises(ValueError, match="predecessor dependency mismatch"):
        restore_recovery_records(UnusedConnection(), records, isolated_guard=True)


def test_exact_recovery_verifier_rejects_own_finalization_as_stale_predecessor(
        tmp_path, commands):
    production = FakeDatabase()
    artifact = export_recovery_bundle(
        production, tmp_path / "self-predecessor.enc", **commands,
    )
    restore = restored(production)
    _make_own_finalization_a_stale_predecessor(restore.records)

    with pytest.raises(ValueError, match="predecessor dependency mismatch"):
        verify_recovery_bundle(
            artifact, restore, production_source=production,
            decrypt_command=commands["decrypt_command"],
        )


@pytest.mark.parametrize("dataset", DISCOVERY_DATASETS)
def test_recovery_rejects_missing_discovery_dataset(dataset):
    records = recovery_records()
    records.pop(dataset)

    with pytest.raises(ValueError, match="exact allowlisted datasets"):
        _validated_records(records)


@pytest.mark.parametrize(("dataset", "field", "replacement"), [
    ("reference_manifests", "run_id", "20000000-0000-4000-8000-000000000001"),
    ("security_reference_revisions", "manifest_id", "20000000-0000-4000-8000-000000000002"),
    ("discovery_stage_tasks", "dependency_ids", ["20000000-0000-4000-8000-000000000003"]),
    ("theme_episode_revisions", "task_id", "10000000-0000-4000-8000-000000000005"),
    ("exposure_facts", "security_revision_id", "20000000-0000-4000-8000-000000000004"),
    ("research_nominations", "exposure_fact_ids", ["20000000-0000-4000-8000-000000000005"]),
])
def test_recovery_rejects_orphaned_discovery_records(dataset, field, replacement):
    records = recovery_records()
    records[dataset][-1][field] = replacement

    with pytest.raises(ValueError, match="discovery.*dependency mismatch"):
        _validated_records(records)


@pytest.mark.parametrize(("dataset", "field", "replacement"), [
    ("reference_manifests", "content_hash", "altered"),
    ("discovery_stage_tasks", "query_hash", "altered"),
    ("discovery_stage_tasks", "provider", "paid_provider"),
    ("reference_manifests", "manifest", {"nested": {"executionAllowed": False}}),
    ("reference_run_bindings", "request_payload", {"reference_status": "healthy"}),
    ("discovery_stage_tasks", "result", {"portfolioOverlap": {"ticker": "TEST"}}),
    ("exposure_facts", "fact", {"nested": {"order": {"side": "buy"}}}),
    ("research_nominations", "rationale", {"action": "buy"}),
    ("research_nominations", "rationale", {"nested": {"order_details": {"side": "buy"}}}),
])
def test_recovery_rejects_altered_or_authoritative_discovery_records(dataset, field, replacement):
    records = recovery_records()
    records[dataset][0][field] = replacement

    with pytest.raises(ValueError, match="discovery"):
        _validated_records(records)


def test_recovery_accepts_empty_intelligence_packet_report_history_when_all_dependents_are_empty():
    records = recovery_records()
    for dataset in EMPTY_INTELLIGENCE_PACKET_REPORT_HISTORY:
        records[dataset] = []

    validated = _validated_records(records)

    assert all(validated[dataset] == [] for dataset in EMPTY_INTELLIGENCE_PACKET_REPORT_HISTORY)


@pytest.mark.parametrize("orphan_dataset", INTELLIGENCE_DEPENDENT_DATASETS)
def test_recovery_rejects_each_intelligence_dependent_without_its_run(orphan_dataset):
    records = recovery_records()
    orphan_rows = copy.deepcopy(records[orphan_dataset])
    if not orphan_rows:
        checkpoint = records["collection_checkpoints"][0]
        orphan_rows = [{
            "run_id": checkpoint["run_id"], "cache_key": checkpoint["cache_key"],
            "source_receipt_id": checkpoint["source_receipt_id"], "payload": checkpoint["payload"],
            "replaced_at": checkpoint["created_at"],
        }]
    records["intelligence_runs"] = []
    for dataset in INTELLIGENCE_DEPENDENT_DATASETS:
        records[dataset] = []
    records[orphan_dataset] = orphan_rows

    with pytest.raises(ValueError, match="dependency mismatch"):
        _validated_records(records)


def test_recovery_rejects_report_without_its_packet():
    records = recovery_records()
    records["packets"] = []
    records["policy_comparisons"] = []
    records["report_origins"] = []
    records["publications"] = []

    with pytest.raises(ValueError, match="report content or packet/run relationship mismatch"):
        _validated_records(records)


def test_recovery_rejects_publication_without_its_report():
    records = recovery_records()
    records["reports"] = []
    records["report_origins"] = []

    with pytest.raises(ValueError, match="publication report relationship mismatch"):
        _validated_records(records)


@pytest.mark.parametrize("missing_dependency", ("packet", "evaluation"))
def test_recovery_rejects_policy_comparison_without_its_dependencies(missing_dependency):
    records = recovery_records()
    if missing_dependency == "packet":
        records["packets"] = []
        records["reports"] = []
        records["report_origins"] = []
        records["publications"] = []
    else:
        records["decision_evaluations"] = []

    with pytest.raises(ValueError, match="policy comparison recovery dependency mismatch"):
        _validated_records(records)


@pytest.mark.parametrize("corruption", (
    "native_missing", "private_missing", "lookalike", "private_hash", "native_hash", "duplicate_version",
    "normal_path_lookalike", "native_extra", "older_private", "baseline_file_drift",
))
def test_recovery_rejects_unpaired_or_drifted_reconciliation(reconciled_records, corruption):
    import scripts.deploy_owner_dashboard_api as deploy

    records = reconciled_records
    private = records["release_migration_ledger"][0]
    if corruption == "native_missing": records["schema_version"] = []
    elif corruption == "private_missing": records["release_migration_ledger"] = []
    elif corruption == "lookalike": private["path"] = private["path"].replace("production_schema", "other_schema")
    elif corruption == "normal_path_lookalike": private["path"] = "sql/migrations/20261004_production_schema_reconciliation.sql"
    elif corruption == "private_hash": private["sha256"] = "a" * 64
    elif corruption == "native_hash":
        records["schema_version"][0].update(statements=["SELECT 2"], sha256=hashlib.sha256(b"SELECT 2").hexdigest())
    elif corruption == "duplicate_version":
        records["release_migration_ledger"].append({**private, "path": "sql/migrations/20261004_duplicate.sql"})
    elif corruption == "native_extra": records["schema_version"].append({**records["schema_version"][0], "version": "20261003"})
    elif corruption == "older_private":
        records["release_migration_ledger"].append({**private, "path": "sql/migrations/20261003_old.sql", "version": "20261003"})
    elif corruption == "baseline_file_drift": (deploy.ROOT / private["path"]).write_text("SELECT 2;")
    with pytest.raises(ValueError, match="migration|reconciliation|schema_version"):
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


def test_actual_recovery_crypt_fernet_command_exports_and_decrypt_verifies(tmp_path, monkeypatch):
    script = Path(__file__).parents[1] / "scripts" / "recovery_crypt.py"
    prefix = " ".join(map(shlex.quote, [sys.executable, str(script)]))
    commands = {
        "encrypt_command": prefix + " encrypt {input} {output}",
        "decrypt_command": prefix + " decrypt {input} {output}",
    }
    monkeypatch.setenv("RELEASE_RECOVERY_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    source = FakeDatabase()

    artifact = export_recovery_bundle(source, tmp_path / "fernet.enc", **commands)

    assert verify_recovery_bundle(
        artifact, restored(source), production_source=source,
        decrypt_command=commands["decrypt_command"],
    )["status"] == "verified"


@pytest.mark.parametrize("wrapper", ("hex", "urlsafe_base64"))
def test_non_fernet_textual_wrappers_are_not_accepted_as_ciphertext(tmp_path, commands, wrapper):
    script = tmp_path / f"{wrapper}.py"
    transform = "raw.hex().encode()" if wrapper == "hex" else "base64.urlsafe_b64encode(raw)"
    script.write_text(
        "import base64, pathlib, sys\n"
        "raw = pathlib.Path(sys.argv[1]).read_bytes()\n"
        f"pathlib.Path(sys.argv[2]).write_bytes({transform})\n"
    )
    encrypt_command = " ".join(map(shlex.quote, [sys.executable, str(script)])) + " {input} {output}"

    with pytest.raises(RuntimeError, match="plaintext"):
        export_recovery_bundle(
            FakeDatabase(), tmp_path / f"{wrapper}.enc",
            encrypt_command=encrypt_command, decrypt_command=commands["decrypt_command"],
        )

    assert not (tmp_path / f"{wrapper}.enc").exists()
    assert not (tmp_path / f"{wrapper}.enc.receipt.json").exists()


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
