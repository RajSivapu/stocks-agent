import pytest

from scripts.verify_personal_stock_agent_v1 import verify_release


def complete_release_receipt():
    sha = "a" * 40
    uid = "11111111-1111-4111-8111-111111111111"
    return {
        "candidate_sha": sha,
        "exact_head_ci": {"status": "passed", "candidate_sha": sha, "workflow_sha": sha, "conclusion": "success", "workflow_run_id": 42},
        "independent_review": {"status": "passed", "candidate_sha": sha, "reviewed_sha": sha, "verdict": "approved"},
        "quota_receipts": {"status": "verified", "provider_reservations": {"finnhub": 2}, "total_requests": 2},
        "dry_run_zero_writes": {"status": "verified", "table_deltas": {"analysis_runs": 0}},
        "dry_run_zero_sends": {"status": "verified", "telegram_message_ids": [], "message_id_delta": 0},
        "migration_version": {"status": "verified", "versions": [{"version": "20260907", "sha256": "b" * 64}, {"version": "20260908", "sha256": "c" * 64}]},
        "gateway_version": {"status": "deployed", "candidate_sha": sha, "deployed_sha": sha, "version": 18, "source_sha256": "d" * 64},
        "dashboard_api_version": {"status": "deployed", "candidate_sha": sha, "deployed_sha": sha, "version": 1, "source_sha256": "e" * 64},
        "site_version": {"status": "deployed", "candidate_sha": sha, "deployed_sha": sha, "version": "immutable-1", "url": "https://stocks.example.com", "asset_hashes": ["f" * 64]},
        "owner_canary": {"status": "verified", "http_status": 200, "url": "https://stocks.example.com"},
        "anonymous_denial": {"status": "verified", "http_status": 401, "url": "https://stocks.example.com"},
        "non_owner_denial": {"status": "verified", "http_status": 403, "url": "https://stocks.example.com"},
        "source_parity": {"status": "verified", "candidate_sha": sha, "relationships_verified": True, "hashes_verified": True, "counts": {key: 1 for key in ("runs", "events", "rankings", "packets", "reports", "report_publications")}},
        "scheduled_receipt": {"status": "completed", "run_id": uid, "intelligence_run_id": uid, "packet_id": "22222222-2222-4222-8222-222222222222", "report_id": "33333333-3333-4333-8333-333333333333", "packet_hash": "1" * 64, "report_hash": "2" * 64, "publication_receipt": {"status": "accepted_by_telegram", "telegram_message_ids": [7]}},
        "rollback_check": {"status": "rolled_back", "function": "owner-dashboard-api", "dashboard_secrets_unset": ["DASHBOARD_ALLOWED_ORIGINS", "DASHBOARD_DATABASE_URL", "DASHBOARD_OWNER_USER_ID"], "runtime_login": {"status": "disabled", "login": False, "memberships": 0}, "gateway": {"status": "restored", "source_sha256": "3" * 64, "git_sha": "9" * 40, "function_version": 19}},
    }


def test_release_receipt_requires_every_gate():
    receipt = complete_release_receipt()
    assert verify_release(receipt) == {"status": "verified", "candidate_sha": "a" * 40, "gate_count": 15}


@pytest.mark.parametrize("missing", [
    "exact_head_ci", "independent_review", "quota_receipts", "dry_run_zero_writes",
    "dry_run_zero_sends", "migration_version", "gateway_version", "dashboard_api_version",
    "site_version", "owner_canary", "anonymous_denial", "non_owner_denial",
    "source_parity", "scheduled_receipt", "rollback_check",
])
def test_release_receipt_fails_when_gate_is_missing(missing):
    receipt = complete_release_receipt()
    receipt[missing] = None
    with pytest.raises(RuntimeError, match=missing):
        verify_release(receipt)
