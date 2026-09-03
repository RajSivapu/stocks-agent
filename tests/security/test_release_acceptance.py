from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import release_acceptance as acceptance


NOW = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40


def evidence() -> dict:
    return {
        "version": 1,
        "commit": COMMIT,
        "evidence": [
            {
                "criterion": criterion,
                "status": "passed",
                "checked_at": (NOW - timedelta(hours=1)).isoformat(),
                "evidence_hash": f"{index:064x}",
                "method": "signed_manual" if criterion in acceptance.MANUAL_CRITERIA else "automated",
            }
            for index, criterion in enumerate(acceptance.REQUIRED_CRITERIA, start=1)
        ],
    }


def test_complete_gate_a_through_g_evidence_produces_hash_only_report():
    report = acceptance.build_acceptance_report(evidence(), now=NOW)

    assert report["status"] == "passed"
    assert report["private_data"] is False
    assert set(report["gates"]) == set("ABCDEFG")
    assert all(value == "passed" for value in report["gates"].values())
    assert len(report["criteria"]) == len(acceptance.REQUIRED_CRITERIA)
    assert set(report) == {
        "version", "status", "commit", "generated_at", "private_data",
        "gates", "criteria", "evidence_digest",
    }
    assert all(set(item) == {"criterion", "gate", "evidence_hash"} for item in report["criteria"])
    acceptance.validate_acceptance_report(report, expected_commit=COMMIT, now=NOW)


@pytest.mark.parametrize("status", ["skipped", "unknown", "failed", "pending"])
def test_non_pass_or_missing_release_evidence_fails_closed(status):
    value = evidence()
    value["evidence"][0]["status"] = status
    with pytest.raises(acceptance.AcceptanceRejected):
        acceptance.build_acceptance_report(value, now=NOW)

    missing = evidence()
    missing["evidence"].pop()
    with pytest.raises(acceptance.AcceptanceRejected, match="incomplete"):
        acceptance.build_acceptance_report(missing, now=NOW)


def test_duplicate_stale_future_and_private_evidence_are_rejected():
    duplicate = evidence()
    duplicate["evidence"].append(deepcopy(duplicate["evidence"][0]))
    with pytest.raises(acceptance.AcceptanceRejected):
        acceptance.build_acceptance_report(duplicate, now=NOW)

    stale = evidence()
    stale["evidence"][0]["checked_at"] = (NOW - timedelta(hours=25)).isoformat()
    with pytest.raises(acceptance.AcceptanceRejected, match="stale"):
        acceptance.build_acceptance_report(stale, now=NOW)

    future = evidence()
    future["evidence"][0]["checked_at"] = (NOW + timedelta(hours=2)).isoformat()
    with pytest.raises(acceptance.AcceptanceRejected, match="future"):
        acceptance.build_acceptance_report(future, now=NOW)

    private = evidence()
    private["evidence"][0]["owner_id"] = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(acceptance.AcceptanceRejected, match="fields"):
        acceptance.build_acceptance_report(private, now=NOW)


def test_recovery_and_friend_manual_evidence_have_bounded_longer_windows():
    value = evidence()
    for item in value["evidence"]:
        if item["criterion"] == "encrypted_recovery":
            item["checked_at"] = (NOW - timedelta(days=29)).isoformat()
        if item["criterion"] == "friend_onboarding_cycle":
            item["checked_at"] = (NOW - timedelta(days=6)).isoformat()
    acceptance.build_acceptance_report(value, now=NOW)

    for item in value["evidence"]:
        if item["criterion"] == "encrypted_recovery":
            item["checked_at"] = (NOW - timedelta(days=31)).isoformat()
    with pytest.raises(acceptance.AcceptanceRejected, match="stale"):
        acceptance.build_acceptance_report(value, now=NOW)


def test_operator_runbook_maps_every_gate_and_forbids_mock_or_private_evidence():
    runbook = (
        Path(__file__).resolve().parents[2] / "docs/runbooks/release-acceptance.md"
    ).read_text(encoding="utf-8")
    for criterion in acceptance.REQUIRED_CRITERIA:
        assert f"`{criterion}`" in runbook
    normalized = " ".join(runbook.lower().split())
    for required in (
        "mock browser results never count as live staging evidence",
        "no tokens, email addresses, owner ids, holdings, tickers, or model content",
        "skipped`, `unknown`, `pending`, and `failed",
        "release_acceptance.py",
    ):
        assert required in normalized
