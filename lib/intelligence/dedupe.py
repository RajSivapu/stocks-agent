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
    seen_urls: set[str] = set()
    seen_upstream_ids: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    output: list[RunItemDisposition] = []
    for item in items:
        reason: str | None = None
        disposition: Disposition = "accepted"
        upstream_identity = (
            (item.provider, item.upstream_item_id) if item.upstream_item_id else None
        )
        if item.canonical_url and item.canonical_url in seen_urls:
            disposition, reason = "duplicate", "same_canonical_url"
        elif upstream_identity and upstream_identity in seen_upstream_ids:
            disposition, reason = "duplicate", "same_upstream_item_id"
        elif item.content_hash in seen_hashes:
            disposition, reason = "duplicate", "same_content_hash"
        else:
            for canonical in accepted:
                if _near_duplicate(item, canonical):
                    disposition, reason = "near_duplicate", "similar_normalized_content"
                    break
        if disposition == "accepted":
            accepted.append(item)
        if item.canonical_url:
            seen_urls.add(item.canonical_url)
        if upstream_identity:
            seen_upstream_ids.add(upstream_identity)
        seen_hashes.add(item.content_hash)
        output.append(RunItemDisposition(item=item, disposition=disposition, reason=reason))
    return output


__all__ = ["RunItemDisposition", "deduplicate"]
