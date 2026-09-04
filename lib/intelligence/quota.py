"""Exact-once consumption of gateway-provided quota reservation IDs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


class QuotaExceeded(RuntimeError):
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(provider)


class QuotaSession:
    def __init__(self, reservations: Mapping[str, Iterable[str]]) -> None:
        available: dict[str, tuple[str, ...]] = {}
        all_ids: set[str] = set()
        for provider, reservation_ids in reservations.items():
            ordered = tuple(reservation_ids)
            if (
                not provider
                or any(not reservation_id for reservation_id in ordered)
                or len(set(ordered)) != len(ordered)
                or all_ids.intersection(ordered)
            ):
                raise ValueError("duplicate or invalid quota reservation")
            all_ids.update(ordered)
            available[provider] = ordered
        self._available = available
        self._used: set[str] = set()

    def consume(self, provider: str, reservation_id: str) -> None:
        available = self._available.get(provider, ())
        if reservation_id not in available or reservation_id in self._used:
            raise QuotaExceeded(provider)
        self._used.add(reservation_id)

    def consume_next(self, provider: str) -> str:
        for reservation_id in self._available.get(provider, ()):
            if reservation_id not in self._used:
                self.consume(provider, reservation_id)
                return reservation_id
        raise QuotaExceeded(provider)
