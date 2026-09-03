#!/usr/bin/env python3
"""Credential-proxy-safe client for the provider-neutral V2 agent gateway."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4


OPERATIONS = {
    "start_run",
    "read_bounded_context",
    "submit_analysis",
    "record_permitted_artifacts",
    "grade_due_decisions",
    "finish_run",
}
REQUIRED_SOURCE_HOSTS = {
    "query1.finance.yahoo.com",
    "www.sec.gov",
    "finnhub.io",
}
SOURCE_PROBES = {
    "query1.finance.yahoo.com": "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d",
    "www.sec.gov": "https://www.sec.gov/files/company_tickers.json",
    "finnhub.io": "https://finnhub.io/api/v1/quote?symbol=AAPL",
}
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_PROBE_BYTES = 64 * 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_RE = re.compile(r"^[a-z0-9-]{3,63}\.supabase\.co$")
STABLE_ERROR_CODES = {
    "INVALID_REQUEST", "UNAUTHORIZED", "RATE_LIMITED", "REQUEST_CONFLICT",
    "GATEWAY_UNAVAILABLE", "REQUEST_TOO_LARGE",
}


class ClientError(RuntimeError):
    """A stable error that deliberately carries no response or credential detail."""


def _uuid(value: str | None, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ClientError(f"INVALID_{field.upper()}") from error


def _gateway_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as error:
        raise ClientError("INVALID_GATEWAY_URL") from error
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.hostname
        or not HOST_RE.fullmatch(parsed.hostname)
        or parsed.path != "/functions/v1/agent-gateway"
        or parsed.query
        or parsed.fragment
    ):
        raise ClientError("INVALID_GATEWAY_URL")
    return urllib.parse.urlunsplit(("https", parsed.hostname, parsed.path, "", ""))


def _bounded_read(response: Any, limit: int) -> bytes:
    declared = response.headers.get("content-length") if response.headers else None
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        raise ClientError("RESPONSE_TOO_LARGE")
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ClientError("RESPONSE_TOO_LARGE")
    return body


def _decode_gateway(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClientError("GATEWAY_UNAVAILABLE") from error
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise ClientError("GATEWAY_UNAVAILABLE")
    if value["ok"] is True:
        if not isinstance(value.get("data"), dict):
            raise ClientError("GATEWAY_UNAVAILABLE")
        return value["data"]
    error = value.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    raise ClientError(code if code in STABLE_ERROR_CODES else "GATEWAY_UNAVAILABLE")


def call_gateway(
    gateway_url: str,
    operation: str,
    payload: Mapping[str, Any],
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    dry_run: bool = False,
    timeout: float = 30,
    _opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    url = _gateway_url(gateway_url)
    if operation not in OPERATIONS:
        raise ClientError("INVALID_OPERATION")
    if not isinstance(payload, Mapping):
        raise ClientError("INVALID_PAYLOAD")
    canonical_request_id = _uuid(request_id or str(uuid4()), "request_id")
    if operation == "start_run":
        if run_id is not None:
            raise ClientError("INVALID_RUN_ID")
        canonical_run_id = None
    else:
        canonical_run_id = _uuid(run_id, "run_id")
    envelope = {
        "contract_version": 2,
        "operation": operation,
        "request_id": canonical_request_id,
        "run_id": canonical_run_id,
        "dry_run": bool(dry_run),
        "payload": dict(payload),
    }
    encoded = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ClientError("REQUEST_TOO_LARGE")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener(request, timeout=timeout) as response:
            return _decode_gateway(_bounded_read(response, MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as error:
        try:
            return _decode_gateway(_bounded_read(error, MAX_RESPONSE_BYTES))
        except ClientError:
            raise
        except Exception as nested:
            raise ClientError("GATEWAY_UNAVAILABLE") from nested
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        raise ClientError("GATEWAY_UNAVAILABLE") from error


def probe_sources(
    *,
    timeout: float = 15,
    _opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for host in sorted(REQUIRED_SOURCE_HOSTS):
        request = urllib.request.Request(
            SOURCE_PROBES[host],
            method="GET",
            headers={"User-Agent": "stock-agent-connection-check/1.0"},
        )
        status = "reachable"
        content_hash: str | None = None
        try:
            with _opener(request, timeout=timeout) as response:
                body = _bounded_read(response, MAX_PROBE_BYTES)
                content_hash = hashlib.sha256(
                    f"{getattr(response, 'status', 200)}:".encode() + body
                ).hexdigest()
        except urllib.error.HTTPError as error:
            try:
                body = _bounded_read(error, MAX_PROBE_BYTES)
                content_hash = hashlib.sha256(f"{error.code}:".encode() + body).hexdigest()
            except Exception:
                status = "unreachable"
        except (ClientError, urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            status = "unreachable"
        checks.append({
            "host": host,
            "status": status,
            "content_hash": content_hash if status == "reachable" else None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
    return checks


def perform_handshake(
    gateway_url: str,
    trigger_request_id: str,
    *,
    _call: Callable[..., dict[str, Any]] = call_gateway,
    _probe: Callable[[], list[dict[str, Any]]] = probe_sources,
) -> dict[str, Any]:
    trigger_id = _uuid(trigger_request_id, "trigger_request_id")
    started = _call(gateway_url, "start_run", {"trigger_request_id": trigger_id})
    run_id = _uuid(str(started.get("run_id")), "run_id")
    context = _call(gateway_url, "read_bounded_context", {}, run_id=run_id)
    return _complete_handshake(
        gateway_url, run_id, context, _call=_call, _probe=_probe
    )


def _complete_handshake(
    gateway_url: str,
    run_id: str,
    context: Mapping[str, Any],
    *,
    _call: Callable[..., dict[str, Any]],
    _probe: Callable[[], list[dict[str, Any]]],
) -> dict[str, Any]:
    if (
        context.get("handshake") is not True
        or context.get("contract_version") != 2
        or not isinstance(context.get("challenge"), str)
        or not HASH_RE.fullmatch(context["challenge"])
        or set(context.get("allowed_source_hosts") or []) != REQUIRED_SOURCE_HOSTS
        or context.get("holdings") != []
        or context.get("plans") != []
    ):
        raise ClientError("HANDSHAKE_CONTEXT_INVALID")
    source_checks = _probe()
    if (
        len(source_checks) != len(REQUIRED_SOURCE_HOSTS)
        or {check.get("host") for check in source_checks} != REQUIRED_SOURCE_HOSTS
        or any(check.get("status") != "reachable" for check in source_checks)
    ):
        raise ClientError("SOURCE_NETWORK_FAILED")
    finished = _call(
        gateway_url,
        "finish_run",
        {
            "contract_version": 2,
            "challenge": context["challenge"],
            "source_checks": source_checks,
        },
        run_id=run_id,
    )
    if finished.get("status") != "completed":
        raise ClientError("HANDSHAKE_INCOMPLETE")
    return {
        "status": "completed",
        "source_hosts": len(source_checks),
        "writes": 0,
        "notifications": 0,
    }


def perform_invocation(
    gateway_url: str,
    trigger_request_id: str,
    *,
    _call: Callable[..., dict[str, Any]] = call_gateway,
    _probe: Callable[[], list[dict[str, Any]]] = probe_sources,
) -> dict[str, Any]:
    trigger_id = _uuid(trigger_request_id, "trigger_request_id")
    started = _call(gateway_url, "start_run", {"trigger_request_id": trigger_id})
    run_id = _uuid(str(started.get("run_id")), "run_id")
    context = _call(gateway_url, "read_bounded_context", {}, run_id=run_id)
    if context.get("handshake") is True:
        receipt = _complete_handshake(
            gateway_url, run_id, context, _call=_call, _probe=_probe
        )
        return {"kind": "handshake", "receipt": receipt}
    return {"kind": "analysis", "run_id": run_id, "context": context}


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ClientError("REQUEST_TOO_LARGE")
    try:
        value = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClientError("INVALID_PAYLOAD") from error
    if not isinstance(value, dict):
        raise ClientError("INVALID_PAYLOAD")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=sorted(OPERATIONS | {"handshake", "invoke"}))
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--trigger-request-id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.operation in {"handshake", "invoke"}:
            if args.run_id or args.request_id or args.dry_run:
                raise ClientError("INVALID_LOCAL_INPUT")
            result = (
                perform_handshake(args.gateway_url, args.trigger_request_id)
                if args.operation == "handshake"
                else perform_invocation(args.gateway_url, args.trigger_request_id)
            )
        else:
            payload = _read_payload()
            result = call_gateway(
                args.gateway_url,
                args.operation,
                payload,
                run_id=args.run_id,
                request_id=args.request_id,
                dry_run=args.dry_run,
            )
        print(json.dumps({"ok": True, "data": result}, separators=(",", ":"), sort_keys=True))
        return 0
    except ClientError as error:
        print(json.dumps({"ok": False, "error": {"code": str(error)}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
