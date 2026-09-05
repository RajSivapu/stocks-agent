"""Resumable, receipt-preserving cache for bounded intelligence collection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from lib.intelligence.http import cache_key
from lib.intelligence.providers import CollectionResult, RequestReceipt, SourceItem, parse_timestamp


class ResumableCollectionCache:
    """Keep completed collection work addressable by its immutable request identity.

    A caller can retain this object across an interrupted collector invocation.  Entries preserve
    the original source receipt; returned collection hits get a derived zero-cost receipt instead
    of consuming a quota reservation again.
    """

    def __init__(self) -> None:
        self._entries: dict[str, Mapping[str, Any]] = {}
        self._collections: dict[str, CollectionResult] = {}
        self._runs: dict[str, object] = {}

    @staticmethod
    def key(
        provider: str,
        query: Mapping[str, str],
        window: str,
        schema_version: int,
    ) -> str:
        return cache_key(provider, query, window, schema_version)

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        if not isinstance(key, str) or not key or not isinstance(value, Mapping):
            raise ValueError("invalid resumable cache entry")
        receipt = value.get("receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("resumable cache entry requires source receipt")
        self._entries[key] = MappingProxyType(deepcopy(dict(value)))

    def get(self, key: str) -> Mapping[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        result = deepcopy(dict(entry))
        result["cache_hit"] = True
        return MappingProxyType(result)

    def put_collection(self, key: str, result: CollectionResult) -> None:
        if result.receipt.status not in {"succeeded", "cache_hit", "failed"}:
            return
        self._collections[key] = result

    def hydrate_collections(self, entries: Iterable[Mapping[str, object]], *, now: datetime) -> None:
        """Rebuild only unexpired, gateway-validated collection checkpoints."""
        for entry in entries:
            key = entry.get("cache_key")
            receipt_row = entry.get("receipt")
            items_row = entry.get("items")
            if not isinstance(key, str) or not key or not isinstance(receipt_row, Mapping) or not isinstance(items_row, list):
                raise ValueError("invalid persisted collection checkpoint")
            receipt = _receipt_from_checkpoint(receipt_row)
            if receipt.status == "succeeded" and (receipt.expires_at is None or receipt.expires_at <= now.astimezone(timezone.utc)):
                continue
            items = tuple(_item_from_checkpoint(value) for value in items_row)
            self._collections[key] = CollectionResult(items, receipt, receipt.requested_limit)

    def get_collection(
        self, key: str, *, reservation_id: str, source_receipt_id: str, now: datetime
    ) -> CollectionResult | None:
        result = self._collections.get(key)
        if result is None:
            return None
        if result.receipt.status == "succeeded" and (result.receipt.expires_at is None or result.receipt.expires_at <= now.astimezone(timezone.utc)):
            self._collections.pop(key, None)
            return None
        predecessor = result.receipt.source_receipt_id
        if not predecessor:
            raise ValueError("cached collection is missing its persisted source receipt")
        if result.receipt.status == "failed":
            return replace(result, receipt=replace(
                result.receipt, reservation_id=reservation_id, request_cost=0,
                source_receipt_id=source_receipt_id,
            ))
        receipt = replace(
            result.receipt, reservation_id=reservation_id, status="cache_hit", request_cost=0,
            source_receipt_id=source_receipt_id, cache_predecessor_receipt_id=predecessor,
        )
        return replace(result, receipt=receipt)

    def put_run(self, run_id: str, receipt: object) -> None:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("invalid run cache key")
        self._runs[run_id] = receipt

    def get_run(self, run_id: str) -> object | None:
        return self._runs.get(run_id)


__all__ = ["ResumableCollectionCache"]


def _timestamp(value: object, name: str, *, nullable: bool = False) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is None and not nullable:
        raise ValueError(f"checkpoint {name} is invalid")
    return parsed


def _receipt_from_checkpoint(row: Mapping[str, object]) -> RequestReceipt:
    required = {
        "provider", "reservation_id", "status", "cache_key", "requested_window", "requested_limit",
        "retrieved_at", "observed_at", "expires_at", "request_cost", "upstream_remaining",
        "returned_count", "accepted_count", "duplicate_count", "dropped_count", "response_hash",
        "error_code", "source_receipt_id", "cache_predecessor_receipt_id",
    }
    if set(row) != required or not isinstance(row["requested_window"], Mapping):
        raise ValueError("invalid persisted receipt checkpoint")
    if not isinstance(row["source_receipt_id"], str) or not row["source_receipt_id"]:
        raise ValueError("invalid persisted receipt checkpoint")
    try:
        return RequestReceipt(
            provider=str(row["provider"]), reservation_id=str(row["reservation_id"]), status=str(row["status"]),
            cache_key=str(row["cache_key"]), requested_window=MappingProxyType(dict(row["requested_window"])),
            requested_limit=int(row["requested_limit"]), retrieved_at=_timestamp(row["retrieved_at"], "retrieved_at"),
            observed_at=_timestamp(row["observed_at"], "observed_at", nullable=True),
            expires_at=_timestamp(row["expires_at"], "expires_at"), request_cost=int(row["request_cost"]),
            upstream_remaining=None if row["upstream_remaining"] is None else int(row["upstream_remaining"]),
            returned_count=int(row["returned_count"]), accepted_count=int(row["accepted_count"]),
            duplicate_count=int(row["duplicate_count"]), dropped_count=int(row["dropped_count"]),
            response_hash=None if row["response_hash"] is None else str(row["response_hash"]),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
            source_receipt_id=row["source_receipt_id"],
            cache_predecessor_receipt_id=None if row["cache_predecessor_receipt_id"] is None else str(row["cache_predecessor_receipt_id"]),
        )
    except (TypeError, ValueError):
        raise ValueError("invalid persisted receipt checkpoint") from None


def _item_from_checkpoint(value: object) -> SourceItem:
    if not isinstance(value, Mapping) or set(value) != {
        "provider", "upstream_item_id", "source_url", "title", "normalized_text", "canonical_content",
        "content_hash", "published_at", "effective_at", "retrieved_at", "authority", "metadata",
        "request_url", "reporting_at", "entity_ids", "security_ids",
    } or not isinstance(value["metadata"], Mapping):
        raise ValueError("invalid persisted source item checkpoint")
    try:
        return SourceItem(
            provider=str(value["provider"]), upstream_item_id=None if value["upstream_item_id"] is None else str(value["upstream_item_id"]),
            source_url=str(value["source_url"]), title=str(value["title"]), normalized_text=str(value["normalized_text"]),
            canonical_content=str(value["canonical_content"]), content_hash=str(value["content_hash"]),
            published_at=_timestamp(value["published_at"], "published_at", nullable=True),
            effective_at=_timestamp(value["effective_at"], "effective_at", nullable=True),
            retrieved_at=_timestamp(value["retrieved_at"], "retrieved_at"), authority=str(value["authority"]),
            metadata=MappingProxyType(dict(value["metadata"])), request_url=None if value["request_url"] is None else str(value["request_url"]),
            reporting_at=_timestamp(value["reporting_at"], "reporting_at", nullable=True),
            entity_ids=tuple(str(item) for item in value["entity_ids"]), security_ids=tuple(str(item) for item in value["security_ids"]),
        )
    except (TypeError, ValueError):
        raise ValueError("invalid persisted source item checkpoint") from None
