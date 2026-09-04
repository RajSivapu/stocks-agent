"""Stable exact and near-duplicate classification for normalized evidence."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Literal

from lib.intelligence.normalize import SourceItem


Disposition = Literal["accepted", "duplicate", "near_duplicate"]


@dataclass(frozen=True, slots=True)
class RunItemDisposition:
    item: SourceItem
    disposition: Disposition
    reason: str | None


def _near_duplicate(left: SourceItem, right: SourceItem) -> bool:
    left_text = f"{left.title}\n{left.summary}".casefold()
    right_text = f"{right.title}\n{right.summary}".casefold()
    return SequenceMatcher(None, left_text, right_text, autojunk=False).ratio() >= 0.88


def deduplicate(items: Iterable[SourceItem]) -> list[RunItemDisposition]:
    accepted: list[SourceItem] = []
    output: list[RunItemDisposition] = []
    for item in items:
        reason: str | None = None
        disposition: Disposition = "accepted"
        for canonical in accepted:
            if item.canonical_url and item.canonical_url == canonical.canonical_url:
                disposition, reason = "duplicate", "same_canonical_url"
                break
            if (
                item.provider == canonical.provider
                and item.upstream_item_id
                and item.upstream_item_id == canonical.upstream_item_id
            ):
                disposition, reason = "duplicate", "same_upstream_item_id"
                break
            if item.content_hash == canonical.content_hash:
                disposition, reason = "duplicate", "same_content_hash"
                break
            if _near_duplicate(item, canonical):
                disposition, reason = "near_duplicate", "similar_normalized_content"
                break
        if disposition == "accepted":
            accepted.append(item)
        output.append(RunItemDisposition(item=item, disposition=disposition, reason=reason))
    return output


__all__ = ["RunItemDisposition", "deduplicate"]
