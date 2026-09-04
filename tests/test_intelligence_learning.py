from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID

import pytest

from lib.intelligence.learning import (
    build_learning_proposal,
    build_noise_observation,
    build_source_failure_observation,
    evaluate_missed_event,
)


RUN_ID = UUID("00000000-0000-4000-8000-000000000002")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000010")


def original_run():
    return {
        "id": RUN_ID,
        "policy_version": 3,
        "completed_at": datetime(2026, 9, 4, 16, tzinfo=timezone.utc),
        "coverage_start": datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        "coverage_end": datetime(2026, 9, 4, 16, tzinfo=timezone.utc),
        "covered_sources": ("sec", "white_house"),
        "ranked_candidate_keys": ("CENX",),
    }


def later_item(**overrides):
    item = {
        "id": EVIDENCE_ID,
        "source": "sec",
        "authoritative": True,
        "occurred_at": datetime(2026, 9, 4, 15, tzinfo=timezone.utc),
        "discovered_at": datetime(2026, 9, 5, 12, tzinfo=timezone.utc),
        "candidate_key": "AA",
    }
    item.update(overrides)
    return item


def social_item():
    return later_item(source="social", authoritative=False)


def outcome(index: int, **overrides):
    row = {
        "original_run_id": RUN_ID,
        "evidence_id": UUID(f"00000000-0000-4000-8000-{index:012d}"),
        "policy_version": 3,
        "horizon_days": 21,
        "horizon_sessions": 21,
        "coverage_status": "complete",
        "benchmark": "VOO",
        "excess_return_pct": "1.2500" if index % 3 else "-0.5000",
        "direction_success": index % 3 != 0,
    }
    row.update(overrides)
    return row


def outcomes(count: int = 6):
    return [outcome(index + 1) for index in range(count)]


def test_missed_event_requires_later_authoritative_evidence():
    assert evaluate_missed_event(original_run(), later_items=[social_item()]) is None


@pytest.mark.parametrize(
    "change",
    [
        {"source": "doe"},
        {"occurred_at": datetime(2026, 9, 4, 17, tzinfo=timezone.utc)},
        {"discovered_at": datetime(2026, 9, 4, 15, tzinfo=timezone.utc)},
        {"candidate_key": "CENX"},
    ],
)
def test_missed_event_does_not_blame_out_of_coverage_or_already_ranked_evidence(change):
    assert evaluate_missed_event(original_run(), [later_item(**change)]) is None


def test_missed_event_records_only_covered_later_official_evidence():
    observation = evaluate_missed_event(original_run(), [later_item()])

    assert observation is not None
    assert observation.kind == "missed_event"
    assert observation.original_run_id == RUN_ID
    assert observation.policy_version == 3
    assert observation.sample_size == 1
    assert observation.evidence_ids == (EVIDENCE_ID,)
    assert observation.horizon_days == 0
    assert observation.benchmark is None
    assert observation.status == "observation"
    assert observation.proposed_change is None
    assert "later evidence does not prove contemporaneous discoverability" in observation.limitations


def test_learning_proposal_cannot_apply_policy():
    proposal = build_learning_proposal(outcomes())

    assert proposal.status == "owner_review"
    assert not hasattr(proposal, "apply")
    assert not hasattr(proposal, "update")
    assert proposal.sample_size == len(outcomes())
    assert proposal.horizon_days == 21
    assert proposal.benchmark == "VOO"
    assert proposal.proposed_change == {
        "area": "candidate_ranking_review",
        "recommendation": "review_false_positive_rate",
        "false_positive_rate": "0.3333",
    }
    with pytest.raises(TypeError):
        proposal.proposed_change["area"] = "policy"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        proposal.sample_size = 0  # type: ignore[misc]


def test_insufficient_or_ineligible_outcomes_remain_observations_only():
    rows = outcomes(3) + [
        outcome(20, horizon_days=10, horizon_sessions=10),
        outcome(21, coverage_status="incomplete"),
        outcome(22, benchmark=None),
    ]
    observation = build_learning_proposal(rows)

    assert observation.status == "observation"
    assert observation.sample_size == 3
    assert observation.proposed_change is None
    assert "minimum 5 eligible outcomes required for owner-review proposal" in observation.limitations


def test_learning_rejects_mixed_policy_versions_and_benchmarks():
    with pytest.raises(ValueError, match="policy version"):
        build_learning_proposal(outcomes() + [outcome(30, policy_version=2)])
    with pytest.raises(ValueError, match="benchmark"):
        build_learning_proposal(outcomes() + [outcome(31, benchmark="VXUS")])


def test_source_failure_and_noise_are_explicit_observations():
    source_failure = build_source_failure_observation(
        original_run_id=RUN_ID,
        policy_version=3,
        evidence_ids=(EVIDENCE_ID,),
        source="sec",
        limitation="SEC retrieval failed; affected coverage is incomplete.",
    )
    noise = build_noise_observation(
        original_run_id=RUN_ID,
        policy_version=3,
        outcomes=outcomes(),
    )

    assert source_failure.kind == "source_failure"
    assert source_failure.status == "observation"
    assert source_failure.metrics == {"source": "sec", "failure_count": 1}
    assert noise.kind == "noise"
    assert noise.metrics["false_positive_count"] == 2
    assert noise.metrics["false_positive_rate"] == "0.3333"
    assert noise.proposed_change is None
