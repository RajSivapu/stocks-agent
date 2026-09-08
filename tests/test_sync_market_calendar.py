from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.sync_market_calendar import (
    SOURCE,
    TARGETS,
    canonical_json,
    load_calendar,
    render_typescript,
    sync,
    validate_calendar,
)


def test_reviewed_calendar_contains_exact_official_2027_and_2028_sessions():
    calendar = load_calendar()

    assert calendar["source"]["url"] == "https://www.nyse.com/trade/hours-calendars"
    assert calendar["years"]["2027"] == {
        "full_day_closures": [
            "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
            "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
            "2027-11-25", "2027-12-24",
        ],
        "early_closes": [{"date": "2027-11-26", "local_time": "13:00"}],
    }
    assert calendar["years"]["2028"] == {
        "full_day_closures": [
            "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
            "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23",
            "2028-12-25",
        ],
        "early_closes": [
            {"date": "2028-07-03", "local_time": "13:00"},
            {"date": "2028-11-24", "local_time": "13:00"},
        ],
    }
    assert "2027-12-31" not in calendar["years"]["2027"]["full_day_closures"]


@pytest.mark.parametrize("mutation", [
    lambda value: value["years"]["2027"]["full_day_closures"].append("2027-02-30"),
    lambda value: value["years"]["2027"]["full_day_closures"].append("2027-01-01"),
    lambda value: value["years"]["2027"]["full_day_closures"].reverse(),
    lambda value: value["years"]["2028"]["early_closes"].append(
        {"date": "2028-07-04", "local_time": "13:00"}
    ),
    lambda value: value["years"]["2028"]["early_closes"].append(
        {"date": "2029-01-02", "local_time": "13:00"}
    ),
    lambda value: value["coverage"].update(end_year=2029),
])
def test_calendar_validation_rejects_invalid_order_duplicates_dates_overlap_and_bounds(mutation):
    calendar = copy.deepcopy(json.loads(SOURCE.read_text()))
    mutation(calendar)

    with pytest.raises(ValueError, match="calendar|date|closure|coverage"):
        validate_calendar(calendar)


def test_checked_in_edge_copies_are_exact_canonical_generated_parity():
    calendar = load_calendar()
    rendered = render_typescript(calendar)
    source_hash = hashlib.sha256(canonical_json(calendar).encode()).hexdigest()

    assert sync(check=True)
    for target in TARGETS:
        assert target.read_text() == rendered
        assert f'NYSE_CALENDAR_SOURCE_SHA256 = "{source_hash}"' in rendered
        generated_json = rendered.split("NYSE_CALENDAR = ", 1)[1].rsplit(" as const;", 1)[0]
        assert json.loads(generated_json) == calendar


def test_check_mode_detects_drift_without_rewriting_targets(tmp_path: Path):
    source = tmp_path / "calendar.json"
    source.write_text(SOURCE.read_text())
    first = tmp_path / "gateway.ts"
    second = tmp_path / "dashboard.ts"

    assert sync(source=source, targets=(first, second))
    first.write_text("drift\n")
    assert sync(source=source, targets=(first, second), check=True) is False
    assert first.read_text() == "drift\n"
    assert sync(source=source, targets=(first, second))
    assert sync(source=source, targets=(first, second), check=True) is True
