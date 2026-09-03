"""Bounded, scoped client for the market-briefing Edge Function."""

from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from lib import config


OPERATIONS = (
    "start_run",
    "read_context",
    "record_artifacts",
    "grade_due_decisions",
    "evaluate_and_publish",
    "evaluate_alert_rules",
    "finish_run",
)
MAX_REQUEST_BYTES = 262_144
MAX_RESPONSE_BYTES = 1_048_576
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
ERROR_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,63}")


class GatewayError(RuntimeError):
    """A gateway failure represented only by a stable, non-sensitive code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _valid_uuid(value: str) -> bool:
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _read_response(response: Any) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_RESPONSE_BYTES or int(declared) < 0:
                raise GatewayError("RESPONSE_TOO_LARGE")
        except ValueError:
            raise GatewayError("INVALID_GATEWAY_RESPONSE") from None
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise GatewayError("RESPONSE_TOO_LARGE")
    return body


def _parse_response(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError("INVALID_GATEWAY_RESPONSE") from None
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        raise GatewayError("INVALID_GATEWAY_RESPONSE")
    if result["ok"] is True:
        if not isinstance(result.get("data"), dict):
            raise GatewayError("INVALID_GATEWAY_RESPONSE")
        return result
    code = result.get("code")
    if not isinstance(code, str) or not ERROR_CODE_PATTERN.fullmatch(code):
        raise GatewayError("INVALID_GATEWAY_RESPONSE")
    raise GatewayError(code)


def call(
    operation: str,
    payload: Any,
    *,
    run_id: str | None = None,
    dry_run: bool = False,
    request_id: str | None = None,
    timeout: int = 30,
    _opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Call one allow-listed operation without exposing broad Supabase credentials."""
    if operation not in OPERATIONS:
        raise ValueError("unknown gateway operation")
    if request_id is None:
        request_id = str(uuid.uuid4())
    if not _valid_uuid(request_id):
        raise ValueError("request_id must be a canonical UUID")
    if operation in {"start_run", "evaluate_alert_rules"}:
        if run_id is not None:
            raise ValueError(f"{operation} does not accept run_id")
    elif not _valid_uuid(run_id):
        raise ValueError("run_id must be a canonical UUID")
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be boolean")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
        raise ValueError("timeout must be between 1 and 60 seconds")

    envelope = {
        "dry_run": dry_run,
        "operation": operation,
        "payload": payload,
        "request_id": request_id,
        "run_id": run_id,
        "schema_version": 1,
    }
    try:
        encoded = json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("payload must be valid JSON") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("request too large")

    try:
        base_url = config.secret("supabase_url").rstrip("/")
        scoped_secret = config.optional_secret("market_agent_secret")
    except Exception:
        raise GatewayError("GATEWAY_CONFIGURATION_MISSING") from None
    endpoint = base_url + "/functions/v1/market-briefing-gateway"
    parsed = urllib.parse.urlparse(endpoint)
    if _opener is None and (parsed.scheme != "https" or not parsed.netloc):
        raise ValueError("gateway URL must use HTTPS")

    headers = {"Content-Type": "application/json"}
    if scoped_secret:
        headers["X-Market-Agent-Secret"] = scoped_secret

    request = urllib.request.Request(
        endpoint,
        data=encoded,
        method="POST",
        headers=headers,
    )
    opener = _opener or urllib.request.urlopen
    try:
        if _opener is None:
            opened = opener(request, timeout=timeout, context=ssl.create_default_context())
        else:
            opened = opener(request, timeout=timeout)
        with opened as response:
            raw = _read_response(response)
    except GatewayError:
        raise
    except Exception:
        raise GatewayError("GATEWAY_UNAVAILABLE") from None
    return _parse_response(raw)
