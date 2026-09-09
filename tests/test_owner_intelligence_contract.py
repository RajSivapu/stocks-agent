import copy
import json
from pathlib import Path

import pytest

from scripts.owner_intelligence_contract import validate_owner_intelligence_v2


PARITY_CASES = json.loads(
    (Path(__file__).parent / "fixtures/owner_intelligence_contract_cases.json").read_text()
)


def empty_projection():
    return {
        "intelligence_version": 2,
        "run_id": None,
        "data_as_of": None,
        "themes": [],
        "companies": [],
        "evidence": [],
        "source_health": [],
        "coverage": {"mode": "bounded", "complete_market_coverage": False},
        "reference": {"state": "unavailable"},
        "scope": {"research_only": True, "market_wide": True},
        "backlog": {"available": 0, "returned": 0, "deferred": 0, "byte_truncated": False},
        "omissions": [],
        "boundaries": {
            "research_only": True,
            "execution_disabled": True,
            "valuation_unavailable": True,
        },
    }


def theme(mechanism: str):
    return {
        "theme_id": "data_center_power",
        "episode_id": "11111111-1111-4111-8111-111111111111",
        "revision_id": "22222222-2222-4222-8222-222222222222",
        "revision": 1,
        "mechanism": mechanism,
        "subject": "US data centers",
        "jurisdiction": "US",
        "state": "active",
        "first_seen": "2026-09-08T12:00:00Z",
        "last_seen": "2026-09-08T12:00:00.123456+00:00",
        "next_review_at": "2026-09-09T12:00:00-05:00",
        "expires_at": "2026-10-08T12:00:00Z",
        "adverse_evidence_count": 0,
        "missing_questions": [],
        "invalidation_conditions": [],
    }


def evidence(passage: str):
    return {
        "evidence_id": "33333333-3333-4333-8333-333333333333",
        "label": "Official evidence",
        "url": None,
        "passage": passage,
        "role": "supporting",
        "retrieved_at": "2026-09-08T12:00:00Z",
    }


def test_empty_owner_intelligence_projection_matches_the_exact_v2_contract():
    value = empty_projection()
    assert validate_owner_intelligence_v2(value) == value


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda case: case["name"])
def test_shared_cross_runtime_acceptance_corpus(case):
    value = empty_projection()
    target = value
    for key in case["path"][:-1]:
        target = target[key]
    if case["path"]:
        target[case["path"][-1]] = case["value"]
    if case["accepted"]:
        validate_owner_intelligence_v2(value)
    else:
        with pytest.raises(ValueError):
            validate_owner_intelligence_v2(value)


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("theme", "😀" * 120, "😀" * 121),
        ("evidence", "😀" * 1000, "😀" * 1001),
    ],
)
def test_bounded_strings_use_javascript_utf16_code_units(field, accepted, rejected):
    value = empty_projection()
    value["themes"] = [theme(accepted if field == "theme" else "valid mechanism")]
    value["evidence"] = [evidence(accepted if field == "evidence" else "valid passage")]
    validate_owner_intelligence_v2(value)

    invalid = copy.deepcopy(value)
    if field == "theme":
        invalid["themes"][0]["mechanism"] = rejected
    else:
        invalid["evidence"][0]["passage"] = rejected
    with pytest.raises(ValueError, match="bounded string"):
        validate_owner_intelligence_v2(invalid)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-W01-1",
        "2026-02-30T12:00:00Z",
        "2026-09-08 12:00:00Z",
        "0000-01-01T00:00:00Z",
        "2026-09-08T24:00:00Z",
        "2026-09-08T12:00:00+24:00",
    ],
)
def test_bounded_timestamps_reject_noncanonical_or_invalid_dates(timestamp):
    value = empty_projection()
    value["data_as_of"] = timestamp
    with pytest.raises(ValueError, match="ISO timestamp"):
        validate_owner_intelligence_v2(value)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-08T12:00:00Z",
        "2026-09-08T12:00:00.123456+00:00",
        "2024-02-29T23:59:59-05:30",
    ],
)
def test_bounded_timestamps_accept_canonical_postgres_shapes(timestamp):
    value = empty_projection()
    value["data_as_of"] = timestamp
    validate_owner_intelligence_v2(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(run_id="not-a-uuid"),
        lambda value: value.update(data_as_of=7),
        lambda value: value.update(coverage={"mode": "all", "complete_market_coverage": True}),
        lambda value: value.update(backlog={"available": -1, "returned": 0, "deferred": 0, "byte_truncated": False}),
        lambda value: value.update(themes=[{"theme_id": "missing-required-fields"}]),
    ],
)
def test_owner_intelligence_projection_rejects_values_the_typescript_parser_rejects(mutation):
    value = copy.deepcopy(empty_projection())
    mutation(value)
    with pytest.raises(ValueError):
        validate_owner_intelligence_v2(value)
