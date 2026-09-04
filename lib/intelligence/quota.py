"""Exact-once consumption of gateway-provided quota reservation IDs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class QuotaExceeded(RuntimeError):
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

    def consume(self, provider: str, reservation_id: str) -> None:
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

    def consume_next(self, provider: str) -> str:
        for reservation in self._available.get(provider, ()):
            if self._consumed[reservation.reservation_id] < reservation.reserved_requests:
                self.consume(provider, reservation.reservation_id)
                return reservation.reservation_id
        raise QuotaExceeded(provider)
