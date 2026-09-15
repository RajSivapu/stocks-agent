#!/usr/bin/env python3
"""Wait for one already-running collection to persist its terminal receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import gateway  # noqa: E402
from lib.intelligence.pipeline import (  # noqa: E402
    PersistedPacket,
    PipelineReceipt,
)


DEFAULT_TIMEOUT_SECONDS = 900.0
MAX_TIMEOUT_SECONDS = 1_200.0
DEFAULT_POLL_SECONDS = 5.0
RETRYABLE_GATEWAY_CODES = frozenset({
    "GATEWAY_UNAVAILABLE",
    "INVALID_GATEWAY_RESPONSE",
    "PERSISTENCE_FAILED",
})


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def completion_request_id(run_id: str) -> str:
    """Return the collector's deterministic terminal request identity."""
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"market-intelligence:completion-request:{run_id}",
    ))


def _valid_run_id(run_id: str) -> bool:
    try:
        return str(uuid.UUID(run_id)) == run_id
    except (AttributeError, TypeError, ValueError):
        return False


def _mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    return value


def _rows(value: object, message: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise ValueError(message)
    return value


def _strings(value: object, message: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(message)
    return tuple(value)


def _receipt_from_completion(
    saved: object,
    *,
    run_id: str,
    completion_id: str,
) -> PipelineReceipt:
    """Validate and reconstruct the collector's bounded terminal receipt."""
    completion = _mapping(saved, "invalid persisted completion")
    final = _mapping(completion.get("receipt"), "invalid persisted completion receipt")
    payload = _mapping(completion.get("payload"), "invalid persisted completion payload")
    providers = _mapping(completion.get("providers"), "invalid persisted completion providers")

    if final.get("run_id") != run_id or final.get("completion_id") != completion_id:
        raise ValueError("persisted completion identity mismatch")

    packet = _mapping(payload.get("packet"), "invalid persisted completion packet")
    packet_value = _mapping(packet.get("packet"), "invalid persisted completion packet")
    packet_id = packet.get("id")
    packet_hash = packet.get("packet_hash")
    if (
        not isinstance(packet_id, str)
        or not isinstance(packet_hash, str)
        or final.get("packet_id") != packet_id
        or final.get("packet_hash") != packet_hash
        or hashlib.sha256(_canonical(packet_value).encode()).hexdigest() != packet_hash
    ):
        raise ValueError("persisted completion packet mismatch")

    receipt_rows = _rows(payload.get("receipts"), "invalid persisted completion sources")
    sources: list[dict[str, object]] = []
    actual_requests = 0
    cache_hits = 0
    for row in receipt_rows:
        reservation_id = row.get("reservation_id")
        provider = providers.get(reservation_id) if isinstance(reservation_id, str) else None
        error = row.get("error")
        if error is not None and not isinstance(error, Mapping):
            raise ValueError("invalid persisted completion sources")
        request_cost = row.get("request_cost")
        if isinstance(request_cost, bool) or not isinstance(request_cost, int) or request_cost < 0:
            raise ValueError("invalid persisted completion sources")
        if not isinstance(provider, str):
            raise ValueError("invalid persisted completion sources")
        status = row.get("status")
        if not isinstance(status, str):
            raise ValueError("invalid persisted completion sources")
        sources.append({
            "accepted_count": row.get("accepted_count"),
            "error_code": error.get("code") if error else None,
            "provider": provider,
            "receipt_id": row.get("id"),
            "reservation_id": reservation_id,
            "response_hash": row.get("response_hash"),
            "status": status,
        })
        actual_requests += request_cost
        cache_hits += status == "cache_hit"

    coverage = _mapping(payload.get("coverage"), "invalid persisted completion coverage")
    drops = _rows(coverage.get("collector_drops", []), "invalid persisted completion drops")
    domains_checked = _strings(
        coverage.get("domains_checked", []),
        "invalid persisted completion domains",
    )
    packet_limitations = _strings(
        packet_value.get("limitations", []),
        "invalid persisted completion limitations",
    )
    source_limitations = tuple(sorted(
        f"{row['provider']}:{row['error_code']}"
        for row in sources
        if row["error_code"]
    ))
    counts = _mapping(final.get("counts"), "invalid persisted completion counts")
    write_counts: dict[str, int] = {}
    for key, value in counts.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError("invalid persisted completion counts")
        write_counts[key] = value
    telegram_ids = final.get("telegram_message_ids", [])
    if not isinstance(telegram_ids, list):
        raise ValueError("invalid persisted completion receipt")

    return PipelineReceipt(
        run_id=run_id,
        packet=PersistedPacket(packet_id, packet_hash, packet_value),
        sources=tuple(sources),
        drops=tuple(dict(row) for row in drops),
        coverage=dict(coverage),
        write_counts=write_counts,
        domains_checked=domains_checked,
        limitations=source_limitations + packet_limitations,
        telegram_message_ids=tuple(telegram_ids),
        completion_id=completion_id,
        actual_requests=actual_requests,
        cache_hits=cache_hits,
    )


def wait_for_completion(
    run_id: str,
    *,
    gateway_client=gateway,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PipelineReceipt | None:
    """Poll only the protected completion reader; return None at the deadline."""
    if not _valid_run_id(run_id):
        raise ValueError("run_id must be a canonical UUID")
    for value, name, lower, upper in (
        (timeout_seconds, "timeout_seconds", 0.0, MAX_TIMEOUT_SECONDS),
        (poll_seconds, "poll_seconds", 0.1, 30.0),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not lower <= float(value) <= upper
        ):
            raise ValueError(f"{name} is outside its approved bound")

    completion_id = completion_request_id(run_id)
    deadline = monotonic() + float(timeout_seconds)
    while True:
        try:
            response = gateway_client.call(
                "read_intelligence_completion",
                {},
                run_id=run_id,
                request_id=completion_id,
            )
        except gateway.GatewayError as error:
            if error.code not in RETRYABLE_GATEWAY_CODES or monotonic() >= deadline:
                raise
        else:
            data = response.get("data", response) if isinstance(response, Mapping) else None
            if not isinstance(data, Mapping):
                raise ValueError("invalid persisted completion response")
            saved = data.get("completion")
            if saved is not None:
                return _receipt_from_completion(
                    saved,
                    run_id=run_id,
                    completion_id=completion_id,
                )
            if monotonic() >= deadline:
                return None

        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        sleep(min(float(poll_seconds), remaining))


def _write_json(output: TextIO, value: dict[str, object]) -> None:
    output.write(_canonical(value) + "\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    try:
        args = parser.parse_args(argv)
        receipt = wait_for_completion(
            args.run_id,
            gateway_client=gateway,
            timeout_seconds=args.timeout_seconds,
        )
        if receipt is None:
            _write_json(output, {
                "error": "COLLECTION_PENDING",
                "ok": False,
                "run_id": args.run_id,
            })
            return 3
        output.write(receipt.to_json_bytes().decode("utf-8") + "\n")
        return 0
    except gateway.GatewayError as error:
        _write_json(output, {"error": error.code, "ok": False})
        return 1
    except (SystemExit, TypeError, ValueError):
        _write_json(output, {"error": "INVALID_COMPLETION_RECEIPT", "ok": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
