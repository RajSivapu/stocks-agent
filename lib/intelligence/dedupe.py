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


def _contradicts(left: SourceItem, right: SourceItem) -> bool:
    return (
        left.claim_polarity in {"affirmed", "denied"}
        and right.claim_polarity in {"affirmed", "denied"}
        and left.claim_polarity != right.claim_polarity
    )


def deduplicate(items: Iterable[SourceItem]) -> list[RunItemDisposition]:
    accepted: list[SourceItem] = []
    output: list[RunItemDisposition] = []
    for item in items:
        reason: str | None = None
        disposition: Disposition = "accepted"
        upstream_identity = (
            (item.provider, item.upstream_item_id) if item.upstream_item_id else None
        )
        upstream_matches = [
            canonical for canonical in accepted
            if upstream_identity
            and canonical.provider == item.provider
            and canonical.upstream_item_id == item.upstream_item_id
        ]
        hash_matches = [
            canonical for canonical in accepted if canonical.content_hash == item.content_hash
        ]
        if upstream_matches and not any(_contradicts(item, canonical) for canonical in upstream_matches):
            disposition, reason = "duplicate", "same_upstream_item_id"
        elif hash_matches and not any(_contradicts(item, canonical) for canonical in hash_matches):
            disposition, reason = "duplicate", "same_content_hash"
        else:
            for canonical in accepted:
                if not _contradicts(item, canonical) and _near_duplicate(item, canonical):
                    disposition, reason = "near_duplicate", "similar_normalized_content"
                    break
        if disposition == "accepted":
            accepted.append(item)
        output.append(RunItemDisposition(item=item, disposition=disposition, reason=reason))
    return output


__all__ = ["RunItemDisposition", "deduplicate"]
