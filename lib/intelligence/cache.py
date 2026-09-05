"""Resumable, receipt-preserving cache for bounded intelligence collection."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from lib.intelligence.http import cache_key
from lib.intelligence.providers import CollectionResult


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
        if result.receipt.status not in {"succeeded", "cache_hit"}:
            return
        self._collections[key] = result

    def get_collection(self, key: str) -> CollectionResult | None:
        result = self._collections.get(key)
        if result is None:
            return None
        receipt = replace(result.receipt, status="cache_hit", request_cost=0)
        return replace(result, receipt=receipt)

    def put_run(self, run_id: str, receipt: object) -> None:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("invalid run cache key")
        self._runs[run_id] = receipt

    def get_run(self, run_id: str) -> object | None:
        return self._runs.get(run_id)


__all__ = ["ResumableCollectionCache"]
