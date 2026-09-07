"""Durable overlap windows and fail-closed source cursor transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import re
from typing import Literal


_MINIMUM_OVERLAP = timedelta(hours=2)
_MAX_ACCEPTED_ITEM_IDS = 500
_MAX_TOKEN_CHARACTERS = 2_048
_PHASES = frozenset({"pre-market", "intraday", "post-market", "on-demand"})
_CURSOR_STATUSES = frozenset({
    "succeeded",
    "cache_hit",
    "failed",
    "configuration_missing",
    "quota_blocked",
    "unsupported",
})


def parse_time(value: datetime | str) -> datetime:
    """Parse one bounded ISO-8601 timestamp and normalize it to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 1 <= len(value) <= 80:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("cursor timestamp must be ISO-8601") from None
    else:
        raise ValueError("cursor timestamp must be ISO-8601")
    if parsed.tzinfo is None:
        raise ValueError("cursor timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_time(value: datetime | str | None) -> datetime | None:
    return None if value is None else parse_time(value)


def _bounded_token(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_TOKEN_CHARACTERS:
        raise ValueError(f"{label} is invalid")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} is invalid")
    return value


def _item_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError("accepted item IDs must be a sequence")
    if len(values) > _MAX_ACCEPTED_ITEM_IDS:
        raise ValueError("accepted item IDs exceed bound")
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
        for value in values
    ):
        raise ValueError("accepted item ID is invalid")
    if len(set(values)) != len(values):
        raise ValueError("accepted item IDs must be unique")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SourceCursor:
    provider: str
    capability_id: str
    completed_through: datetime | str | None = None
    active_window_start: datetime | str | None = None
    active_window_end: datetime | str | None = None
    backlog_token: str | None = None
    page: int = 1
    accepted_item_ids: tuple[str, ...] = ()
    next_retry_phase: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or re.fullmatch(
            r"[a-z][a-z0-9_]{1,79}", self.provider
        ) is None:
            raise ValueError("cursor provider is invalid")
        if not isinstance(self.capability_id, str) or re.fullmatch(
            r"[a-z][a-z0-9_]{2,79}", self.capability_id
        ) is None:
            raise ValueError("cursor capability ID is invalid")
        completed = _optional_time(self.completed_through)
        active_start = _optional_time(self.active_window_start)
        active_end = _optional_time(self.active_window_end)
        if (active_start is None) != (active_end is None):
            raise ValueError("active cursor window must contain both bounds")
        if active_start is not None and active_start > active_end:
            raise ValueError("active cursor window is unordered")
        if completed is not None and active_start is not None and active_start > completed:
            raise ValueError("active cursor window must overlap the completed watermark")
        backlog = _bounded_token(self.backlog_token, "backlog token")
        if backlog is not None and active_start is None:
            raise ValueError("backlog token requires an active window")
        page = self.page
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10:
            raise ValueError("cursor page is invalid")
        if backlog is not None and page == 1:
            page = 2
        ids = _item_ids(self.accepted_item_ids)
        phase = self.next_retry_phase
        if phase is not None and phase not in _PHASES:
            raise ValueError("next retry phase is invalid")
        object.__setattr__(self, "completed_through", completed)
        object.__setattr__(self, "active_window_start", active_start)
        object.__setattr__(self, "active_window_end", active_end)
        object.__setattr__(self, "backlog_token", backlog)
        object.__setattr__(self, "page", page)
        object.__setattr__(self, "accepted_item_ids", ids)

    def to_mapping(self) -> dict[str, object]:
        def encoded(value: datetime | None) -> str | None:
            return value.isoformat().replace("+00:00", "Z") if value is not None else None

        return {
            "provider": self.provider,
            "capability_id": self.capability_id,
            "completed_through": encoded(self.completed_through),
            "active_window_start": encoded(self.active_window_start),
            "active_window_end": encoded(self.active_window_end),
            "backlog_token": self.backlog_token,
            "page": self.page,
            "accepted_item_ids": list(self.accepted_item_ids),
            "next_retry_phase": self.next_retry_phase,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SourceCursor:
        expected = {
            "provider",
            "capability_id",
            "completed_through",
            "active_window_start",
            "active_window_end",
            "backlog_token",
            "page",
            "accepted_item_ids",
            "next_retry_phase",
        }
        legacy = expected - {"page"}
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset(expected), frozenset(legacy)
        }:
            raise ValueError("persisted source cursor has invalid keys")
        return cls(
            provider=value["provider"],  # type: ignore[arg-type]
            capability_id=value["capability_id"],  # type: ignore[arg-type]
            completed_through=value["completed_through"],  # type: ignore[arg-type]
            active_window_start=value["active_window_start"],  # type: ignore[arg-type]
            active_window_end=value["active_window_end"],  # type: ignore[arg-type]
            backlog_token=value["backlog_token"],  # type: ignore[arg-type]
            page=value.get("page", 1),  # type: ignore[arg-type]
            accepted_item_ids=value["accepted_item_ids"],  # type: ignore[arg-type]
            next_retry_phase=value["next_retry_phase"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class CollectionWindow:
    start: datetime
    end: datetime
    overlap_seconds: int
    backlog_token: str | None = None

    def __post_init__(self) -> None:
        start = parse_time(self.start)
        end = parse_time(self.end)
        if start > end:
            raise ValueError("collection window is unordered")
        if (
            isinstance(self.overlap_seconds, bool)
            or not isinstance(self.overlap_seconds, int)
            or self.overlap_seconds < int(_MINIMUM_OVERLAP.total_seconds())
            or self.overlap_seconds > 31 * 24 * 60 * 60
        ):
            raise ValueError("overlap must be at least two hours and no more than 31 days")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "backlog_token", _bounded_token(self.backlog_token, "backlog token"))


@dataclass(frozen=True, slots=True)
class CollectionPage:
    window: CollectionWindow
    status: str
    exhausted: bool
    truncated: bool
    backlog_token: str | None
    accepted_item_ids: tuple[str, ...] = ()
    next_retry_phase: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _CURSOR_STATUSES:
            raise ValueError("collection page status is invalid")
        if not isinstance(self.exhausted, bool) or not isinstance(self.truncated, bool):
            raise ValueError("collection page flags must be booleans")
        if self.exhausted and self.truncated:
            raise ValueError("an exhausted page cannot be truncated")
        successful = self.status in {"succeeded", "cache_hit"}
        if successful and not self.exhausted and not self.truncated:
            raise ValueError("an unexhausted successful page must be truncated")
        if not successful and self.exhausted:
            raise ValueError("a failed page cannot establish exhaustion")
        if not successful and self.accepted_item_ids:
            raise ValueError("a failed page cannot accept item IDs")
        token = _bounded_token(self.backlog_token, "backlog token")
        if self.exhausted and token is not None:
            raise ValueError("an exhausted page cannot retain a backlog token")
        if self.next_retry_phase is not None and self.next_retry_phase not in _PHASES:
            raise ValueError("next retry phase is invalid")
        object.__setattr__(self, "backlog_token", token)
        object.__setattr__(self, "accepted_item_ids", _item_ids(self.accepted_item_ids))


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    status: str
    returned: int
    coverage_status: Literal[
        "success_empty",
        "success_nonempty",
        "source_failed",
        "configuration_missing",
        "quota_blocked",
        "unsupported",
    ]


def source_outcome(*, status: str, returned: int) -> SourceOutcome:
    if isinstance(returned, bool) or not isinstance(returned, int) or returned < 0:
        raise ValueError("returned count must be a non-negative integer")
    normalized = "succeeded" if status == "cache_hit" else status
    if normalized == "succeeded":
        coverage = "success_nonempty" if returned else "success_empty"
    elif normalized == "failed":
        coverage = "source_failed"
    elif normalized in {"configuration_missing", "quota_blocked", "unsupported"}:
        coverage = normalized
    else:
        raise ValueError("source outcome status is invalid")
    return SourceOutcome(status, returned, coverage)  # type: ignore[arg-type]


def window_from_cursor(
    cursor: SourceCursor,
    *,
    run_at: datetime | str,
    overlap: timedelta,
    max_backfill: timedelta,
) -> CollectionWindow:
    """Open a bounded window or resume the exact persisted active window."""
    if overlap < _MINIMUM_OVERLAP:
        raise ValueError("overlap must be at least two hours")
    if max_backfill < overlap or max_backfill > timedelta(days=31):
        raise ValueError("maximum backfill is invalid")
    end = parse_time(run_at)
    if cursor.active_window_start is not None:
        return CollectionWindow(
            start=cursor.active_window_start,
            end=cursor.active_window_end,
            overlap_seconds=int(overlap.total_seconds()),
            backlog_token=cursor.backlog_token,
        )
    earliest = end - max_backfill
    start = earliest
    if cursor.completed_through is not None:
        start = max(earliest, cursor.completed_through - overlap)
    return CollectionWindow(
        start=start,
        end=end,
        overlap_seconds=int(overlap.total_seconds()),
    )


def _merged_ids(existing: tuple[str, ...], new: tuple[str, ...]) -> tuple[str, ...]:
    combined = tuple(dict.fromkeys((*existing, *new)))
    return _item_ids(combined)


def update_cursor(cursor: SourceCursor, page: CollectionPage) -> SourceCursor:
    """Apply one page without skipping an unexhausted or failed interval."""
    successful = page.status in {"succeeded", "cache_hit"}
    if not successful:
        if cursor.active_window_start is not None and (
            page.window.start != cursor.active_window_start
            or page.window.end != cursor.active_window_end
        ):
            raise ValueError("page window does not match the active cursor window")
        if cursor.completed_through is not None and (
            page.window.start > cursor.completed_through
            or page.window.end < cursor.completed_through
        ):
            raise ValueError("page window does not overlap the completed watermark")
        return replace(
            cursor,
            active_window_start=cursor.active_window_start or page.window.start,
            active_window_end=cursor.active_window_end or page.window.end,
            backlog_token=cursor.backlog_token or page.backlog_token,
            page=cursor.page,
            next_retry_phase=page.next_retry_phase,
        )

    if cursor.active_window_start is not None and (
        page.window.start != cursor.active_window_start
        or page.window.end != cursor.active_window_end
    ):
        raise ValueError("page window does not match the active cursor window")
    if cursor.completed_through is not None and (
        page.window.start > cursor.completed_through
        or page.window.end < cursor.completed_through
    ):
        raise ValueError("page window is not contiguous with the completed watermark")
    accepted = _merged_ids(cursor.accepted_item_ids, page.accepted_item_ids)
    if not page.exhausted:
        if cursor.backlog_token is not None and page.backlog_token == cursor.backlog_token:
            raise ValueError("page repeated the active backlog token")
        return replace(
            cursor,
            active_window_start=page.window.start,
            active_window_end=page.window.end,
            backlog_token=page.backlog_token,
            page=cursor.page + 1 if page.backlog_token is not None else cursor.page,
            accepted_item_ids=accepted,
            next_retry_phase=page.next_retry_phase,
        )
    return replace(
        cursor,
        completed_through=page.window.end,
        active_window_start=None,
        active_window_end=None,
        backlog_token=None,
        page=1,
        # A completed contiguous window no longer needs the overlap-local ID
        # guard. Durable item hashes remain the cross-window dedupe authority.
        accepted_item_ids=(),
        next_retry_phase=None,
    )


__all__ = [
    "CollectionPage",
    "CollectionWindow",
    "SourceCursor",
    "SourceOutcome",
    "parse_time",
    "source_outcome",
    "update_cursor",
    "window_from_cursor",
]
