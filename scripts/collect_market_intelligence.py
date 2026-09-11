#!/usr/bin/env python3
"""Emit one bounded intelligence packet for the scheduled Analyst/Checker pass."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from uuid import UUID
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import config, gateway  # noqa: E402
from lib.config import load_settings  # noqa: E402
from lib.intelligence.cursors import SourceCursor, parse_time  # noqa: E402
from lib.intelligence.http import BoundedHttpClient  # noqa: E402
from lib.intelligence.pipeline import IntelligencePipeline, PHASES, PipelineRequest, protected_collection_context  # noqa: E402
from lib.intelligence.planner import build_discovery_plan, load_source_capabilities  # noqa: E402
from lib.intelligence.policy import load_intelligence_policy  # noqa: E402
from lib.intelligence.providers import build_adapter  # noqa: E402
from lib.intelligence.quota import QuotaSession  # noqa: E402
from lib.intelligence.universe import (  # noqa: E402
    MAX_REFERENCE_SECURITIES,
    ReferenceSnapshot,
    SecurityIdentity,
    build_reference_transfer,
    merge_reference_snapshot,
    reference_snapshot_from_rows,
    refresh_sec_reference,
)


MAX_REFERENCE_TRANSFER_CALLS = 384
MAX_REFERENCE_TRANSFER_BYTES = 48 * 1024 * 1024
MAX_REFERENCE_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 512
# The SEC universe currently needs roughly 55 sequential gateway calls. Cloud
# round trips can exceed 90 seconds even when every bounded request succeeds.
MAX_REFERENCE_TRANSFER_SECONDS = 300.0
_REFERENCE_CAPABILITY = "sec_company_tickers_universe"


def _reference_request_id(
    run_id: str,
    operation: str,
    payload: dict[str, object],
) -> str:
    """Bind a restart-safe request id to the exact reference operation payload."""
    identity = json.dumps(
        {"operation": operation, "payload": payload},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return str(uuid.uuid5(
        uuid.UUID(run_id),
        f"reference-transfer:{hashlib.sha256(identity).hexdigest()}",
    ))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid argument")


def _diagnostic(error: BaseException) -> str:
    detail = f"{type(error).__name__}: {error}"
    detail = re.sub(r"https?://\S+", "[redacted-url]", detail, flags=re.IGNORECASE)
    detail = re.sub(
        r"\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        detail,
        flags=re.IGNORECASE,
    )
    detail = re.sub(r"\s+", " ", detail).strip()
    encoded = detail.encode("utf-8")
    if len(encoded) <= MAX_DIAGNOSTIC_BYTES:
        return detail
    return encoded[: MAX_DIAGNOSTIC_BYTES - 3].decode(
        "utf-8", errors="ignore"
    ).rstrip() + "..."


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


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
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
            discovery_supported = callable(getattr(gateway, "call", None)) or (
                callable(getattr(gateway, "read_discovery_context", None))
                and callable(getattr(gateway, "checkpoint_discovery_stage", None))
            )
            planned = {
                "discovery_plan": _build_capability_plan(policy, context, request),
                "source_cursors": _source_cursors(context),
            } if discovery_supported else {}
            installed_reference: dict[str, ReferenceSnapshot] = {}

            def persist_reference(run_id, pipeline_request):
                return _persist_reference_stage(
                    gateway, run_id, pipeline_request.now,
                    snapshot_sink=lambda snapshot: installed_reference.__setitem__(run_id, snapshot),
                )

            def recover_reference(run_id, pipeline_request):
                return _recover_reference_stage(
                    gateway,
                    run_id,
                    pipeline_request.now,
                    snapshot_sink=lambda snapshot: installed_reference.__setitem__(
                        run_id, snapshot
                    ),
                )

            def hydrate_reference(run_id):
                return installed_reference.get(run_id) or _read_current_reference_snapshot(
                    gateway, run_id,
                )

            pipeline = IntelligencePipeline(
                gateway, _adapters(policy, now), context=context, packet_limits=policy.packet,
                **planned,
                reference_stage=persist_reference
                if callable(getattr(gateway, "call", None)) else None,
                reference_recovery_stage=recover_reference
                if callable(getattr(gateway, "call", None)) else None,
                reference_snapshot_loader=hydrate_reference
                if callable(getattr(gateway, "call", None)) else None,
            )
        result = pipeline.run(request)
        output.write(result.to_json_bytes().decode("utf-8") + "\n")
        return 0
    except (SystemExit, TypeError, ValueError) as error:
        errors.write(_diagnostic(error) + "\n")
        output.write(json.dumps({"error": "INVALID_ARGUMENT", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 2
    except Exception as error:
        errors.write(_diagnostic(error) + "\n")
        output.write(json.dumps({"error": "COLLECTION_FAILED", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 1


def _read_context(run_id: str):
    return gateway.call("read_intelligence_context", {}, run_id=run_id)


def _source_cursors(context: dict[str, object]) -> dict[str, SourceCursor]:
    rows = context.get("source_cursors", [])
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("protected source cursors are invalid")
    result: dict[str, SourceCursor] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("protected source cursor is invalid")
        task_key = row.get("task_key")
        if not isinstance(task_key, str) or not task_key or len(task_key) > 200:
            raise ValueError("protected source cursor key is invalid")
        cursor_keys = {
            "provider", "capability_id", "completed_through", "active_window_start",
            "active_window_end", "backlog_token", "page", "accepted_item_ids",
            "next_retry_phase", "continuation_token_history",
        }
        legacy_cursor_keys = cursor_keys - {"continuation_token_history"}
        provenance_keys = {"source_run_id", "source_task_id", "source_updated_at"}
        if set(row) not in (
            {"task_key", *cursor_keys, *provenance_keys},
            {"task_key", *legacy_cursor_keys, *provenance_keys},
        ):
            raise ValueError("protected source cursor provenance is invalid")
        try:
            if not all(
                isinstance(row[key], str) and str(UUID(row[key])) == row[key]
                for key in ("source_run_id", "source_task_id")
            ):
                raise ValueError
            updated_at = datetime.fromisoformat(str(row["source_updated_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError("protected source cursor provenance is invalid") from None
        if updated_at.tzinfo is None or updated_at > datetime.now(timezone.utc):
            raise ValueError("protected source cursor provenance is invalid")
        value = {key: row[key] for key in cursor_keys if key in row}
        cursor = SourceCursor.from_mapping(value)
        current = datetime.now(timezone.utc)
        if any(timestamp is not None and timestamp > current for timestamp in (
            cursor.completed_through, cursor.active_window_start, cursor.active_window_end,
        )):
            raise ValueError("protected source cursor timestamp is in the future")
        theme_id = task_key.rsplit(":", 1)[-1]
        if (
            task_key != f"{cursor.capability_id}:{theme_id}"
            or re.fullmatch(r"(?:default|[a-z][a-z0-9_]{2,79})", theme_id) is None
        ):
            raise ValueError("protected source cursor key is invalid")
        if task_key in result:
            raise ValueError("protected source cursor key is duplicated")
        result[task_key] = cursor
    return result


def _last_completed_scans(
    context: dict[str, object], cursors: dict[str, SourceCursor]
) -> dict[tuple[str, str], str]:
    rows = context.get("last_completed_scans", [])
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("protected completed scans are invalid")
    result: dict[tuple[str, str], str] = {}
    cursor_rows = context.get("source_cursors", [])
    provenance = {
        row["task_key"]: (row["source_run_id"], row["source_task_id"])
        for row in cursor_rows if isinstance(row, dict)
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "capability_id", "theme_id", "completed_through",
            "source_run_id", "source_task_id",
        }:
            raise ValueError("protected completed scan is invalid")
        capability_id = row["capability_id"]
        theme_id = row["theme_id"]
        completed = row["completed_through"]
        if not all(isinstance(value, str) and value for value in (
            capability_id, theme_id, completed, row["source_run_id"], row["source_task_id"],
        )):
            raise ValueError("protected completed scan is invalid")
        task_key = f"{capability_id}:{theme_id}"
        cursor = cursors.get(task_key)
        if cursor is None or cursor.completed_through is None \
                or cursor.completed_through != parse_time(completed) \
                or provenance.get(task_key) != (row["source_run_id"], row["source_task_id"]):
            raise ValueError("protected completed scan does not match its source cursor")
        scan_key = (capability_id, theme_id)
        if scan_key in result:
            raise ValueError("protected completed scan is duplicated")
        result[scan_key] = completed
    for task_key, cursor in cursors.items():
        if cursor.completed_through is None:
            continue
        prefix = f"{cursor.capability_id}:"
        if task_key.startswith(prefix):
            result.setdefault(
                (cursor.capability_id, task_key[len(prefix):]),
                cursor.completed_through.isoformat(),
            )
    return result


def _build_capability_plan(policy, context: dict[str, object], request: PipelineRequest):
    capabilities = load_source_capabilities()
    available_credentials = frozenset(
        capability.required_credential
        for capability in capabilities.values()
        if capability.required_credential is not None
        and config.optional_secret(capability.required_credential.lower())
    )
    cursors = _source_cursors(context)
    reference_version = context.get("reference_version")
    if not isinstance(reference_version, str) or not reference_version:
        reference_version = "sec:unresolved"
    duration = {
        "pre-market": timedelta(hours=16),
        "intraday": timedelta(hours=6),
        "post-market": timedelta(hours=12),
        "on-demand": timedelta(days=2),
    }[request.phase]
    return build_discovery_plan(
        policy,
        capabilities,
        phase=request.phase,
        run_id=request.request_id,
        reference_version=reference_version,
        requested_window={
            "start": (request.now - duration).astimezone(timezone.utc).isoformat(),
            "end": request.now.astimezone(timezone.utc).isoformat(),
        },
        available_credentials=available_credentials,
        required_holding_quote_requests=0,
        last_completed_scans=_last_completed_scans(context, cursors),
    )


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
    sec_contact=None,
    snapshot_sink=None,
) -> dict[str, object]:
    """Refresh and bind one complete SEC snapshot inside explicit aggregate bounds."""
    http = client or BoundedHttpClient(allowed_hosts={"www.sec.gov"}, clock=lambda: now)
    started = monotonic()
    call_count = 0
    byte_count = 0
    response_byte_count = 0

    def invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal call_count, byte_count, response_byte_count
        request_id = _reference_request_id(run_id, operation, payload)
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
        response_bytes = len(json.dumps(
            result, allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode())
        response_byte_count += response_bytes
        if response_byte_count > MAX_REFERENCE_RESPONSE_BYTES:
            raise ValueError("reference response exceeds aggregate bound")
        if monotonic() - started > MAX_REFERENCE_TRANSFER_SECONDS:
            raise ValueError("reference transfer exceeds aggregate bound")
        data = result.get("data", result) if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise ValueError("reference gateway receipt is invalid")
        return data

    refreshed = refresh_sec_reference(
        http,
        contact=(
            sec_contact
            if sec_contact is not None
            else config.optional_secret("sec_user_agent_contact")
        ),
    )
    reference_as_of = now.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    reuse_predecessor_pin = False
    try:
        predecessor_pin = invoke("pin_discovery_reference", {
            "capability_id": _REFERENCE_CAPABILITY,
            "binding_role": "predecessor",
            "manifest_id": None,
            "reference_status": "reference_stale",
            "reference_as_of": reference_as_of,
        })
    except gateway.GatewayError as error:
        if error.code != "PERSISTENCE_FAILED":
            raise
        try:
            coverage, recovered_snapshot = _read_current_reference_binding(
                gateway_client,
                run_id,
                monotonic=monotonic,
            )
        except gateway.GatewayError as current_error:
            if current_error.code != "PERSISTENCE_FAILED":
                raise
            # The interrupted run may have persisted only its predecessor pin.
            # Reuse that immutable server-side pin instead of trying to replace
            # it with a later reference_as_of value.
            reuse_predecessor_pin = True
            predecessor_pin = None
        else:
            if recovered_snapshot is not None and snapshot_sink is not None:
                snapshot_sink(recovered_snapshot)
            return coverage
    if predecessor_pin is not None \
            and predecessor_pin.get("binding_role") != "predecessor":
        raise ValueError("reference predecessor pin receipt is invalid")
    predecessor_manifest_id = (
        predecessor_pin.get("manifest_id")
        if predecessor_pin is not None else None
    )
    predecessor_snapshot: ReferenceSnapshot | None = None
    predecessor_manifest: dict[str, object] | None = None
    if reuse_predecessor_pin or predecessor_manifest_id is not None:
        if not isinstance(predecessor_manifest_id, str):
            if not reuse_predecessor_pin:
                raise ValueError("reference predecessor pin receipt is invalid")
        after = None
        predecessor_rows: list[dict[str, object]] = []
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
                or not isinstance(rows, list)
            ):
                raise ValueError("reference predecessor page is invalid")
            if reuse_predecessor_pin and after is None:
                predecessor_manifest_id = binding.get("manifest_id")
                if predecessor_manifest_id is None:
                    if (
                        binding.get("reference_status") != "reference_unavailable"
                        or reference.get("manifest") is not None
                        or rows
                        or reference.get("complete") is not True
                        or reference.get("next_after_security_id") is not None
                    ):
                        raise ValueError("reference predecessor page is invalid")
                    break
                if not isinstance(predecessor_manifest_id, str):
                    raise ValueError("reference predecessor page is invalid")
            if binding.get("manifest_id") != predecessor_manifest_id:
                raise ValueError("reference predecessor page is invalid")
            manifest = reference.get("manifest")
            if not isinstance(manifest, dict):
                raise ValueError("reference predecessor page is invalid")
            _validate_reference_name_availability(binding, manifest)
            if manifest.get("id") != predecessor_manifest_id:
                raise ValueError("reference predecessor page is invalid")
            if predecessor_manifest is None:
                predecessor_manifest = manifest
            elif predecessor_manifest != manifest:
                raise ValueError("reference predecessor manifest changed while paging")
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError("reference predecessor security is invalid")
            predecessor_rows.extend(rows)
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
        if predecessor_manifest_id is not None:
            predecessor_snapshot = reference_snapshot_from_rows(
                predecessor_manifest, predecessor_rows
            )

    manifest_id = None
    selected_manifest_record: dict[str, object] | None = None
    if refreshed.status == "healthy" and refreshed.snapshot is not None:
        snapshot = (
            merge_reference_snapshot(refreshed.snapshot, predecessor_snapshot)
            if predecessor_snapshot is not None else refreshed.snapshot
        )
        transfer = build_reference_transfer(
            snapshot,
            run_id=run_id,
            capability_version=1,
            taxonomy_version=1,
            predecessor_manifest_id=predecessor_manifest_id,
            transfer_attempt_id=(
                str(uuid.uuid5(
                    UUID(run_id),
                    f"reference-recovery:{reference_as_of}",
                ))
                if reuse_predecessor_pin else None
            ),
            capability_id=_REFERENCE_CAPABILITY,
            semantic_encoding_version=2,
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
        selected_snapshot = snapshot
        selected_manifest_record = transfer.begin["manifest"]
    else:
        requested_status = "reference_stale"
        manifest_id = predecessor_manifest_id
        selected_snapshot = predecessor_snapshot
        selected_manifest_record = predecessor_manifest

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
    if selected_snapshot is not None and callable(snapshot_sink):
        snapshot_sink(selected_snapshot)
    revision = (
        selected_manifest_record.get("revision")
        if isinstance(selected_manifest_record, dict) else None
    )
    source_retrieved = (
        selected_manifest_record.get("manifest", {}).get("source_retrieved_at")
        if isinstance(selected_manifest_record, dict)
        and isinstance(selected_manifest_record.get("manifest"), dict) else None
    )
    reference_expires_at = None
    if isinstance(source_retrieved, str):
        reference_expires_at = (
            datetime.fromisoformat(source_retrieved.replace("Z", "+00:00"))
            + timedelta(hours=24)
        ).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    coverage = {
        "coverage_status": "scope_not_guaranteed",
        "reference_status": status,
        "reference_manifest_id": pinned_manifest,
        "reference_age_seconds": age,
        "execution_allowed": False,
    }
    if revision is not None and reference_expires_at is not None:
        coverage.update(
            reference_revision=revision,
            reference_expires_at=reference_expires_at,
        )
    return coverage


def _read_current_reference_binding(
    gateway_client,
    run_id: str,
    *,
    monotonic=time.monotonic,
) -> tuple[dict[str, object], ReferenceSnapshot | None]:
    """Read the durable current pin and its snapshot without contacting SEC."""
    started = monotonic()
    call_count = 0
    request_bytes = 0
    response_bytes = 0
    after = None
    manifest = None
    pinned_binding = None
    rows: list[dict[str, object]] = []
    while True:
        payload = {
            "capability_id": _REFERENCE_CAPABILITY,
            "binding_role": "current",
            "after_security_id": after,
            "limit": 500,
        }
        request_id = _reference_request_id(
            run_id, "read_discovery_reference", payload,
        )
        envelope = {
            "dry_run": False, "operation": "read_discovery_reference",
            "payload": payload, "request_id": request_id, "run_id": run_id,
            "schema_version": 1,
        }
        encoded = json.dumps(
            envelope, allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode()
        elapsed = monotonic() - started
        if call_count + 1 > MAX_REFERENCE_TRANSFER_CALLS \
                or request_bytes + len(encoded) > MAX_REFERENCE_TRANSFER_BYTES \
                or elapsed >= MAX_REFERENCE_TRANSFER_SECONDS:
            raise ValueError("reference hydration exceeds aggregate bound")
        result = gateway_client.call(
            "read_discovery_reference", payload, run_id=run_id,
            request_id=request_id,
            timeout=float(min(30.0, MAX_REFERENCE_TRANSFER_SECONDS - elapsed)),
        )
        call_count += 1
        request_bytes += len(encoded)
        response_bytes += len(json.dumps(
            result, allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode())
        if response_bytes > MAX_REFERENCE_RESPONSE_BYTES \
                or monotonic() - started > MAX_REFERENCE_TRANSFER_SECONDS:
            raise ValueError("reference hydration exceeds aggregate bound")
        data = result.get("data", result) if isinstance(result, dict) else None
        page = data.get("reference", data) if isinstance(data, dict) else None
        if not isinstance(page, dict):
            raise ValueError("current reference page is invalid")
        binding = page.get("binding")
        page_rows = page.get("securities")
        if not isinstance(binding, dict) or binding.get("binding_role") != "current" \
                or not isinstance(page_rows, list):
            raise ValueError("current reference page is invalid")
        binding_identity = {
            key: binding.get(key) for key in (
                "binding_role", "manifest_id", "reference_status",
                "source_retrieved_at", "reference_age_seconds",
            )
        }
        if pinned_binding is None:
            pinned_binding = binding_identity
        elif pinned_binding != binding_identity:
            raise ValueError("current reference binding changed while paging")
        status = binding.get("reference_status")
        manifest_id = binding.get("manifest_id")
        age = binding.get("reference_age_seconds")
        source_retrieved = binding.get("source_retrieved_at")
        if status == "reference_unavailable":
            if page.get("manifest") is not None or page_rows or page.get("complete") is not True:
                raise ValueError("current reference page is invalid")
            if manifest_id is not None or age is not None or source_retrieved is not None:
                raise ValueError("current reference page is invalid")
            return ({
                "coverage_status": "scope_not_guaranteed",
                "reference_status": status,
                "reference_manifest_id": None,
                "reference_age_seconds": None,
                "execution_allowed": False,
            }, None)
        if status not in {"healthy", "reference_stale"} \
                or not isinstance(manifest_id, str) \
                or isinstance(age, bool) or not isinstance(age, int) or age < 0 \
                or not isinstance(source_retrieved, str):
            raise ValueError("current reference page is invalid")
        page_manifest = page.get("manifest")
        if not isinstance(page_manifest, dict):
            raise ValueError("current reference page is invalid")
        _validate_reference_name_availability(binding, page_manifest)
        if page_manifest.get("id") != binding.get("manifest_id"):
            raise ValueError("current reference page is invalid")
        if manifest is None:
            manifest = page_manifest
        elif manifest != page_manifest:
            raise ValueError("current reference manifest changed while paging")
        if any(not isinstance(row, dict) for row in page_rows):
            raise ValueError("current reference security is invalid")
        rows.extend(page_rows)
        if len(rows) > MAX_REFERENCE_SECURITIES:
            raise ValueError("current reference exceeds item bound")
        complete = page.get("complete")
        next_after = page.get("next_after_security_id")
        if complete is True:
            if next_after is not None:
                raise ValueError("current reference page is invalid")
            try:
                expires_at = (
                    datetime.fromisoformat(source_retrieved.replace("Z", "+00:00"))
                    + timedelta(hours=24)
                ).astimezone(timezone.utc).isoformat(
                    timespec="milliseconds"
                ).replace("+00:00", "Z")
            except ValueError:
                raise ValueError("current reference page is invalid") from None
            revision = manifest.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise ValueError("current reference page is invalid")
            return ({
                "coverage_status": "scope_not_guaranteed",
                "reference_status": status,
                "reference_manifest_id": manifest_id,
                "reference_age_seconds": age,
                "reference_revision": revision,
                "reference_expires_at": expires_at,
                "execution_allowed": False,
            }, reference_snapshot_from_rows(manifest, rows))
        if complete is not False or not isinstance(next_after, str) or next_after == after:
            raise ValueError("current reference page is invalid")
        after = next_after


def _read_current_reference_snapshot(
    gateway_client,
    run_id: str,
    *,
    monotonic=time.monotonic,
) -> ReferenceSnapshot | None:
    """Hydrate the already-pinned snapshot on restart without an SEC request."""
    return _read_current_reference_binding(
        gateway_client, run_id, monotonic=monotonic,
    )[1]


def _recover_reference_stage(
    gateway_client,
    run_id: str,
    now: datetime,
    *,
    client=None,
    monotonic=time.monotonic,
    sec_contact=None,
    snapshot_sink=None,
) -> dict[str, object]:
    """Recover a current pin or rebuild once when no current pin was created."""
    try:
        coverage, snapshot = _read_current_reference_binding(
            gateway_client, run_id, monotonic=monotonic,
        )
    except gateway.GatewayError as error:
        if error.code != "PERSISTENCE_FAILED":
            raise
        return _persist_reference_stage(
            gateway_client,
            run_id,
            now,
            client=client,
            monotonic=monotonic,
            sec_contact=sec_contact,
            snapshot_sink=snapshot_sink,
        )
    if snapshot is not None and callable(snapshot_sink):
        snapshot_sink(snapshot)
    return coverage


def _validate_reference_name_availability(
    binding: dict[str, object], manifest: dict[str, object]
) -> None:
    metadata = manifest.get("manifest")
    if not isinstance(metadata, dict):
        raise ValueError("reference manifest metadata is invalid")
    version = metadata.get("format_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
        raise ValueError("reference manifest format is invalid")
    expected = "available" if version == 2 else "issuer_names_unavailable"
    if binding.get("issuer_names_status") != expected:
        raise ValueError("reference issuer name availability is inconsistent")


if __name__ == "__main__":
    raise SystemExit(main())
