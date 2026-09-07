from dataclasses import replace
from datetime import timedelta

import pytest

from lib.intelligence.cursors import (
    CollectionPage,
    SourceCursor,
    parse_time,
    source_outcome,
    update_cursor,
    window_from_cursor,
)


RUN_AT = parse_time("2026-09-08T11:30:00Z")
COMPLETED = parse_time("2026-09-04T20:00:00Z")


def _cursor(**overrides):
    values = {
        "provider": "doe",
        "capability_id": "doe_energy_news_rss",
        "completed_through": COMPLETED,
    }
    values.update(overrides)
    return SourceCursor(**values)


def _window():
    return window_from_cursor(
        _cursor(),
        run_at=RUN_AT,
        overlap=timedelta(hours=2),
        max_backfill=timedelta(days=7),
    )


def test_long_weekend_window_resumes_from_cursor_with_overlap():
    window = _window()

    assert window.start == parse_time("2026-09-04T18:00:00Z")
    assert window.end == RUN_AT
    assert window.overlap_seconds == 7_200


def test_collection_window_rejects_less_than_two_hours_of_overlap():
    with pytest.raises(ValueError, match="overlap must be at least two hours"):
        window_from_cursor(
            _cursor(),
            run_at=RUN_AT,
            overlap=timedelta(minutes=30),
            max_backfill=timedelta(days=7),
        )


def test_newest_first_truncation_saves_backlog_without_advancing_watermark():
    previous = _cursor()
    page = CollectionPage(
        window=_window(),
        status="succeeded",
        exhausted=False,
        truncated=True,
        backlog_token="cursor:older-page-2",
        accepted_item_ids=("doe:1", "doe:2"),
        next_retry_phase="post-market",
    )

    updated = update_cursor(previous, page)

    assert updated.completed_through == previous.completed_through
    assert updated.active_window_start == page.window.start
    assert updated.active_window_end == RUN_AT
    assert updated.backlog_token == "cursor:older-page-2"
    assert updated.accepted_item_ids == ("doe:1", "doe:2")
    assert updated.next_retry_phase == "post-market"


def test_saved_backlog_resumes_the_exact_active_window():
    previous = _cursor(
        active_window_start=parse_time("2026-09-04T18:00:00Z"),
        active_window_end=RUN_AT,
        backlog_token="cursor:older-page-2",
    )

    resumed = window_from_cursor(
        previous,
        run_at=parse_time("2026-09-09T11:30:00Z"),
        overlap=timedelta(hours=2),
        max_backfill=timedelta(days=7),
    )

    assert resumed.start == previous.active_window_start
    assert resumed.end == previous.active_window_end
    assert resumed.backlog_token == previous.backlog_token


def test_exhausted_empty_window_advances_completed_window():
    previous = _cursor(accepted_item_ids=("doe:already-seen",))
    page = CollectionPage(
        window=_window(),
        status="succeeded",
        exhausted=True,
        truncated=False,
        backlog_token=None,
    )

    updated = update_cursor(previous, page)

    assert updated.completed_through == RUN_AT
    assert updated.active_window_start is None
    assert updated.active_window_end is None
    assert updated.backlog_token is None
    assert updated.accepted_item_ids == ()


def test_unpageable_rolling_feed_overflow_freezes_window_without_fake_cursor():
    previous = _cursor()
    page = CollectionPage(
        window=_window(),
        status="succeeded",
        exhausted=False,
        truncated=True,
        backlog_token=None,
        accepted_item_ids=("doe:1",),
        next_retry_phase="post-market",
    )

    updated = update_cursor(previous, page)

    assert updated.completed_through == previous.completed_through
    assert updated.active_window_start == page.window.start
    assert updated.active_window_end == page.window.end
    assert updated.backlog_token is None
    assert updated.accepted_item_ids == ("doe:1",)
    assert updated.next_retry_phase == "post-market"


def test_pageable_cursor_rejects_a_repeated_continuation_token():
    previous = _cursor(
        active_window_start=parse_time("2026-09-04T18:00:00Z"),
        active_window_end=RUN_AT,
        backlog_token="cursor:older-page-2",
    )
    page = CollectionPage(
        window=_window(),
        status="succeeded",
        exhausted=False,
        truncated=True,
        backlog_token="cursor:older-page-2",
        next_retry_phase="post-market",
    )

    with pytest.raises(ValueError, match="repeated"):
        update_cursor(previous, page)


def test_failure_retains_watermark_and_safe_resume_state():
    previous = _cursor(
        active_window_start=parse_time("2026-09-04T18:00:00Z"),
        active_window_end=RUN_AT,
        backlog_token="cursor:older-page-2",
        accepted_item_ids=("doe:1",),
    )
    page = CollectionPage(
        window=_window(),
        status="failed",
        exhausted=False,
        truncated=False,
        backlog_token=None,
        next_retry_phase="post-market",
    )

    updated = update_cursor(previous, page)

    assert updated.completed_through == previous.completed_through
    assert updated.active_window_start == previous.active_window_start
    assert updated.active_window_end == previous.active_window_end
    assert updated.backlog_token == previous.backlog_token
    assert updated.accepted_item_ids == previous.accepted_item_ids
    assert updated.next_retry_phase == "post-market"


def test_first_failure_persists_the_exact_window_for_retry():
    previous = _cursor()
    page = CollectionPage(
        window=_window(),
        status="configuration_missing",
        exhausted=False,
        truncated=False,
        backlog_token=None,
        next_retry_phase="post-market",
    )

    updated = update_cursor(previous, page)
    resumed = window_from_cursor(
        updated,
        run_at=parse_time("2026-09-10T11:30:00Z"),
        overlap=timedelta(hours=2),
        max_backfill=timedelta(days=7),
    )

    assert resumed.start == page.window.start
    assert resumed.end == page.window.end
    assert updated.completed_through == previous.completed_through


def test_cursor_rejects_inconsistent_page_progress():
    with pytest.raises(ValueError, match="truncated"):
        CollectionPage(
            window=_window(),
            status="succeeded",
            exhausted=False,
            truncated=False,
            backlog_token="page-2",
        )

    older_window = replace(_window(), end=COMPLETED - timedelta(minutes=1))
    with pytest.raises(ValueError, match="watermark"):
        update_cursor(
            _cursor(),
            CollectionPage(
                window=older_window,
                status="succeeded",
                exhausted=True,
                truncated=False,
                backlog_token=None,
            ),
        )


def test_failed_source_is_not_reported_as_no_event():
    assert source_outcome(status="failed", returned=0).coverage_status == "source_failed"
    assert source_outcome(status="succeeded", returned=0).coverage_status == "success_empty"


def test_cursor_persistence_round_trip_is_exact_and_bounded():
    cursor = _cursor(
        active_window_start=parse_time("2026-09-04T18:00:00Z"),
        active_window_end=RUN_AT,
        backlog_token="cursor:older-page-2",
        accepted_item_ids=("doe:1", "doe:2"),
        next_retry_phase="post-market",
    )

    assert SourceCursor.from_mapping(cursor.to_mapping()) == cursor

    with pytest.raises(ValueError, match="accepted item IDs exceed bound"):
        SourceCursor(
            provider="doe",
            capability_id="doe_energy_news_rss",
            completed_through=COMPLETED,
            accepted_item_ids=tuple(f"doe:{index}" for index in range(501)),
        )
