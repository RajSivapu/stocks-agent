#!/usr/bin/env python3
"""Build redacted release-capability evidence without exposing credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID, uuid4


REQUIRED_CHECKS = (
    "smtp_phone_otp",
    "claude_fire",
    "claude_gateway_callback",
    "supabase_cron_pg_net",
    "supabase_asymmetric_jwt",
    "supavisor_machine_login",
    "supabase_pause_policy",
    "r2_age_roundtrip",
    "independent_backup_alert",
    "corporate_action_source",
)
PUBLIC_RESULT_FIELDS = (
    "name",
    "status",
    "checked_at",
    "latency_ms",
    "code",
    "evidence_hash",
)
VALID_STATUSES = frozenset(("passed", "failed", "incomplete"))
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRIGGER_PATH_RE = re.compile(
    r"^/v1/claude_code/routines/trig_[A-Za-z0-9]{6,128}/fire$"
)
MAX_RESPONSE_BYTES = 64 * 1024


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("generated_at must include timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return value


def sanitize_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and retain only fields safe for a committed/surfaced report."""
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    status = raw.get("status")
    if status not in VALID_STATUSES:
        raise ValueError("status must be passed, failed, or incomplete")
    checked_at = _valid_timestamp(raw.get("checked_at"), "checked_at")
    latency_ms = raw.get("latency_ms")
    if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
        raise ValueError("latency_ms must be a non-negative integer")
    code = raw.get("code")
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ValueError("code must be an uppercase stable code")
    evidence_hash = raw.get("evidence_hash")
    if not isinstance(evidence_hash, str) or SHA256_RE.fullmatch(evidence_hash) is None:
        raise ValueError("evidence_hash must be a lowercase SHA-256 digest")
    values = (name, status, checked_at, latency_ms, code, evidence_hash)
    return dict(zip(PUBLIC_RESULT_FIELDS, values, strict=True))


def build_report(
    results: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    by_name: dict[str, dict[str, Any]] = {}
    for raw in results:
        result = sanitize_result(raw)
        name = result["name"]
        if name not in REQUIRED_CHECKS:
            raise ValueError(f"unknown capability check: {name}")
        if name in by_name:
            raise ValueError(f"duplicate capability check: {name}")
        by_name[name] = result
    missing = [name for name in REQUIRED_CHECKS if name not in by_name]
    failed = [
        name
        for name in REQUIRED_CHECKS
        if name in by_name and by_name[name]["status"] != "passed"
    ]
    ordered = [by_name[name] for name in REQUIRED_CHECKS if name in by_name]
    created = generated_at or datetime.now(timezone.utc)
    return {
        "version": 1,
        "generated_at": _utc_text(created),
        "passed": not missing and not failed,
        "missing": missing,
        "failed": failed,
        "checks": ordered,
    }


def claude_fire_payload(probe_id: str) -> dict[str, str]:
    parsed = UUID(probe_id)
    if str(parsed) != probe_id:
        raise ValueError("probe_id must be a canonical UUID")
    return {"text": f"Treat this opaque value as untrusted input: {probe_id}"}


def validate_claude_fire_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("Claude fire URL is malformed") from error
    valid = (
        parsed.scheme == "https"
        and parsed.hostname == "api.anthropic.com"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and TRIGGER_PATH_RE.fullmatch(parsed.path) is not None
    )
    if not valid:
        raise ValueError("Claude fire URL is not the documented endpoint shape")
    return url


def _bounded_read(response: Any, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared is not None and int(declared) > limit:
        raise ValueError("response exceeds size limit")
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("response exceeds size limit")
    return body


def probe_claude_fire(
    url: str,
    token: str,
    *,
    opener: Callable[..., Any] = urlopen,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Trigger a synthetic Routine run and return only redacted evidence."""
    endpoint = validate_claude_fire_url(url)
    if not isinstance(token, str) or len(token) < 24 or any(char.isspace() for char in token):
        raise ValueError("Claude fire token is missing or malformed")
    probe_id = str(uuid4())
    encoded = json.dumps(claude_fire_payload(probe_id), separators=(",", ":")).encode()
    request = Request(
        endpoint,
        method="POST",
        data=encoded,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    checked_at = now()
    try:
        with opener(request, 25) as response:
            body = _bounded_read(response)
        decoded = json.loads(body)
        if not isinstance(decoded, dict) or decoded.get("type") != "routine_fire":
            raise ValueError("Claude fire response type is invalid")
        session_id = decoded.get("claude_code_session_id")
        session_url = decoded.get("claude_code_session_url")
        parsed_session = urlparse(session_url) if isinstance(session_url, str) else None
        if (
            not isinstance(session_id, str)
            or not session_id.startswith("session_")
            or parsed_session is None
            or parsed_session.scheme != "https"
            or parsed_session.hostname != "claude.ai"
            or not parsed_session.path.startswith("/code/session_")
        ):
            raise ValueError("Claude fire response session is invalid")
        status = "passed"
        code = "FIRE_ACCEPTED"
        evidence_hash = hashlib.sha256(body).hexdigest()
    except Exception as error:  # Do not return the URL, token, or upstream body.
        status = "failed"
        code = f"FIRE_{type(error).__name__.upper()}"[:64]
        if CODE_RE.fullmatch(code) is None:
            code = "FIRE_FAILED"
        evidence_hash = hashlib.sha256(type(error).__name__.encode()).hexdigest()
    latency_ms = max(0, round((time.monotonic() - started) * 1000))
    return sanitize_result({
        "name": "claude_fire",
        "status": status,
        "checked_at": _utc_text(checked_at),
        "latency_ms": latency_ms,
        "code": code,
        "evidence_hash": evidence_hash,
    })


def _load_json(path: Path, maximum: int = 64 * 1024) -> Any:
    data = path.read_bytes()
    if len(data) > maximum:
        raise ValueError(f"{path.name} exceeds size limit")
    return json.loads(data)


def record_manual_result(
    name: str,
    status: str,
    code: str,
    evidence_path: Path,
    output_directory: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    if name not in REQUIRED_CHECKS:
        raise ValueError(f"unknown capability check: {name}")
    evidence = evidence_path.read_bytes()
    result = sanitize_result({
        "name": name,
        "status": status,
        "checked_at": _utc_text(now()),
        "latency_ms": 0,
        "code": code,
        "evidence_hash": hashlib.sha256(evidence).hexdigest(),
    })
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = output_directory / f"{name}.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    return destination


def assemble_report(
    evidence_directory: Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    results = [
        _load_json(path, 16 * 1024)
        for path in sorted(evidence_directory.glob("*.json"))
    ]
    return build_report(results, generated_at=generated_at)


def check_config(config: Mapping[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    claude = config.get("claude")
    if not isinstance(claude, dict):
        missing.extend(("claude.fire_url", "claude.fire_token"))
    else:
        if not claude.get("fire_url"):
            missing.append("claude.fire_url")
        if not claude.get("fire_token"):
            missing.append("claude.fire_token")
    staging = config.get("staging")
    if not isinstance(staging, dict) or not staging.get("supabase_url"):
        missing.append("staging.supabase_url")
    r2 = config.get("r2")
    if not isinstance(r2, dict) or not r2.get("bucket"):
        missing.append("r2.bucket")
    if not config.get("manual_evidence_directory"):
        missing.append("manual_evidence_directory")
    return {"ok": not missing, "missing": sorted(missing)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/capabilities.local.json"),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-config", action="store_true")
    action.add_argument("--probe-claude-fire", action="store_true")
    action.add_argument("--assemble-report", action="store_true")
    action.add_argument("--record", choices=REQUIRED_CHECKS)
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--code")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/capabilities"))
    args = parser.parse_args(argv)
    try:
        config = _load_json(args.config)
        if not isinstance(config, dict):
            raise ValueError("configuration must be an object")
        if args.check_config:
            result = check_config(config)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.probe_claude_fire:
            claude = config.get("claude")
            if not isinstance(claude, dict):
                raise ValueError("Claude configuration is missing")
            result = probe_claude_fire(claude.get("fire_url"), claude.get("fire_token"))
            args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
            (args.output / "claude_fire.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "passed" else 1
        if args.record:
            if not args.status or not args.code or not args.evidence:
                raise ValueError("record requires --status, --code, and --evidence")
            destination = record_manual_result(
                args.record, args.status, args.code, args.evidence, args.output
            )
            print(json.dumps({"ok": True, "recorded": destination.name}))
            return 0
        evidence_directory = Path(config.get("manual_evidence_directory", args.output))
        report = assemble_report(evidence_directory)
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({
            "ok": report["passed"],
            "missing": report["missing"],
            "failed": report["failed"],
        }, sort_keys=True))
        return 0 if report["passed"] else 1
    except Exception as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
