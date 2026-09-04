"""Deterministic, review-only learning observations for market intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Literal, Mapping, Sequence
from uuid import UUID


ObservationKind = Literal["outcome", "missed_event", "source_failure", "noise"]
ObservationStatus = Literal["observation", "owner_review"]
ELIGIBLE_HORIZONS = frozenset((5, 21, 63))
MINIMUM_PROPOSAL_SAMPLE = 5


@dataclass(frozen=True, slots=True)
class LearningObservation:
    kind: ObservationKind
    original_run_id: UUID
    policy_version: int
    sample_size: int
    evidence_ids: tuple[UUID, ...]
    limitations: tuple[str, ...]
    proposed_change: Mapping[str, object] | None
    status: ObservationStatus
    horizon_days: Literal[0, 5, 21, 63] = 0
    benchmark: str | None = None
    metrics: Mapping[str, object] = MappingProxyType({})


def _value(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _uuid(value: object, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"invalid {field}") from error


def _frozen(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


def _rate(count: int, total: int) -> str:
    value = (Decimal(count) / Decimal(total)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return format(value, ".4f")


def _eligible_outcomes(outcomes: Sequence[object]) -> list[object]:
    return [
        row
        for row in outcomes
        if _value(row, "horizon_days") in ELIGIBLE_HORIZONS
        and _value(row, "horizon_sessions") == _value(row, "horizon_days")
        and _value(row, "coverage_status") == "complete"
        and isinstance(_value(row, "benchmark"), str)
        and bool(_value(row, "benchmark"))
        and _value(row, "excess_return_pct") is not None
        and isinstance(_value(row, "direction_success"), bool)
    ]


def evaluate_missed_event(
    original_run: object, later_items: Sequence[object]
) -> LearningObservation | None:
    """Attribute a miss only to later official proof inside declared coverage."""
    run_id = _uuid(_value(original_run, "id"), "original run id")
    policy_version = _value(original_run, "policy_version")
    if not isinstance(policy_version, int) or isinstance(policy_version, bool) or policy_version <= 0:
        raise ValueError("invalid policy version")
    coverage_start = _value(original_run, "coverage_start")
    coverage_end = _value(original_run, "coverage_end")
    completed_at = _value(original_run, "completed_at")
    covered_sources = frozenset(_value(original_run, "covered_sources") or ())
    ranked = frozenset(_value(original_run, "ranked_candidate_keys") or ())
    if coverage_start is None or coverage_end is None or completed_at is None:
        return None

    eligible: list[tuple[UUID, str]] = []
    for item in later_items:
        occurred_at = _value(item, "occurred_at")
        discovered_at = _value(item, "discovered_at")
        source = _value(item, "source")
        candidate_key = _value(item, "candidate_key")
        if (
            _value(item, "authoritative") is True
            and source in covered_sources
            and candidate_key not in ranked
            and occurred_at is not None
            and coverage_start <= occurred_at <= coverage_end
            and discovered_at is not None
            and discovered_at > completed_at
        ):
            eligible.append((_uuid(_value(item, "id"), "evidence id"), str(candidate_key)))
    if not eligible:
        return None
    eligible.sort(key=lambda item: (str(item[0]), item[1]))
    return LearningObservation(
        kind="missed_event",
        original_run_id=run_id,
        policy_version=policy_version,
        sample_size=len(eligible),
        evidence_ids=tuple(item[0] for item in eligible),
        limitations=(
            "later evidence does not prove contemporaneous discoverability",
            "miss attribution is limited to the original declared source and time window",
        ),
        proposed_change=None,
        status="observation",
        metrics=_frozen({"missed_candidate_keys": tuple(item[1] for item in eligible)}),
    )


def build_learning_proposal(outcomes: Sequence[object]) -> LearningObservation:
    eligible = _eligible_outcomes(outcomes)
    if not eligible:
        raise ValueError("no eligible outcomes")
    policy_versions = {_value(row, "policy_version") for row in eligible}
    if len(policy_versions) != 1 or not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in policy_versions
    ):
        raise ValueError("outcomes must share one policy version")
    benchmarks = {_value(row, "benchmark") for row in eligible}
    if len(benchmarks) != 1:
        raise ValueError("outcomes must share one benchmark")
    horizons = {_value(row, "horizon_days") for row in eligible}
    if len(horizons) != 1:
        raise ValueError("outcomes must share one horizon")
    run_ids = {_uuid(_value(row, "original_run_id"), "original run id") for row in eligible}
    if len(run_ids) != 1:
        raise ValueError("outcomes must share one original run")
    evidence_ids = tuple(
        sorted(
            {_uuid(_value(row, "evidence_id"), "evidence id") for row in eligible},
            key=str,
        )
    )
    false_positives = sum(_value(row, "direction_success") is False for row in eligible)
    false_positive_rate = _rate(false_positives, len(eligible))
    limitations = ["historical outcomes do not prove future performance"]
    proposed_change: Mapping[str, object] | None = None
    status: ObservationStatus = "observation"
    if len(eligible) >= MINIMUM_PROPOSAL_SAMPLE:
        status = "owner_review"
        proposed_change = _frozen(
            {
                "area": "candidate_ranking_review",
                "recommendation": "review_false_positive_rate",
                "false_positive_rate": false_positive_rate,
            }
        )
    else:
        limitations.append("minimum 5 eligible outcomes required for owner-review proposal")
    return LearningObservation(
        kind="outcome",
        original_run_id=next(iter(run_ids)),
        policy_version=next(iter(policy_versions)),
        sample_size=len(eligible),
        evidence_ids=evidence_ids,
        limitations=tuple(limitations),
        proposed_change=proposed_change,
        status=status,
        horizon_days=next(iter(horizons)),
        benchmark=next(iter(benchmarks)),
        metrics=_frozen(
            {
                "false_positive_count": false_positives,
                "false_positive_rate": false_positive_rate,
            }
        ),
    )


def build_source_failure_observation(
    *,
    original_run_id: UUID,
    policy_version: int,
    evidence_ids: Sequence[UUID],
    source: str,
    limitation: str,
) -> LearningObservation:
    if not source or len(source) > 100 or not limitation or len(limitation) > 500:
        raise ValueError("invalid source failure")
    return LearningObservation(
        kind="source_failure",
        original_run_id=_uuid(original_run_id, "original run id"),
        policy_version=policy_version,
        sample_size=max(1, len(evidence_ids)),
        evidence_ids=tuple(_uuid(value, "evidence id") for value in evidence_ids),
        limitations=(limitation,),
        proposed_change=None,
        status="observation",
        metrics=_frozen({"source": source, "failure_count": 1}),
    )


def build_noise_observation(
    *, original_run_id: UUID, policy_version: int, outcomes: Sequence[object]
) -> LearningObservation:
    eligible = _eligible_outcomes(outcomes)
    if not eligible:
        raise ValueError("no eligible outcomes")
    expected_run_id = _uuid(original_run_id, "original run id")
    if not isinstance(policy_version, int) or isinstance(policy_version, bool) or policy_version <= 0:
        raise ValueError("invalid policy version")
    if any(
        _uuid(_value(row, "original_run_id"), "original run id") != expected_run_id
        for row in eligible
    ):
        raise ValueError("outcomes must match the supplied original run")
    if any(_value(row, "policy_version") != policy_version for row in eligible):
        raise ValueError("outcomes must match the supplied policy version")
    horizons = {_value(row, "horizon_days") for row in eligible}
    if len(horizons) != 1:
        raise ValueError("outcomes must share one horizon")
    benchmarks = {_value(row, "benchmark") for row in eligible}
    if len(benchmarks) != 1:
        raise ValueError("outcomes must share one benchmark")
    false_positives = sum(_value(row, "direction_success") is False for row in eligible)
    return LearningObservation(
        kind="noise",
        original_run_id=expected_run_id,
        policy_version=policy_version,
        sample_size=len(eligible),
        evidence_ids=tuple(
            sorted(
                {_uuid(_value(row, "evidence_id"), "evidence id") for row in eligible},
                key=str,
            )
        ),
        limitations=("historical outcomes do not prove future performance",),
        proposed_change=None,
        status="observation",
        horizon_days=next(iter(horizons)),
        benchmark=next(iter(benchmarks)),
        metrics=_frozen(
            {
                "false_positive_count": false_positives,
                "false_positive_rate": _rate(false_positives, len(eligible)),
            }
        ),
    )
