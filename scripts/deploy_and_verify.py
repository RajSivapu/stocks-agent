#!/usr/bin/env python3
"""Fail-closed staging/production evidence checks and static-build verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_claude_connection import evaluate_connection, read_connection


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
MAX_HTTP_BYTES = 1024 * 1024
STAGING_CHECKS = {
    "migration", "edge", "web", "tenant_isolation", "provider_handshake", "recovery"
}
FORBIDDEN_BUILD_LITERALS = (
    b"SUPABASE_SERVICE_ROLE_KEY",
    b"STAGING_SUPABASE_SERVICE_ROLE_KEY",
    b"service_role",
    b"TELEGRAM_BOT_TOKEN",
    b"ANTHROPIC_API_KEY",
    b"OPENAI_API_KEY",
    b"navigator.serviceWorker",
    b"serviceWorker.register",
    b"sourceMappingURL=",
)
FORBIDDEN_BUILD_PATTERNS = (
    re.compile(rb"sb_secret_[A-Za-z0-9_-]{20,}", re.IGNORECASE),
)


class DeploymentRejected(RuntimeError):
    """A release gate is missing, stale, private, or contradictory."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _parse_time(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise DeploymentRejected(f"{label} is invalid") from error
    else:
        raise DeploymentRejected(f"{label} is invalid")
    if parsed.tzinfo is None:
        raise DeploymentRejected(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _bounded_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise DeploymentRejected(f"{label} is invalid")
    return value


def build_release_report(
    source: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    if set(source) != {"environment", "commit", "checks", "restore_age_days"}:
        raise DeploymentRejected("release evidence fields are invalid")
    environment = source.get("environment")
    commit = source.get("commit")
    checks = source.get("checks")
    if environment != "staging" or not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise DeploymentRejected("release identity is invalid")
    if not isinstance(checks, Mapping) or set(checks) != STAGING_CHECKS:
        raise DeploymentRejected("release checks are incomplete")
    if any(value != "passed" for value in checks.values()):
        raise DeploymentRejected("release checks did not all pass")
    restore_age = _bounded_integer(source.get("restore_age_days"), "restore age")
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report: dict[str, Any] = {
        "status": "passed",
        "environment": environment,
        "commit": commit,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "private_data": False,
        "checks": {name: "passed" for name in sorted(checks)},
        "recovery": {"restore_age_days": restore_age},
    }
    report["evidence_digest"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report


def validate_release_report(
    value: Mapping[str, Any], *, expected_commit: str, now: datetime | None = None
) -> dict[str, Any]:
    expected = {
        "status", "environment", "commit", "generated_at", "private_data",
        "checks", "recovery", "evidence_digest",
    }
    if set(value) != expected or value.get("status") != "passed" or value.get("private_data") is not False:
        raise DeploymentRejected("staging report is invalid")
    if value.get("environment") != "staging" or value.get("commit") != expected_commit:
        raise DeploymentRejected("staging report is for a different release")
    checks = value.get("checks")
    recovery = value.get("recovery")
    if not isinstance(checks, Mapping) or set(checks) != STAGING_CHECKS:
        raise DeploymentRejected("staging report checks are incomplete")
    if any(result != "passed" for result in checks.values()):
        raise DeploymentRejected("staging report has a failed check")
    if not isinstance(recovery, Mapping) or set(recovery) != {"restore_age_days"}:
        raise DeploymentRejected("staging recovery evidence is invalid")
    _bounded_integer(recovery["restore_age_days"], "restore age")
    generated_at = _parse_time(value.get("generated_at"), "report time")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if generated_at > observed_at + _hours(1) or generated_at < observed_at - _hours(24):
        raise DeploymentRejected("staging report is stale")
    digest = value.get("evidence_digest")
    unsigned = {key: value[key] for key in value if key != "evidence_digest"}
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise DeploymentRejected("staging report digest is invalid")
    if not secrets_compare(digest, hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()):
        raise DeploymentRejected("staging report digest does not match")
    return dict(value)


def _hours(value: int):
    from datetime import timedelta

    return timedelta(hours=value)


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def validate_owner_cutover_evidence(
    *,
    expected_commit: str,
    staging_report: Mapping[str, Any],
    backup_checked_at: str,
    backup_evidence_hash: str,
    deployment_confirmation: str,
    pause_confirmation: str,
    rollback_ref: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Validate the one-time legacy cutover without assuming the new health API exists."""
    if deployment_confirmation != "CUTOVER OWNER" or pause_confirmation != "TRIGGERS PAUSED":
        raise DeploymentRejected("owner cutover confirmation is invalid")
    if not SHA_RE.fullmatch(rollback_ref) or rollback_ref == expected_commit:
        raise DeploymentRejected("rollback reference is invalid")
    if not isinstance(backup_evidence_hash, str) or not DIGEST_RE.fullmatch(
        backup_evidence_hash
    ):
        raise DeploymentRejected("backup evidence digest is invalid")
    report = validate_release_report(
        staging_report, expected_commit=expected_commit, now=now
    )
    if _bounded_integer(report["recovery"]["restore_age_days"], "restore age") > 30:
        raise DeploymentRejected("restore evidence is stale")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checked_at = _parse_time(backup_checked_at, "backup evidence time")
    if checked_at > observed_at + _hours(1) or checked_at < observed_at - _hours(36):
        raise DeploymentRejected("backup evidence is stale")
    return {"status": "ready", "rollback_ref": rollback_ref}


def validate_production_evidence(
    operator_health: Mapping[str, Any],
    *,
    expected_commit: str,
    staging_report: Mapping[str, Any],
    backup_max_hours: int,
    restore_max_days: int,
    deployment_confirmation: str,
    pause_confirmation: str,
    rollback_ref: str,
    now: datetime | None = None,
) -> dict[str, str]:
    if deployment_confirmation != "DEPLOY PRODUCTION" or pause_confirmation != "TRIGGERS PAUSED":
        raise DeploymentRejected("production confirmation is invalid")
    if not SHA_RE.fullmatch(rollback_ref) or rollback_ref == expected_commit:
        raise DeploymentRejected("rollback reference is invalid")
    if not 1 <= backup_max_hours <= 36 or not 1 <= restore_max_days <= 30:
        raise DeploymentRejected("recovery thresholds are invalid")
    report = validate_release_report(staging_report, expected_commit=expected_commit, now=now)
    restore_age = _bounded_integer(report["recovery"]["restore_age_days"], "restore age")
    if restore_age > restore_max_days:
        raise DeploymentRejected("restore evidence is stale")

    components = operator_health.get("component_status")
    backup = operator_health.get("backup")
    missed = operator_health.get("missed_runs")
    provider = operator_health.get("provider_adapter")
    projection = operator_health.get("projection")
    if not all(isinstance(item, Mapping) for item in (components, backup, missed, provider, projection)):
        raise DeploymentRejected("operator health is malformed")
    for component in ("database", "scheduler", "provider_adapter", "backup", "projections"):
        if components.get(component) != "ok":
            raise DeploymentRejected("production component is not healthy")
    if _bounded_integer(backup.get("age_hours"), "backup age") > backup_max_hours:
        raise DeploymentRejected("backup evidence is stale")
    if _bounded_integer(missed.get("last_24_hours"), "missed runs") != 0:
        raise DeploymentRejected("recent scheduled run is missing")
    if _bounded_integer(provider.get("unavailable"), "provider availability") != 0:
        raise DeploymentRejected("provider connection is unavailable")
    if _bounded_integer(projection.get("failed"), "projection failures") != 0 or _bounded_integer(
        projection.get("paused"), "paused projections"
    ) != 0:
        raise DeploymentRejected("ledger projection is not healthy")
    return {"status": "ready", "rollback_ref": rollback_ref}


def scan_build_output(directory: Path) -> dict[str, int | str]:
    root = directory.resolve()
    if not root.is_dir() or root.is_symlink():
        raise DeploymentRejected("web build directory is invalid")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files or len(files) > 1000:
        raise DeploymentRejected("web build file count is invalid")
    total = 0
    digest = hashlib.sha256()
    for path in files:
        if path.is_symlink() or path.suffix == ".map":
            raise DeploymentRejected("web build contains a forbidden file")
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        total += len(payload)
        if total > 25 * 1024 * 1024:
            raise DeploymentRejected("web build is too large")
        if any(marker.lower() in payload.lower() for marker in FORBIDDEN_BUILD_LITERALS) or any(
            pattern.search(payload) for pattern in FORBIDDEN_BUILD_PATTERNS
        ):
            raise DeploymentRejected("web build contains forbidden runtime material")
        digest.update(relative.encode() + b"\0" + payload + b"\0")
    return {"status": "passed", "files": len(files), "bytes": total, "digest": digest.hexdigest()}


def write_report(
    path: Path, report: Mapping[str, Any], *, now: datetime | None = None
) -> None:
    validated = validate_release_report(
        report, expected_commit=str(report.get("commit")), now=now
    )
    encoded = json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(encoded)


def _operator_health(database_url: str, operator_id: str) -> dict[str, Any]:
    try:
        owner = UUID(operator_id)
    except ValueError as error:
        raise DeploymentRejected("operator identity is invalid") from error
    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
            row = connection.execute(
                "SELECT app.read_operator_health(%s)", (owner,)
            ).fetchone()
    except psycopg.Error as error:
        raise DeploymentRejected("operator health query failed") from error
    if row is None or not isinstance(row[0], dict):
        raise DeploymentRejected("operator health query is malformed")
    return row[0]


def _bounded_request(request: Request, *, opener: Callable[..., Any] = urlopen) -> bytes:
    try:
        with opener(request, timeout=15) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_HTTP_BYTES):
                raise DeploymentRejected("deployment response is too large")
            body = response.read(MAX_HTTP_BYTES + 1)
    except OSError as error:
        raise DeploymentRejected("deployment endpoint is unavailable") from error
    if len(body) > MAX_HTTP_BYTES:
        raise DeploymentRejected("deployment response is too large")
    return body


def _bounded_get(url: str, *, opener: Callable[..., Any] = urlopen) -> bytes:
    return _bounded_request(Request(
        url, method="GET", headers={"User-Agent": "stock-agent-release-verifier/1"}
    ), opener=opener)


def verify_postgrest_tenant_isolation(
    project_url: str,
    publishable_key: str,
    bundle: Mapping[str, Any],
    *,
    opener: Callable[..., Any] = urlopen,
) -> str:
    parsed = urlparse(project_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co"):
        raise DeploymentRejected("Supabase project URL is invalid")
    if bundle.get("version") != 1 or bundle.get("supabase_url") != project_url.rstrip("/"):
        raise DeploymentRejected("security-user bundle is invalid")
    if not isinstance(publishable_key, str) or not publishable_key.startswith("sb_publishable_"):
        raise DeploymentRejected("publishable key is invalid")
    owners: list[tuple[str, str]] = []
    for label in ("owner_a", "owner_b"):
        row = bundle.get(label)
        if not isinstance(row, Mapping):
            raise DeploymentRejected("security-user bundle is invalid")
        try:
            owner_id = str(UUID(str(row.get("id"))))
        except ValueError as error:
            raise DeploymentRejected("security-user bundle is invalid") from error
        token = row.get("access_token")
        if not isinstance(token, str) or len(token) > 16_384 or token.count(".") != 2:
            raise DeploymentRejected("security-user bundle is invalid")
        owners.append((owner_id, token))
    if owners[0][0] == owners[1][0]:
        raise DeploymentRejected("security-user identities are not distinct")

    endpoint = project_url.rstrip("/") + "/rest/v1/profile?select=id"
    for (owner_id, token), (other_id, _) in zip(owners, reversed(owners), strict=True):
        headers = {
            "Authorization": f"Bearer {token}",
            "apikey": publishable_key,
            "Accept": "application/json",
            "Accept-Profile": "api",
            "User-Agent": "stock-agent-release-verifier/1",
        }
        observed: list[Any] = []
        for url in (endpoint, f"{endpoint}&id=eq.{other_id}"):
            try:
                value = json.loads(_bounded_request(
                    Request(url, method="GET", headers=headers), opener=opener
                ))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DeploymentRejected("tenant-isolation response is malformed") from error
            if not isinstance(value, list) or len(value) > 2:
                raise DeploymentRejected("tenant-isolation response is malformed")
            observed.append(value)
        if observed[0] != [{"id": owner_id}] or observed[1] != []:
            raise DeploymentRejected("staging tenant isolation failed")
    return "passed"


def _public_health(project_url: str) -> None:
    parsed = urlparse(project_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co"):
        raise DeploymentRejected("Supabase project URL is invalid")
    body = _bounded_get(project_url.rstrip("/") + "/functions/v1/app-api/healthz")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeploymentRejected("public health response is malformed") from error
    if value != {"ok": True, "data": {"status": "ok", "schema_version": 1}}:
        raise DeploymentRejected("public health response is not exact")


def _web_health(
    web_url: str,
    *,
    expected_commit: str,
    environment: str,
    opener: Callable[..., Any] = urlopen,
) -> None:
    parsed = urlparse(web_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
        raise DeploymentRejected("web URL is invalid")
    if SHA_RE.fullmatch(expected_commit) is None or environment not in {
        "staging", "production"
    }:
        raise DeploymentRejected("expected web release is invalid")
    root = web_url.rstrip("/")
    if b'<div id="root"></div>' not in _bounded_get(root + "/", opener=opener):
        raise DeploymentRejected("web root is malformed")
    try:
        marker = json.loads(_bounded_get(root + "/release.json", opener=opener))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeploymentRejected("web release marker is malformed") from error
    if marker != {"commit": expected_commit, "environment": environment}:
        raise DeploymentRejected("web release marker does not match")


def _database_release_checks(
    database_url: str, owner_a: str, owner_b: str, connection_public_id: str,
    *, now: datetime | None = None,
) -> dict[str, str]:
    try:
        owners = (UUID(owner_a), UUID(owner_b))
        public_id = UUID(connection_public_id)
    except ValueError as error:
        raise DeploymentRejected("staging test identity is invalid") from error
    if owners[0] == owners[1]:
        raise DeploymentRejected("staging owners are not distinct")
    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
            catalog = connection.execute(
                "SELECT machine.backup_export_catalog(%s::jsonb)",
                (json.dumps({"schema_version": 1}),),
            ).fetchone()
            if catalog is None or not isinstance(catalog[0], dict):
                raise DeploymentRejected("migration catalog is unavailable")
            for owner, other in ((owners[0], owners[1]), (owners[1], owners[0])):
                with connection.transaction(force_rollback=True):
                    connection.execute("SET LOCAL ROLE authenticated")
                    connection.execute(
                        "SELECT set_config('request.jwt.claim.sub', %s, true)", (str(owner),)
                    )
                    own = connection.execute("SELECT id FROM api.profile").fetchall()
                    cross = connection.execute(
                        "SELECT id FROM api.profile WHERE id = %s", (other,)
                    ).fetchall()
                    if own != [(owner,)] or cross:
                        raise DeploymentRejected("staging tenant isolation failed")
            provider_record = read_connection(connection, public_id)
            provider = evaluate_connection(provider_record)
            if provider.get("ok") is not True:
                raise DeploymentRejected("staging provider handshake failed")
            validate_handshake_freshness(provider_record or {}, now=now)
    except psycopg.Error as error:
        raise DeploymentRejected("staging database verification failed") from error
    return {
        "migration": "passed",
        "tenant_isolation": "passed",
        "provider_handshake": "passed",
    }


def validate_handshake_freshness(
    record: Mapping[str, Any], *, now: datetime | None = None
) -> None:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for key in ("last_handshake_at", "slot_updated_at"):
        timestamp = _parse_time(record.get(key), "handshake evidence time")
        if timestamp > observed_at + _hours(1) or timestamp < observed_at - _hours(24):
            raise DeploymentRejected("handshake evidence is stale or future-dated")


def verify_staging(
    *, database_url: str, operator_id: str, owner_a: str, owner_b: str,
    connection_public_id: str, project_url: str, web_url: str, commit: str,
    publishable_key: str, security_users: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    checks = _database_release_checks(
        database_url, owner_a, owner_b, connection_public_id, now=now
    )
    bundle_rows = [security_users.get(label) for label in ("owner_a", "owner_b")]
    if not all(isinstance(row, Mapping) for row in bundle_rows):
        raise DeploymentRejected("security-user bundle is malformed")
    bundle_ids = tuple(str(row.get("id")) for row in bundle_rows)  # type: ignore[union-attr]
    if bundle_ids != (owner_a, owner_b):
        raise DeploymentRejected("security-user identities do not match the database gate")
    checks["tenant_isolation"] = verify_postgrest_tenant_isolation(
        project_url, publishable_key, security_users
    )
    _public_health(project_url)
    checks["edge"] = "passed"
    _web_health(web_url, expected_commit=commit, environment="staging")
    checks["web"] = "passed"
    health = _operator_health(database_url, operator_id)
    restore = health.get("restore")
    components = health.get("component_status")
    if not isinstance(restore, Mapping) or not isinstance(components, Mapping):
        raise DeploymentRejected("staging recovery health is malformed")
    restore_age = _bounded_integer(restore.get("age_days"), "restore age")
    if components.get("restore") != "ok" or restore_age > 30:
        raise DeploymentRejected("staging restore drill is stale")
    checks["recovery"] = "passed"
    return build_release_report({
        "environment": "staging",
        "commit": commit,
        "checks": checks,
        "restore_age_days": restore_age,
    }, now=now)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise DeploymentRejected(f"required environment is missing: {name}")
    return value


def _report_from_argument(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DeploymentRejected("staging report JSON is malformed") from error
    if not isinstance(value, dict):
        raise DeploymentRejected("staging report JSON is malformed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan-build")
    scan.add_argument("--dist", type=Path, default=Path("apps/web/dist"))
    stage_pre = sub.add_parser("staging-preflight")
    stage_pre.add_argument("--project-ref", required=True)
    stage_pre.add_argument("--confirm", required=True)
    stage = sub.add_parser("staging-verify")
    stage.add_argument("--commit", required=True)
    stage.add_argument("--report", type=Path)
    stage.add_argument("--security-users", type=Path, required=True)
    prod = sub.add_parser("production-preflight")
    prod.add_argument("--commit", required=True)
    prod.add_argument("--staging-report-json", required=True)
    prod.add_argument("--rollback-ref", required=True)
    prod.add_argument("--confirm", required=True)
    prod.add_argument("--pause-confirm", required=True)
    prod.add_argument("--max-backup-hours", type=int, default=36)
    prod.add_argument("--max-restore-days", type=int, default=30)
    cutover = sub.add_parser("owner-cutover-preflight")
    cutover.add_argument("--commit", required=True)
    cutover.add_argument("--staging-report-json", required=True)
    cutover.add_argument("--rollback-ref", required=True)
    cutover.add_argument("--confirm", required=True)
    cutover.add_argument("--pause-confirm", required=True)
    cutover.add_argument("--backup-checked-at", required=True)
    cutover.add_argument("--backup-evidence-hash", required=True)
    health = sub.add_parser("production-health")
    health.add_argument("--project-ref", required=True)
    health.add_argument("--commit", required=True)
    verify = sub.add_parser("production-verify")
    verify.add_argument("--project-ref", required=True)
    verify.add_argument("--confirm", required=True)
    verify.add_argument("--commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "scan-build":
            result = scan_build_output(args.dist)
        elif args.command == "staging-preflight":
            if not PROJECT_RE.fullmatch(args.project_ref) or "prod" in args.project_ref:
                raise DeploymentRejected("staging project reference is invalid")
            if args.confirm != f"DEPLOY STAGING {args.project_ref}":
                raise DeploymentRejected("staging confirmation is invalid")
            result = {"status": "ready", "environment": "staging"}
        elif args.command == "staging-verify":
            raw_bundle = args.security_users.read_bytes()
            if len(raw_bundle) > MAX_HTTP_BYTES:
                raise DeploymentRejected("security-user bundle is too large")
            try:
                security_users = json.loads(raw_bundle)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DeploymentRejected("security-user bundle is malformed") from error
            if not isinstance(security_users, dict):
                raise DeploymentRejected("security-user bundle is malformed")
            result = verify_staging(
                database_url=_required_environment("STAGING_POSTGRES_URL"),
                operator_id=_required_environment("STAGING_OPERATOR_ID"),
                owner_a=_required_environment("STAGING_TEST_OWNER_A_ID"),
                owner_b=_required_environment("STAGING_TEST_OWNER_B_ID"),
                connection_public_id=_required_environment("STAGING_CONNECTION_PUBLIC_ID"),
                project_url=_required_environment("STAGING_SUPABASE_URL"),
                web_url=_required_environment("STAGING_WEB_URL"),
                commit=args.commit,
                publishable_key=_required_environment("STAGING_SUPABASE_PUBLISHABLE_KEY"),
                security_users=security_users,
            )
            if args.report:
                write_report(args.report, result)
        elif args.command == "production-preflight":
            result = validate_production_evidence(
                _operator_health(
                    _required_environment("PRODUCTION_POSTGRES_URL"),
                    _required_environment("PRODUCTION_OPERATOR_ID"),
                ),
                expected_commit=args.commit,
                staging_report=_report_from_argument(args.staging_report_json),
                backup_max_hours=args.max_backup_hours,
                restore_max_days=args.max_restore_days,
                deployment_confirmation=args.confirm,
                pause_confirmation=args.pause_confirm,
                rollback_ref=args.rollback_ref,
            )
        elif args.command == "owner-cutover-preflight":
            result = validate_owner_cutover_evidence(
                expected_commit=args.commit,
                staging_report=_report_from_argument(args.staging_report_json),
                backup_checked_at=args.backup_checked_at,
                backup_evidence_hash=args.backup_evidence_hash,
                deployment_confirmation=args.confirm,
                pause_confirmation=args.pause_confirm,
                rollback_ref=args.rollback_ref,
            )
        else:
            if not PROJECT_RE.fullmatch(args.project_ref):
                raise DeploymentRejected("production verification project is invalid")
            if args.command == "production-verify" and args.confirm != "OWNER SMOKE PASSED":
                raise DeploymentRejected("production verification confirmation is invalid")
            _public_health(_required_environment("PRODUCTION_SUPABASE_URL"))
            _web_health(
                _required_environment("PRODUCTION_WEB_URL"),
                expected_commit=args.commit,
                environment="production",
            )
            health = _operator_health(
                _required_environment("PRODUCTION_POSTGRES_URL"),
                _required_environment("PRODUCTION_OPERATOR_ID"),
            )
            components = health.get("component_status", {})
            if not isinstance(components, Mapping) or any(
                components.get(name) != "ok"
                for name in (
                    "database", "scheduler", "provider_adapter", "backup", "restore", "projections"
                )
            ):
                raise DeploymentRejected("production verification is unhealthy")
            result = {
                "status": "passed",
                "environment": "production",
                "gate": "owner_smoke" if args.command == "production-verify" else "post_deploy_health",
            }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (DeploymentRejected, OSError, psycopg.Error):
        print("deployment verification rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
