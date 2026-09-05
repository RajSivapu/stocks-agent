"""Exact-once consumption of gateway-provided quota reservation IDs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


class QuotaExceeded(RuntimeError):
    code = "QUOTA_BLOCKED"
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(provider)


@dataclass(frozen=True, slots=True)
class _Reservation:
    reservation_id: str
    reserved_requests: int


class QuotaSession:
    def __init__(
        self,
        reservations: Mapping[str, Iterable[str | Mapping[str, object]]],
    ) -> None:
        available: dict[str, tuple[_Reservation, ...]] = {}
        all_ids: set[str] = set()
        for provider, reservation_entries in reservations.items():
            ordered: list[_Reservation] = []
            if not isinstance(provider, str) or not provider:
                raise ValueError("invalid quota reservation")
            for entry in reservation_entries:
                if isinstance(entry, str):
                    reservation = _Reservation(entry, 1)
                elif isinstance(entry, Mapping) and set(entry) == {
                    "reservation_id", "reserved_requests"
                }:
                    reservation_id = entry["reservation_id"]
                    reserved_requests = entry["reserved_requests"]
                    if (
                        not isinstance(reservation_id, str)
                        or not reservation_id
                        or isinstance(reserved_requests, bool)
                        or not isinstance(reserved_requests, int)
                        or not 1 <= reserved_requests <= 100
                    ):
                        raise ValueError("invalid quota reservation")
                    reservation = _Reservation(reservation_id, reserved_requests)
                else:
                    raise ValueError("invalid quota reservation")
                if not reservation.reservation_id:
                    raise ValueError("invalid quota reservation")
                if reservation.reservation_id in all_ids:
                    raise ValueError("duplicate quota reservation")
                all_ids.add(reservation.reservation_id)
                ordered.append(reservation)
            available[provider] = ordered
        self._available = {
            provider: tuple(provider_reservations)
            for provider, provider_reservations in available.items()
        }
        self._consumed: dict[str, int] = {reservation_id: 0 for reservation_id in all_ids}
        self._actual_requests: dict[str, int] = {provider: 0 for provider in available}
        self._cache_hits: dict[str, int] = {provider: 0 for provider in available}

    def consume(self, provider: str, reservation_id: str) -> None:
        self.record_actual_request(provider, reservation_id)

    def record_actual_request(self, provider: str, reservation_id: str) -> None:
        available = self._available.get(provider, ())
        reservation = next(
            (entry for entry in available if entry.reservation_id == reservation_id),
            None,
        )
        if (
            reservation is None
            or self._consumed[reservation_id] >= reservation.reserved_requests
        ):
            raise QuotaExceeded(provider)
        self._consumed[reservation_id] += 1
        self._actual_requests[provider] += 1

    def record_cache_hit(self, provider: str, reservation_id: str) -> None:
        if not any(entry.reservation_id == reservation_id for entry in self._available.get(provider, ())):
            raise QuotaExceeded(provider)
        self._cache_hits[provider] += 1

    @property
    def actual_requests(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._actual_requests))

    @property
    def cache_hits(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._cache_hits))

    @property
    def consumed_requests(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._actual_requests))

    def consume_next(self, provider: str) -> str:
        for reservation in self._available.get(provider, ()):
            if self._consumed[reservation.reservation_id] < reservation.reserved_requests:
                self.consume(provider, reservation.reservation_id)
                return reservation.reservation_id
        raise QuotaExceeded(provider)

    def next_reservation_id(self, provider: str) -> str:
        for reservation in self._available.get(provider, ()):
            if self._consumed[reservation.reservation_id] < reservation.reserved_requests:
                return reservation.reservation_id
        raise QuotaExceeded(provider)

    def receipt_reservation_id(self, provider: str) -> str:
        """Retain the admitted reservation identity after its capacity is exhausted."""
        entries = self._available.get(provider, ())
        return entries[0].reservation_id if entries else ""
