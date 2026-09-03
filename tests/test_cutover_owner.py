from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from scripts import cutover_owner as cutover


NOW = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
CANDIDATE = "a" * 40
ROLLBACK = "b" * 40
DIGEST = "c" * 64


def manifest() -> dict:
    return {
        "version": 1,
        "candidate_commit": CANDIDATE,
        "rollback_commit": ROLLBACK,
        "checked_at": (NOW - timedelta(minutes=5)).isoformat(),
        "gates": {gate: "passed" for gate in ("0", "A", "B", "C", "D", "E")},
        "gate_evidence_hashes": {gate: DIGEST for gate in ("0", "A", "B", "C", "D", "E")},
        "backup": {
            "status": "passed",
            "checked_at": (NOW - timedelta(hours=2)).isoformat(),
            "evidence_hash": "d" * 64,
        },
        "restore": {
            "status": "passed",
            "checked_at": (NOW - timedelta(days=2)).isoformat(),
            "evidence_hash": "e" * 64,
        },
        "legacy_routines_paused": True,
        "legacy_webhook_mutations_paused": True,
        "owner_identity": {"verified": True, "evidence_hash": "f" * 64},
        "parity": {
            "before_count": 487,
            "after_count": 487,
            "before_digest": "1" * 64,
            "after_digest": "1" * 64,
        },
    }


def passed_step(step: str, *, checked_at: datetime = NOW) -> dict:
    return {
        "step": step,
        "status": "passed",
        "checked_at": checked_at.isoformat(),
        "evidence_hash": "2" * 64,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["backup"].update(checked_at=(NOW - timedelta(hours=37)).isoformat()), "backup"),
        (lambda value: value["restore"].update(checked_at=(NOW - timedelta(days=31)).isoformat()), "restore"),
        (lambda value: value["gates"].update(B="failed"), "gate"),
        (lambda value: value.update(legacy_routines_paused=False), "routine"),
        (lambda value: value.update(legacy_webhook_mutations_paused=False), "webhook"),
        (lambda value: value["owner_identity"].update(verified=False), "identity"),
        (lambda value: value["parity"].update(after_count=486), "parity"),
        (lambda value: value["parity"].update(after_digest="3" * 64), "parity"),
        (lambda value: value.update(rollback_commit=CANDIDATE), "rollback"),
    ],
)
def test_cutover_preconditions_fail_closed(mutation, message):
    source = manifest()
    mutation(source)

    with pytest.raises(cutover.CutoverRejected, match=message):
        cutover.prepare_cutover(source, now=NOW, cutover_id="11111111-1111-4111-8111-111111111111")


def test_prepared_state_is_hash_only_paused_and_ordered():
    state = cutover.prepare_cutover(
        manifest(), now=NOW, cutover_id="11111111-1111-4111-8111-111111111111"
    )

    assert state["status"] == "prepared"
    assert state["next_step"] == cutover.CUTOVER_STEPS[0]
    assert state["completed_steps"] == []
    assert all(state["paused_paths"].values())
    assert state["additive_schema_retained"] is True
    assert state["rollback_required"] is False
    assert set(state) == cutover.STATE_FIELDS
    assert "owner" not in str(state).lower()
    cutover.validate_state(state)


def test_steps_cannot_be_skipped_and_stale_or_private_evidence_is_rejected():
    state = cutover.prepare_cutover(manifest(), now=NOW)

    with pytest.raises(cutover.CutoverRejected, match="order"):
        cutover.advance_cutover(state, passed_step(cutover.CUTOVER_STEPS[1]), now=NOW)
    with pytest.raises(cutover.CutoverRejected, match="stale"):
        cutover.advance_cutover(
            state,
            passed_step(cutover.CUTOVER_STEPS[0], checked_at=NOW - timedelta(hours=25)),
            now=NOW,
        )
    private = passed_step(cutover.CUTOVER_STEPS[0])
    private["owner_email"] = "private@example.test"
    with pytest.raises(cutover.CutoverRejected, match="fields"):
        cutover.advance_cutover(state, private, now=NOW)


def test_failed_step_forces_immediate_rollback_and_all_paths_paused():
    state = cutover.prepare_cutover(manifest(), now=NOW)
    failed = {**passed_step(cutover.CUTOVER_STEPS[0]), "status": "failed"}

    stopped = cutover.advance_cutover(state, failed, now=NOW)

    assert stopped["status"] == "rollback_required"
    assert stopped["rollback_required"] is True
    assert stopped["next_step"] is None
    assert all(stopped["paused_paths"].values())
    assert stopped["additive_schema_retained"] is True
    with pytest.raises(cutover.CutoverRejected, match="rollback"):
        cutover.advance_cutover(stopped, passed_step(cutover.CUTOVER_STEPS[0]), now=NOW)

    rolled_back = cutover.record_rollback(
        stopped,
        {
            "status": "passed",
            "checked_at": NOW.isoformat(),
            "evidence_hash": "4" * 64,
        },
        now=NOW,
    )
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["previous_bundle_restored"] is True
    assert all(rolled_back["paused_paths"].values())
    assert rolled_back["additive_schema_retained"] is True


def test_resume_steps_open_only_the_named_path_and_invitations_stay_disabled():
    state = cutover.prepare_cutover(manifest(), now=NOW)
    for step in cutover.CUTOVER_STEPS:
        before = deepcopy(state["paused_paths"])
        state = cutover.advance_cutover(state, passed_step(step), now=NOW)
        if step in cutover.RESUME_STEP_PATH:
            resumed = cutover.RESUME_STEP_PATH[step]
            assert before[resumed] is True
            assert state["paused_paths"][resumed] is False
        assert state["paused_paths"]["friend_invitations"] is True

    assert state["status"] == "owner_soak_complete"
    assert state["next_step"] is None
    assert state["rollback_required"] is False


def test_final_receipt_requires_every_step_and_contains_no_private_evidence():
    state = cutover.prepare_cutover(
        manifest(), now=NOW, cutover_id="11111111-1111-4111-8111-111111111111"
    )
    with pytest.raises(cutover.CutoverRejected, match="complete"):
        cutover.finalize_receipt(state, now=NOW)

    for step in cutover.CUTOVER_STEPS:
        state = cutover.advance_cutover(state, passed_step(step), now=NOW)
    receipt = cutover.finalize_receipt(state, now=NOW)

    assert receipt["status"] == "owner_soak_complete"
    assert receipt["private_data"] is False
    assert receipt["invitations_enabled"] is False
    assert set(receipt) == cutover.RECEIPT_FIELDS
    assert "owner" not in str(receipt).lower().replace("owner_soak_complete", "")
    assert "487" not in str(receipt)


def test_state_digest_detects_tampering_and_writer_never_overwrites(tmp_path: Path):
    state = cutover.prepare_cutover(manifest(), now=NOW)
    tampered = deepcopy(state)
    tampered["paused_paths"]["telegram_recordkeeping"] = False
    with pytest.raises(cutover.CutoverRejected, match="digest"):
        cutover.validate_state(tampered)

    forged = deepcopy(state)
    forged["paused_paths"]["telegram_recordkeeping"] = False
    unsigned = {key: value for key, value in forged.items() if key != "state_digest"}
    forged["state_digest"] = cutover._hash(unsigned)
    with pytest.raises(cutover.CutoverRejected, match="paused path"):
        cutover.validate_state(forged)

    destination = tmp_path / "state.json"
    cutover.write_private_json(destination, state)
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        cutover.write_private_json(destination, state)


def test_operator_inputs_must_be_private_regular_files(tmp_path: Path):
    source = tmp_path / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    source.chmod(0o644)
    with pytest.raises(cutover.CutoverRejected, match="permissions"):
        cutover._load(source)

    source.chmod(0o600)
    assert cutover._load(source) == {}
    link = tmp_path / "manifest-link.json"
    link.symlink_to(source)
    with pytest.raises(cutover.CutoverRejected, match="regular"):
        cutover._load(link)


def test_cutover_manifest_example_has_the_exact_bounded_shape():
    path = Path(__file__).resolve().parents[1] / "config/cutover-manifest.local.json.example"
    example = json.loads(path.read_text(encoding="utf-8"))
    assert set(example) == cutover.MANIFEST_FIELDS
    assert set(example["gates"]) == set(cutover.GATES)
    assert set(example["gate_evidence_hashes"]) == set(cutover.GATES)


def test_runbook_covers_revocation_rollback_soak_and_no_fake_completion():
    text = " ".join((
        Path(__file__).resolve().parents[1] / "docs/runbooks/owner-cutover.md"
    ).read_text(encoding="utf-8").lower().split())
    for phrase in (
        "market_agent_secret",
        "service-role",
        "static telegram owner",
        "never destructively down-migrate",
        "pre-market",
        "quiet intraday",
        "active intraday",
        "post-market",
        "maintenance",
        "backup",
        "operational alert",
        "invitations remain disabled",
        "missing, stale, skipped, unknown, or failed",
    ):
        assert phrase in text


def test_tracked_roadmap_distinguishes_local_candidate_from_live_cutover():
    root = Path(__file__).resolve().parents[1]
    roadmap = " ".join((root / "docs/ROADMAP.md").read_text(encoding="utf-8").lower().split())
    assert "not deployed to staging or production" in roadmap
    assert "no owner-cutover or soak receipt exists" in roadmap
    assert "friend invitations remain disabled" in roadmap
