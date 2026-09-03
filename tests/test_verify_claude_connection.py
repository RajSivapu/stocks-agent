from __future__ import annotations

from copy import deepcopy

from scripts.verify_claude_connection import evaluate_connection


def valid_record():
    return {
        "connection_status": "ready",
        "contract_version": 2,
        "last_handshake_at": "2026-09-03T12:03:00+00:00",
        "slot_status": "completed",
        "slot_updated_at": "2026-09-03T12:03:00+00:00",
        "run_status": "completed",
        "telegram_message_count": 0,
        "publication_count": 0,
        "submission_count": 0,
        "artifact_operation_count": 0,
        "operations": ["finish_run", "read_bounded_context", "start_run"],
        "receipt": {
            "contract_version": 2,
            "challenge": "a" * 64,
            "source_checks": [
                {"host": "query1.finance.yahoo.com", "status": "reachable", "content_hash": "b" * 64, "observed_at": "2026-09-03T12:02:00+00:00"},
                {"host": "www.sec.gov", "status": "reachable", "content_hash": "c" * 64, "observed_at": "2026-09-03T12:02:10+00:00"},
                {"host": "finnhub.io", "status": "reachable", "content_hash": "d" * 64, "observed_at": "2026-09-03T12:02:20+00:00"},
            ],
        },
    }


def test_verifier_accepts_only_application_completed_no_write_handshake():
    result = evaluate_connection(valid_record())
    assert result["ok"] is True
    assert all(result["checks"].values())
    assert "receipt" not in result


def test_verifier_rejects_provider_green_without_callback_completion():
    record = valid_record()
    record.update({
        "connection_status": "testing",
        "last_handshake_at": None,
        "slot_status": "triggered",
        "run_status": None,
        "operations": [],
        "receipt": None,
    })
    result = evaluate_connection(record)
    assert result["ok"] is False
    assert result["checks"]["application_callback_completed"] is False


def test_verifier_rejects_unreachable_or_write_capable_handshake_receipts():
    unreachable = deepcopy(valid_record())
    unreachable["receipt"]["source_checks"][0]["status"] = "unreachable"
    assert evaluate_connection(unreachable)["checks"]["source_network_verified"] is False

    wrote = valid_record()
    wrote["publication_count"] = 1
    wrote["artifact_operation_count"] = 1
    result = evaluate_connection(wrote)
    assert result["ok"] is False
    assert result["checks"]["zero_domain_writes"] is False
