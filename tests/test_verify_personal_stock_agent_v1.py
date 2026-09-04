import pytest

from scripts.verify_personal_stock_agent_v1 import verify_release


def complete_release_receipt():
    return {
        "candidate_sha": "a" * 40,
        "exact_head_ci": {"status": "passed", "candidate_sha": "a" * 40},
        "independent_review": {"status": "passed", "candidate_sha": "a" * 40},
        "quota_receipts": {"status": "verified"},
        "dry_run_zero_writes": True,
        "dry_run_zero_sends": True,
        "migration_version": "20260907,20260908",
        "gateway_version": 18,
        "dashboard_api_version": 1,
        "site_version": "immutable-version-1",
        "owner_canary": {"status": "verified"},
        "anonymous_denial": {"status": "verified"},
        "non_owner_denial": {"status": "verified"},
        "source_parity": {"status": "verified", "candidate_sha": "a" * 40},
        "scheduled_receipt": {"status": "verified"},
        "rollback_check": {"status": "verified"},
    }


def test_release_receipt_requires_every_gate():
    receipt = complete_release_receipt()
    assert verify_release(receipt) == {"status": "verified", "candidate_sha": "a" * 40}


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
