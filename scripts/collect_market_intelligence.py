#!/usr/bin/env python3
"""Emit one bounded intelligence packet for the scheduled Analyst/Checker pass."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import gateway  # noqa: E402
from lib.config import load_settings  # noqa: E402
from lib.intelligence.http import BoundedHttpClient  # noqa: E402
from lib.intelligence.pipeline import IntelligencePipeline, PHASES, PipelineRequest, protected_collection_context  # noqa: E402
from lib.intelligence.policy import load_intelligence_policy  # noqa: E402
from lib.intelligence.providers import build_adapter  # noqa: E402
from lib.intelligence.quota import QuotaSession  # noqa: E402
from lib.intelligence.universe import (  # noqa: E402
    MAX_REFERENCE_SECURITIES,
    SecurityIdentity,
    build_reference_transfer,
    merge_reference_snapshot,
    refresh_sec_reference,
)


MAX_REFERENCE_TRANSFER_CALLS = 160
MAX_REFERENCE_TRANSFER_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_TRANSFER_SECONDS = 45.0
_REFERENCE_CAPABILITY = "sec_company_tickers_universe"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid argument")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--market-date")
    parser.add_argument("--now")
    parser.add_argument("--context-file")
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("now must include a timezone")
    return parsed


def _market_date(value: str | None, now: datetime) -> date:
    return date.fromisoformat(value) if value else now.date()


def _adapters(policy, now: datetime):
    adapters = []
    empty_quota = QuotaSession({provider: () for provider in policy.providers})
    for provider in policy.providers:
        adapter = build_adapter(
            provider,
            BoundedHttpClient(allowed_hosts=_provider_hosts(provider)),
            empty_quota,
            clock=lambda: now,
        )
        adapters.append(adapter)
    return adapters


def _provider_hosts(provider: str) -> frozenset[str]:
    from lib.intelligence.providers.alpha_vantage import AlphaVantageAdapter
    from lib.intelligence.providers.finnhub import FinnhubAdapter
    from lib.intelligence.providers.gdelt import GdeltAdapter
    from lib.intelligence.providers.official import OFFICIAL_ADAPTERS
    from lib.intelligence.providers.yahoo import YahooAdapter

    types = {
        "gdelt": GdeltAdapter,
        "alpha_vantage": AlphaVantageAdapter,
        "finnhub": FinnhubAdapter,
        "yahoo": YahooAdapter,
        **OFFICIAL_ADAPTERS,
    }
    return types[provider].allowed_hosts


def _context(path: str | None) -> dict[str, object]:
    if path is None:
        return {}
    raw = Path(path).read_bytes()
    if len(raw) > 65_536:
        raise ValueError("context exceeds bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("context must be an object")
    allowed = {
        "holdings", "owner_plans", "qualified_candidates", "urgent_events",
        "high_materiality_themes", "requested_topics",
        "liquidity_by_ticker", "overlap_by_ticker",
    }
    forbidden = {"comparison_ids", "learning_inputs"} & set(value)
    if forbidden:
        raise ValueError("comparison and learning inputs require typed gateway operations")
    return {key: value[key] for key in sorted(value) if key in allowed}


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    output = stdout or sys.stdout
    try:
        args = _parser().parse_args(argv)
        if not args.dry_run and not args.run_id:
            raise ValueError("scheduled collection requires run id")
        now = _now(args.now)
        request = PipelineRequest(
            args.phase, _market_date(args.market_date, now), now, args.dry_run,
            args.run_id or "00000000-0000-4000-8000-000000000000",
        )
        context = _context(args.context_file)
        if args.dry_run:
            pipeline = IntelligencePipeline(object(), (), context=context)
        else:
            protected = _read_context(args.run_id)
            data = protected.get("data", protected)
            # --context-file is fixture/intent input only. The scheduled path
            # obtains holdings and every ranking value from the protected reader.
            context = protected_collection_context(data["context"])
            policy = load_intelligence_policy(load_settings())
            pipeline = IntelligencePipeline(
                gateway, _adapters(policy, now), context=context, packet_limits=policy.packet,
                reference_stage=(
                    lambda run_id, request: _persist_reference_stage(
                        gateway, run_id, request.now,
                    )
                ) if callable(getattr(gateway, "call", None)) else None,
            )
        result = pipeline.run(request)
        output.write(result.to_json_bytes().decode("utf-8") + "\n")
        return 0
    except (SystemExit, TypeError, ValueError):
        output.write(json.dumps({"error": "INVALID_ARGUMENT", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 2
    except Exception:
        output.write(json.dumps({"error": "COLLECTION_FAILED", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 1


def _read_context(run_id: str):
    return gateway.call("read_intelligence_context", {}, run_id=run_id)


def _security_from_reference_row(value: object) -> SecurityIdentity:
    if not isinstance(value, dict):
        raise ValueError("reference predecessor security is invalid")
    try:
        valid_from = date.fromisoformat(str(value["valid_from"])[:10])
        valid_to_value = value.get("valid_to")
        valid_to = (
            date.fromisoformat(str(valid_to_value)[:10])
            if valid_to_value is not None else None
        )
        aliases = value["aliases"]
        source_ids = value["source_ids"]
        exclusions = value["exclusion_reasons"]
        if not all(isinstance(items, list) and all(isinstance(item, str) for item in items)
                   for items in (aliases, source_ids, exclusions)):
            raise ValueError
        if not isinstance(value["eligible"], bool):
            raise ValueError
        row = SecurityIdentity(
            security_id=str(value["security_id"]),
            entity_id=str(value["entity_id"]),
            ticker=str(value["ticker"]),
            exchange=str(value["exchange"]) if value.get("exchange") is not None else None,
            instrument_type=str(value["instrument_type"]),
            valid_from=valid_from,
            valid_to=valid_to,
            aliases=tuple(aliases),
            source_ids=tuple(source_ids),
            eligible=value["eligible"],
            exclusion_reasons=tuple(exclusions),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("reference predecessor security is invalid") from None
    if not row.security_id or not row.entity_id or not row.ticker:
        raise ValueError("reference predecessor security is invalid")
    return row


def _persist_reference_stage(
    gateway_client,
    run_id: str,
    now: datetime,
    *,
    client=None,
    monotonic=time.monotonic,
) -> dict[str, object]:
    """Refresh and bind one complete SEC snapshot inside explicit aggregate bounds."""
    http = client or BoundedHttpClient(allowed_hosts={"www.sec.gov"}, clock=lambda: now)
    started = monotonic()
    call_count = 0
    byte_count = 0

    def invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal call_count, byte_count
        request_id = str(uuid.uuid5(uuid.UUID(run_id), f"reference-transfer:{call_count}:{operation}"))
        envelope = {
            "dry_run": False,
            "operation": operation,
            "payload": payload,
            "request_id": request_id,
            "run_id": run_id,
            "schema_version": 1,
        }
        encoded = json.dumps(
            envelope, allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode()
        if len(encoded) > gateway.MAX_REQUEST_BYTES:
            raise ValueError("reference transfer call exceeds gateway bound")
        elapsed = monotonic() - started
        if (
            call_count + 1 > MAX_REFERENCE_TRANSFER_CALLS
            or byte_count + len(encoded) > MAX_REFERENCE_TRANSFER_BYTES
            or elapsed >= MAX_REFERENCE_TRANSFER_SECONDS
        ):
            raise ValueError("reference transfer exceeds aggregate bound")
        remaining = MAX_REFERENCE_TRANSFER_SECONDS - elapsed
        result = gateway_client.call(
            operation, payload, run_id=run_id, request_id=request_id,
            timeout=float(min(30.0, remaining)),
        )
        call_count += 1
        byte_count += len(encoded)
        if monotonic() - started > MAX_REFERENCE_TRANSFER_SECONDS:
            raise ValueError("reference transfer exceeds aggregate bound")
        data = result.get("data", result) if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise ValueError("reference gateway receipt is invalid")
        return data

    refreshed = refresh_sec_reference(http)
    reference_as_of = now.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    predecessor_pin = invoke("pin_discovery_reference", {
        "capability_id": _REFERENCE_CAPABILITY,
        "binding_role": "predecessor",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": reference_as_of,
    })
    if predecessor_pin.get("binding_role") != "predecessor":
        raise ValueError("reference predecessor pin receipt is invalid")
    predecessor_manifest_id = predecessor_pin.get("manifest_id")
    predecessor_rows: list[SecurityIdentity] = []
    if predecessor_manifest_id is not None:
        if not isinstance(predecessor_manifest_id, str):
            raise ValueError("reference predecessor pin receipt is invalid")
        after = None
        while True:
            page = invoke("read_discovery_reference", {
                "capability_id": _REFERENCE_CAPABILITY,
                "binding_role": "predecessor",
                "after_security_id": after,
                "limit": 500,
            })
            reference = page.get("reference", page)
            if not isinstance(reference, dict):
                raise ValueError("reference predecessor page is invalid")
            binding = reference.get("binding")
            rows = reference.get("securities")
            if (
                not isinstance(binding, dict)
                or binding.get("binding_role") != "predecessor"
                or binding.get("manifest_id") != predecessor_manifest_id
                or not isinstance(rows, list)
            ):
                raise ValueError("reference predecessor page is invalid")
            predecessor_rows.extend(_security_from_reference_row(row) for row in rows)
            if len(predecessor_rows) > MAX_REFERENCE_SECURITIES:
                raise ValueError("reference predecessor exceeds item bound")
            complete = reference.get("complete")
            next_after = reference.get("next_after_security_id")
            if complete is True:
                if next_after is not None:
                    raise ValueError("reference predecessor page is invalid")
                break
            if complete is not False or not isinstance(next_after, str) or next_after == after:
                raise ValueError("reference predecessor page is invalid")
            after = next_after

    manifest_id = None
    if refreshed.status == "healthy" and refreshed.snapshot is not None:
        snapshot = (
            merge_reference_snapshot(refreshed.snapshot, predecessor_rows)
            if predecessor_rows else refreshed.snapshot
        )
        transfer = build_reference_transfer(
            snapshot,
            run_id=run_id,
            capability_version=1,
            taxonomy_version=1,
            predecessor_manifest_id=predecessor_manifest_id,
            capability_id=_REFERENCE_CAPABILITY,
        )
        begin = invoke("begin_discovery_reference", transfer.begin)
        manifest_id = str(begin.get("manifest_id") or "")
        if manifest_id != transfer.begin["manifest"]["id"]:
            raise ValueError("reference begin receipt is invalid")
        for chunk in transfer.chunks:
            receipt = invoke("record_discovery_reference_chunk", chunk)
            if receipt.get("manifest_id") != manifest_id or receipt.get("chunk_index") != chunk["chunk_index"]:
                raise ValueError("reference chunk receipt is invalid")
        finalized = invoke("finalize_discovery_reference", {
            "manifest_id": manifest_id,
            "root_hash": transfer.begin["root_hash"],
        })
        if finalized.get("manifest_id") != manifest_id or finalized.get("security_count") != len(snapshot.securities):
            raise ValueError("reference finalization receipt is invalid")
        requested_status = "healthy"
    else:
        requested_status = "reference_stale"
        manifest_id = predecessor_manifest_id

    pinned = invoke("pin_discovery_reference", {
        "capability_id": _REFERENCE_CAPABILITY,
        "binding_role": "current",
        "manifest_id": manifest_id,
        "reference_status": requested_status,
        "reference_as_of": reference_as_of,
    })
    if pinned.get("binding_role") != "current":
        raise ValueError("reference pin receipt is invalid")
    status = pinned.get("reference_status")
    pinned_manifest = pinned.get("manifest_id")
    age = pinned.get("reference_age_seconds")
    if status not in {"healthy", "reference_stale", "reference_unavailable"}:
        raise ValueError("reference pin receipt is invalid")
    if status == "reference_unavailable":
        if pinned_manifest is not None or age is not None:
            raise ValueError("reference pin receipt is invalid")
    elif not isinstance(pinned_manifest, str) or isinstance(age, bool) or not isinstance(age, int) or age < 0:
        raise ValueError("reference pin receipt is invalid")
    return {
        "coverage_status": "scope_not_guaranteed",
        "reference_status": status,
        "reference_manifest_id": pinned_manifest,
        "reference_age_seconds": age,
        "execution_allowed": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
