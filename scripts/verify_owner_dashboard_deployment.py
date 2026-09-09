#!/usr/bin/env python3
"""GET-only production canary for the owner-only dashboard API."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.provision_owner_dashboard_auth import (
    validate_configuration as validate_auth_admin_configuration,
    validate_email_otp_configuration,
)
CANARY_METHOD = "GET"
CANARY_ROUTES = (
    "/v1/today",
    "/v1/portfolio",
    "/v1/ideas",
    "/v1/companion",
    "/v1/alerts",
    "/v1/runs",
    "/v1/system",
    "/v1/intelligence",
    "/v1/reports",
)
BOUNDARIES = {
    "owner_only": True,
    "suggestion_only": True,
    "friend_invitations": "disabled",
    "brokerage_authority": "none",
}
INTELLIGENCE_BOUNDARIES = {
    "research_only": True,
    "execution_disabled": True,
    "valuation_unavailable": True,
}
ROUTE_BOUNDARIES = {
    "/v1/today": BOUNDARIES,
    "/v1/system": BOUNDARIES,
    "/v1/intelligence": INTELLIGENCE_BOUNDARIES,
}
RELEASE_FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")
RUNTIME_ROLE = "stock_agent_dashboard_runtime"
EVIDENCE_ROLE = "stock_agent_release_reader_runtime"
SCHEDULED_PHASES = {"pre-market", "intraday", "post-market"}
MAX_SCHEDULED_READINESS_ROWS = 128
REPORT_KINDS = {
    "morning", "urgent", "weekly", "monthly", "theme", "on-demand",
    "intraday",
}
REPORT_PUBLIC_SOURCE_FIELDS = (
    "id", "run_id", "market_date", "kind", "canonical", "report_hash",
    "created_at",
)
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
MIGRATION_NAME = re.compile(r"^(?P<version>\d{8}(?:\d{4})?)_[a-z0-9][a-z0-9_]*\.sql$")
REPORT_PUBLICATION_SQL = """SELECT p.report_id::text AS report_id,r.run_id::text AS run_id,p.idempotency_key,
    p.status,p.telegram_message_ids,to_char(p.telegram_accepted_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') AS telegram_accepted_at,p.suppression_reason,
    jsonb_build_object('report_id',p.report_id,'idempotency_key',p.idempotency_key,'status',p.status,
      'telegram_message_ids',p.telegram_message_ids,
      'telegram_accepted_at',to_char(p.telegram_accepted_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'),
      'suppression_reason',p.suppression_reason) AS canonical
    FROM public.market_report_publications p JOIN public.market_reports r ON r.id=p.report_id
    WHERE r.run_id=%s::uuid ORDER BY p.report_id"""
DASHBOARD_REPORT_SOURCE_SQL = """SELECT r.id::text AS id,
       r.run_id::text AS run_id, r.market_date::text AS market_date, r.kind,
       r.report AS canonical, r.report_hash,
       to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS created_at
  FROM unnest(%s::uuid[]) WITH ORDINALITY AS requested(id, position)
  JOIN public.market_reports r ON r.id = requested.id
 ORDER BY requested.position"""
EVIDENCE_REPORT_SOURCE_SQL = """SELECT r.id::text AS id,
       r.run_id::text AS run_id, r.packet_id::text AS packet_id,
       r.market_date::text AS market_date, r.kind, r.report AS canonical,
       r.report_hash, r.rendered_text, r.rendered_hash,
       to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS created_at
  FROM public.market_reports r
  JOIN public.market_intelligence_runs i ON i.id = r.run_id
  JOIN public.market_evidence_packets p
    ON p.id = r.packet_id AND p.run_id = r.run_id
 ORDER BY r.created_at DESC, r.id DESC LIMIT 50"""


def validate_deployment_auth_configuration(config: Mapping[str, object]) -> dict[str, object]:
    """Expose the Auth configuration gate at the deployment-verifier boundary."""
    return validate_email_otp_configuration(config)


def load_deployment_auth_configuration_receipt(receipt_path: Path) -> dict[str, object]:
    """Read the protected, manually verified Auth-settings receipt before network access."""
    try:
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Auth configuration receipt is unavailable or malformed") from error
    if not isinstance(value, dict):
        raise RuntimeError("Auth configuration receipt is unavailable or malformed")
    return validate_deployment_auth_configuration(value)


def normalize_receipt_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_sha256(value: object) -> str:
    """Hash retained JSON content using the same canonical bytes as receipt producers."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


def _dashboard_text(value: object, maximum: int) -> str | None:
    """Mirror the bounded scalar conversion used by the dashboard mapper."""
    if isinstance(value, str) and value:
        return value[:maximum]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)[:maximum]
    if isinstance(value, float) and math.isfinite(value):
        rendered = str(int(value)) if value.is_integer() else str(value)
        return rendered[:maximum]
    return None


def report_summary_source_view(row: Mapping[str, object]) -> dict[str, object]:
    """Map a protected report row to the public report-summary contract."""
    report = row.get("canonical")
    if not isinstance(report, Mapping):
        report = {}
    kind = row.get("kind")
    return {
        "id": _dashboard_text(row.get("id"), 64) or "unknown",
        "market_date": _dashboard_text(row.get("market_date"), 40) or "",
        "kind": kind if kind in REPORT_KINDS else "unknown",
        "title": _dashboard_text(report.get("title"), 200) or "Untitled report",
        "summary": _dashboard_text(report.get("summary"), 1_000) or "",
        "report_hash": _dashboard_text(row.get("report_hash"), 64) or "",
        "created_at": _dashboard_text(row.get("created_at"), 40) or "",
    }


def validate_intelligence_projection_receipt(value: object) -> str | None:
    """Validate bounded metadata for the owner intelligence projection."""
    if not isinstance(value, Mapping) or set(value) != {
        "projection_present", "projection_type", "run_id_present",
        "run_id_type", "intelligence_run_id",
    }:
        raise RuntimeError("dashboard intelligence projection receipt is unavailable")
    run_id_type = value.get("run_id_type")
    run_id = value.get("intelligence_run_id")
    if (
        value.get("projection_present") is not True
        or value.get("projection_type") != "object"
        or value.get("run_id_present") is not True
        or run_id_type not in {"null", "string"}
        or (run_id_type == "null" and run_id is not None)
        or (
            run_id_type == "string"
            and (
                not isinstance(run_id, str)
                or UUID_PATTERN.fullmatch(run_id) is None
            )
        )
    ):
        raise RuntimeError("dashboard intelligence projection receipt is unavailable")
    return run_id if isinstance(run_id, str) else None


def scheduled_readiness_receipt(overdue: object) -> dict[str, object]:
    """Bind overdue slot visibility without promoting it to deployment health."""
    if not isinstance(overdue, list):
        raise RuntimeError("dashboard claim differs from its source receipt")
    if len(overdue) > MAX_SCHEDULED_READINESS_ROWS:
        raise RuntimeError("dashboard claim differs from its source receipt")
    rows: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for raw in overdue:
        if not isinstance(raw, Mapping) or set(raw) != {
            "market_date", "phase", "deadline_at",
        }:
            raise RuntimeError("dashboard claim differs from its source receipt")
        market_date = raw.get("market_date")
        phase = raw.get("phase")
        deadline_at = raw.get("deadline_at")
        try:
            parsed_date = date.fromisoformat(market_date) if isinstance(market_date, str) else None
        except ValueError:
            parsed_date = None
        normalized_deadline = normalize_receipt_timestamp(deadline_at)
        if (
            parsed_date is None
            or parsed_date.isoformat() != market_date
            or phase not in SCHEDULED_PHASES
            or normalized_deadline != deadline_at
        ):
            raise RuntimeError("dashboard claim differs from its source receipt")
        identity = (market_date, str(phase))
        if identity in identities:
            raise RuntimeError("dashboard claim differs from its source receipt")
        identities.add(identity)
        rows.append({
            "market_date": market_date,
            "phase": str(phase),
            "deadline_at": str(deadline_at),
        })
    rows.sort(key=lambda row: (row["market_date"], row["phase"], row["deadline_at"]))
    deadlines = sorted(row["deadline_at"] for row in rows)
    return {
        "status": "pending" if rows else "ready",
        "overdue_phase_count": len(rows),
        "oldest_deadline_at": deadlines[0] if deadlines else None,
        "latest_deadline_at": deadlines[-1] if deadlines else None,
        "phases": sorted({row["phase"] for row in rows}),
        "receipt_sha256": canonical_sha256(rows),
        "overdue_scheduled_phases": rows,
    }


def validate_scheduled_readiness_receipt(value: object) -> dict[str, object]:
    """Validate the bounded release-time schedule observation."""
    if not isinstance(value, Mapping) or set(value) != {
        "status", "overdue_phase_count", "oldest_deadline_at",
        "latest_deadline_at", "phases", "receipt_sha256",
        "overdue_scheduled_phases",
    }:
        raise RuntimeError("scheduled readiness receipt is incomplete")
    try:
        expected = scheduled_readiness_receipt(
            value.get("overdue_scheduled_phases"),
        )
    except RuntimeError as error:
        raise RuntimeError("scheduled readiness receipt is incomplete") from error
    if dict(value) != expected:
        raise RuntimeError("scheduled readiness receipt is incomplete")
    return expected


def normalize_migration_statements(sql: str) -> list[str]:
    # Keep this byte-independent representation aligned with Supabase's
    # schema_migrations.statements[] receipts.
    statements, buffer, quote, dollar, index = [], [], None, None, 0
    while index < len(sql):
        char = sql[index]
        if quote is None and dollar is None and sql.startswith("--", index):
            end = sql.find("\n", index); index = len(sql) if end < 0 else end; continue
        if quote is None and dollar is None and sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0: raise RuntimeError("migration contains unterminated comment")
            index = end + 2; continue
        if dollar is not None:
            if sql.startswith(dollar, index): buffer.append(dollar); index += len(dollar); dollar = None; continue
            buffer.append(char); index += 1; continue
        if quote is not None:
            buffer.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote: buffer.append(quote); index += 2; continue
                quote = None
            index += 1; continue
        matched = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
        if matched: dollar = matched.group(0); buffer.append(dollar); index += len(dollar); continue
        if char in {"'", '"'}: quote = char; buffer.append(char)
        elif char == ";":
            value = " ".join("".join(buffer).split())
            if value: statements.append(value)
            buffer = []
        else: buffer.append(char)
        index += 1
    if quote is not None or dollar is not None: raise RuntimeError("migration contains unterminated quoted SQL")
    value = " ".join("".join(buffer).split())
    if value: statements.append(value)
    if not statements: raise RuntimeError("candidate migration is empty")
    return statements


def migration_statements_sha256(statements: Sequence[str]) -> str:
    canonical = [item for statement in statements for item in normalize_migration_statements(statement)]
    return canonical_sha256(canonical)


def candidate_migration_manifest(migrations_directory: Path = ROOT / "sql/migrations") -> list[dict[str, str]]:
    if not migrations_directory.is_dir() or migrations_directory.is_symlink():
        raise RuntimeError("candidate migration directory is unavailable")
    paths = sorted(migrations_directory.iterdir())
    if (not paths or any(not path.is_file() or path.is_symlink() or not MIGRATION_NAME.fullmatch(path.name)
                          for path in paths)):
        raise RuntimeError("candidate migration manifest is malformed")
    manifest = [{
        "path": f"sql/migrations/{path.name}",
        "version": MIGRATION_NAME.fullmatch(path.name).group("version"),  # type: ignore[union-attr]
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    } for path in paths]
    if len({item["version"] for item in manifest}) != len(manifest):
        raise RuntimeError("candidate migration versions must be globally unique")
    return manifest


def verify_release_artifact_receipts(
    candidate_sha: str,
    deployment: Mapping[str, object],
    expected_migrations: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    """Bind migrations, changed functions, and static assets to one reviewed candidate."""
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
        raise RuntimeError("release candidate SHA receipt is malformed")
    migrations = deployment.get("migrations")
    functions = deployment.get("functions")
    static = deployment.get("static_assets")
    if not isinstance(migrations, list) or migrations != list(expected_migrations):
        raise RuntimeError("release migration receipts are incomplete")
    if any(not isinstance(row, Mapping) or set(row) != {"path", "version", "sha256"}
           or not re.fullmatch(r"sql/migrations/\d{8}(?:\d{4})?_[a-z0-9][a-z0-9_]*\.sql", str(row.get("path", "")))
           or not re.fullmatch(r"\d{8}(?:\d{4})?", str(row.get("version", "")))
           or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) for row in migrations):
        raise RuntimeError("release migration hash receipt is malformed")
    if not isinstance(functions, list) or tuple(row.get("function") for row in functions if isinstance(row, dict)) != RELEASE_FUNCTIONS:
        raise RuntimeError("changed function receipts are incomplete")
    for row in functions:
        if (
            row.get("git_sha") != candidate_sha
            or isinstance(row.get("function_version"), bool)
            or not isinstance(row.get("function_version"), int)
            or int(row["function_version"]) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_sha256", "")))
        ):
            raise RuntimeError("changed function source receipt is incomplete")
    if (
        not isinstance(static, Mapping)
        or static.get("status") != "verified"
        or static.get("candidate_sha") != candidate_sha
        or not isinstance(static.get("asset_hashes"), list)
        or not static["asset_hashes"]
    ):
        raise RuntimeError("immutable static asset receipt is incomplete")
    return {
        "status": "verified",
        "candidate_sha": candidate_sha,
        "migration_version": ",".join(row["version"] for row in migrations),
        "function_count": len(RELEASE_FUNCTIONS),
        "static_asset_count": len(static["asset_hashes"]),
    }


def _auth_request(method: str, url: str, headers: Mapping[str, str], body: bytes | None):
    request = Request(url, method=method, headers=dict(headers), data=body)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def _auth_json(status: int, body: bytes) -> dict[str, object]:
    if status < 200 or status >= 300:
        raise RuntimeError("ephemeral owner session request failed")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("ephemeral owner session response is malformed") from error
    if not isinstance(value, dict):
        raise RuntimeError("ephemeral owner session response is malformed")
    return value


def obtain_ephemeral_owner_access_token(
    project_url: str,
    owner_email: str,
    redirect_origin: str,
    service_key: str,
    publishable_key: str,
    *,
    verification_type: str = "magiclink",
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _auth_request,
) -> str:
    """Generate and directly verify an owner link without sending an email or logging the token."""
    project_url, owner_email, service_key = validate_auth_admin_configuration(
        project_url, owner_email, service_key,
    )
    validate_api_boundary(f"{project_url}/functions/v1/owner-dashboard-api", redirect_origin)
    if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{24,128}", publishable_key):
        raise ValueError("Supabase publishable key is invalid")
    if verification_type not in {"magiclink", "recovery"}:
        raise ValueError("ephemeral Auth verification type is invalid")
    admin_headers = {
        "apikey": service_key,
        "authorization": f"Bearer {service_key}",
        "content-type": "application/json",
    }
    link_body = json.dumps({
        "type": verification_type,
        "email": owner_email,
        "options": {"redirect_to": redirect_origin},
    }, separators=(",", ":")).encode()
    link_status, link_response = requester(
        "POST", f"{project_url}/auth/v1/admin/generate_link", admin_headers, link_body,
    )
    link = _auth_json(link_status, link_response)
    token_hash = link.get("hashed_token")
    if not isinstance(token_hash, str):
        properties = link.get("properties")
        token_hash = properties.get("hashed_token") if isinstance(properties, dict) else None
    if not isinstance(token_hash, str) or not 8 <= len(token_hash) <= 512 or any(character.isspace() for character in token_hash):
        raise RuntimeError("ephemeral owner session link receipt is malformed")

    public_headers = {
        "apikey": publishable_key,
        "authorization": f"Bearer {publishable_key}",
        "content-type": "application/json",
    }
    verify_body = json.dumps({"type": verification_type, "token_hash": token_hash}, separators=(",", ":")).encode()
    verify_status, verify_response = requester(
        "POST", f"{project_url}/auth/v1/verify", public_headers, verify_body,
    )
    session = _auth_json(verify_status, verify_response)
    access_token = session.get("access_token")
    if (
        not isinstance(access_token, str)
        or len(access_token) < 40
        or len(access_token) > 8_192
        or any(character.isspace() for character in access_token)
    ):
        raise RuntimeError("ephemeral owner session receipt is malformed")
    return access_token


def obtain_ephemeral_existing_user_access_token(
    project_url: str,
    email: str,
    redirect_origin: str,
    service_key: str,
    publishable_key: str,
    *,
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _auth_request,
) -> str:
    """Mint a recovery session that fails if the already-bound Auth identity disappeared."""
    return obtain_ephemeral_owner_access_token(
        project_url, email, redirect_origin, service_key, publishable_key,
        verification_type="recovery", requester=requester,
    )


def revoke_ephemeral_owner_session(
    project_url: str,
    access_token: str,
    publishable_key: str,
    *,
    scope: str = "global",
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _auth_request,
) -> dict[str, str]:
    """Revoke the requested temporary canary session scope without returning its token."""
    parsed = urlparse(project_url)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"[a-z0-9]{20}\.supabase\.co", parsed.hostname or "")
        or project_url != f"https://{parsed.netloc}"
    ):
        raise ValueError("project URL must be an exact Supabase origin")
    if (
        len(access_token) < 40
        or len(access_token) > 8_192
        or any(character.isspace() for character in access_token)
        or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{24,128}", publishable_key)
        or scope not in {"global", "local"}
    ):
        raise ValueError("ephemeral session revocation configuration is invalid")
    status, _body = requester(
        "POST",
        f"{project_url}/auth/v1/logout?scope={scope}",
        {
            "apikey": publishable_key,
            "authorization": f"Bearer {access_token}",
        },
        None,
    )
    if status < 200 or status >= 300:
        raise RuntimeError("ephemeral owner session revocation failed")
    return {"status": "revoked", "scope": scope}


def verify_auth_canary_inventory(
    project_url: str,
    owner_email: str,
    owner_user_id: str,
    canary_email: str,
    canary_user_id: str,
    service_key: str,
    *,
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _auth_request,
) -> dict[str, object]:
    """Require exactly one privileged owner and one permanently denied canary identity."""
    project_url, owner_email, service_key = validate_auth_admin_configuration(
        project_url, owner_email, service_key,
    )
    project_url, canary_email, service_key = validate_auth_admin_configuration(
        project_url, canary_email, service_key,
    )
    if not re.fullmatch(r"release-canary-[0-9a-f]{32}@example\.com", canary_email):
        raise ValueError("denied Auth identity must use the exact reserved canary address")
    if not UUID_PATTERN.fullmatch(owner_user_id) or not UUID_PATTERN.fullmatch(canary_user_id):
        raise ValueError("owner and canary user identifiers must be UUIDs")
    owner_user_id = owner_user_id.lower()
    canary_user_id = canary_user_id.lower()
    if owner_user_id == canary_user_id or owner_email == canary_email:
        raise ValueError("owner and denied canary identities must be distinct")
    headers = {"apikey": service_key, "authorization": f"Bearer {service_key}"}
    users = []
    for page in (1, 2):
        status, body = requester(
            "GET", f"{project_url}/auth/v1/admin/users?page={page}&per_page=1000",
            headers, None,
        )
        if status < 200 or status >= 300:
            raise RuntimeError("Auth canary inventory request failed")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("Auth canary inventory response is malformed") from error
        page_users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(page_users, list) or any(not isinstance(row, dict) for row in page_users):
            raise RuntimeError("Auth canary inventory response is malformed")
        if page == 2 and page_users:
            raise RuntimeError("Auth inventory must contain exactly the owner and denied canary")
        users.extend(page_users)
    observed = []
    for row in users:
        user_id = str(row.get("id", ""))
        email = str(row.get("email", "")).strip().lower()
        confirmed = row.get("email_confirmed_at") or row.get("confirmed_at")
        if not UUID_PATTERN.fullmatch(user_id) or not email or not isinstance(confirmed, str) or not confirmed:
            raise RuntimeError("Auth inventory must contain exactly the owner and denied canary")
        observed.append((user_id.lower(), email))
    expected = {(owner_user_id, owner_email), (canary_user_id, canary_email)}
    if len(observed) != 2 or len(set(observed)) != 2 or set(observed) != expected:
        raise RuntimeError("Auth inventory must contain exactly the owner and denied canary")
    return {
        "status": "verified",
        "identity_count": 2,
        "privileged_owner_count": 1,
        "denied_canary_count": 1,
    }


def validate_source_database_url(database_url: str, api_url: str) -> str:
    api = urlparse(api_url)
    project_ref = (api.hostname or "").split(".", 1)[0]
    parsed = urlparse(database_url)
    expected_user = f"{RUNTIME_ROLE}.{project_ref}"
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", parsed.hostname or "")
        or parsed.port != 5432
        or parsed.username != expected_user
        or not parsed.password
        or len(parsed.password) < 24
        or parsed.path != "/postgres"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("source database URL must use the scoped session-pooler login")
    return database_url


def validate_evidence_database_url(database_url: str, api_url: str) -> str:
    api = urlparse(api_url)
    project_ref = (api.hostname or "").split(".", 1)[0]
    parsed = urlparse(database_url)
    expected_pooler_user = f"{EVIDENCE_ROLE}.{project_ref}"
    pooler = (
        bool(re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", parsed.hostname or ""))
        and unquote(parsed.username or "") == expected_pooler_user
        and parsed.port == 5432
    )
    direct = (
        parsed.hostname == f"db.{project_ref}.supabase.co"
        and unquote(parsed.username or "") == EVIDENCE_ROLE
        and parsed.port in {None, 5432}
    )
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not (pooler or direct)
        or len(unquote(parsed.password or "")) < 24
        or parsed.path != "/postgres"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("evidence database URL must use the scoped release reader login")
    return database_url


def _fetch_one(connection, query: str, parameters: tuple[object, ...] = ()) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
    return dict(row) if row else {}


def _fetch_all(connection, query: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]


def collect_source_receipts(
    database_url: str,
    evidence_database_url: str,
    api_url: str,
    run_id: str,
) -> dict[str, object]:
    """Read visible claims and protected hashes through separate scoped logins."""
    validate_source_database_url(database_url, api_url)
    validate_evidence_database_url(evidence_database_url, api_url)
    if not UUID_PATTERN.fullmatch(run_id):
        raise ValueError("completed run identifier is malformed")
    timestamp = "to_char({field} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"

    project_ref = (urlparse(api_url).hostname or "").split(".", 1)[0]
    # Keep this import at the post-migration boundary. protected_evidence shares
    # canonical verification helpers with this module's release verifier.
    from scripts.protected_evidence import PostgresReadOnlySource
    with PostgresReadOnlySource(
        evidence_database_url, project_ref,
    ) as evidence_source:
        evidence_connection = evidence_source.connection
        if evidence_connection is None:
            raise RuntimeError("release evidence reader authority is unavailable")
        verified_authority = evidence_source.identity()
        evidence_authority = {
            "status": "verified",
            "connection_id": verified_authority.get("connection_id"),
            "read_only": verified_authority.get("read_only"),
            "isolated_guard": verified_authority.get("isolated_guard"),
        }
        evidence_identity = _fetch_one(
            evidence_connection,
            "SELECT current_user AS evidence_database_user, current_setting('transaction_read_only') AS evidence_transaction_read_only",
        )
        reports = _fetch_all(
            evidence_connection, EVIDENCE_REPORT_SOURCE_SQL,
        )
    report_ids = [row.get("id") for row in reports if isinstance(row, Mapping)]
    if (
        len(report_ids) != len(reports)
        or len(report_ids) > 50
        or any(
            not isinstance(report_id, str)
            or UUID_PATTERN.fullmatch(report_id) is None
            for report_id in report_ids
        )
        or len(set(report_ids)) != len(report_ids)
    ):
        raise RuntimeError("protected report identifiers are malformed")

    with psycopg.connect(
        database_url,
        row_factory=dict_row,
        sslmode="verify-full",
        connect_timeout=15,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SET LOCAL statement_timeout='30s'")
        identity = _fetch_one(
            connection,
            "SELECT current_user AS database_user, current_setting('transaction_read_only') AS transaction_read_only",
        )
        run = _fetch_one(
            connection,
            f"""SELECT id::text AS id, kind, status,
                       {timestamp.format(field='finished_at')} AS finished_at,
                       {timestamp.format(field='data_as_of')} AS data_as_of,
                       write_counts, telegram_message_ids
                  FROM public.analysis_runs WHERE id = %s::uuid""",
            (run_id,),
        )
        counts = _fetch_one(
            connection,
            """SELECT
                 (SELECT count(*) FROM public.market_gateway_requests WHERE run_id = %s::uuid) AS gateway_request_count,
                 (SELECT count(*) FROM public.decision_evaluations WHERE run_id = %s::uuid) AS evaluation_count,
                 (SELECT count(*) FROM public.suggestions WHERE run_id = %s::uuid) AS suggestion_count""",
            (run_id, run_id, run_id),
        )
        alerts = _fetch_all(
            connection,
            """SELECT p.id::text AS id, p.status, p.telegram_message_ids, p.rendered_hash,
                      p.template_version,
                      (SELECT e.status FROM public.market_alert_events e
                        WHERE e.publication_id = p.id ORDER BY e.persisted_at DESC LIMIT 1) AS event_status
                 FROM public.market_publications p
                ORDER BY p.created_at DESC, p.id DESC LIMIT 50""",
        )
        policy = _fetch_one(
            connection,
            "SELECT version FROM public.market_policy_config WHERE active = true ORDER BY version DESC LIMIT 1",
        )
        intelligence_projection = _fetch_one(
            connection,
            """WITH value AS (
                 SELECT public.read_owner_intelligence_v2(25) AS projection
               )
               SELECT projection IS NOT NULL AS projection_present,
                      jsonb_typeof(projection) AS projection_type,
                      COALESCE(projection ? 'run_id', false) AS run_id_present,
                      jsonb_typeof(projection->'run_id') AS run_id_type,
                      projection->>'run_id' AS intelligence_run_id
                 FROM value""",
        )
        intelligence_run_id = validate_intelligence_projection_receipt(
            intelligence_projection,
        )
        holdings = _fetch_all(
            connection,
            f"""SELECT h.ticker, h.shares::text AS shares, h.avg_cost::text AS average_cost,
                       latest.normalized->>'verified_price' AS price,
                       latest.normalized->>'quote_as_of' AS price_as_of,
                       latest.normalized->>'quote_source' AS price_source
                  FROM public.holdings h
                  LEFT JOIN LATERAL (
                    SELECT de.normalized, de.created_at FROM public.decision_evaluations de
                     WHERE de.normalized->>'ticker' = h.ticker
                       AND COALESCE(de.normalized->>'quote_as_of', '') <> ''
                     ORDER BY de.created_at DESC LIMIT 1
                  ) latest ON true
                 ORDER BY h.ticker LIMIT 100""",
        )
        overdue_scheduled_phases = _fetch_all(
            connection,
            "SELECT market_date::text AS market_date, phase, to_char(deadline_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') AS deadline_at FROM public.read_overdue_scheduled_market_phases() LIMIT %s",
            (MAX_SCHEDULED_READINESS_ROWS + 1,),
        )
        dashboard_reports = _fetch_all(
            connection,
            DASHBOARD_REPORT_SOURCE_SQL,
            (list(report_ids),),
        )

    for row in holdings:
        row["price_as_of"] = normalize_receipt_timestamp(row.get("price_as_of"))
        for field in ("shares", "average_cost", "price", "price_source"):
            if row.get(field) is not None:
                row[field] = str(row[field])
    price_times = [row.get("price_as_of") for row in holdings if row.get("price_as_of")]
    canonical_records = []
    canonical_records.extend({
        "kind": "report", "body": row["canonical"],
        "sha256": row["report_hash"],
    } for row in reports)
    return {
        "dashboard": {
            **identity,
            "run": run,
            **{key: int(value) for key, value in counts.items()},
            "alerts": alerts,
            "policy_version": policy.get("version"),
            "intelligence_run_id": intelligence_run_id,
            "holdings": holdings,
            "portfolio_data_as_of": max(price_times) if price_times else None,
            "overdue_scheduled_phases": overdue_scheduled_phases,
            "reports": dashboard_reports,
        },
        "evidence": {
            **evidence_identity,
            "evidence_authority": evidence_authority,
            "reports": reports,
            "canonical_records": canonical_records,
        },
    }


def _publication_state(row: Mapping[str, object]) -> str:
    status = row.get("status")
    ids = row.get("telegram_message_ids")
    if status == "delivered":
        return "delivered" if isinstance(ids, list) and ids else "incomplete"
    if status in {"ready", "sending", "delivery_failed", "delivery_unknown", "suppressed"}:
        return str(status)
    return "incomplete"


def reconcile_source_receipts(
    payloads: Mapping[str, Mapping[str, object]],
    detail: Mapping[str, object],
    source: Mapping[str, object],
    run_id: str,
) -> dict[str, object]:
    """Fail closed unless all visible production claims agree with independent source reads."""
    def fail() -> None:
        raise RuntimeError("dashboard claim differs from its source receipt")

    dashboard = source.get("dashboard")
    evidence = source.get("evidence")
    if not isinstance(dashboard, Mapping) or not isinstance(evidence, Mapping):
        fail()
    scheduled_readiness = scheduled_readiness_receipt(
        dashboard.get("overdue_scheduled_phases"),
    )

    if dashboard.get("database_user") != RUNTIME_ROLE or dashboard.get("transaction_read_only") != "on":
        fail()
    if (
        evidence.get("evidence_database_user") != EVIDENCE_ROLE
        or evidence.get("evidence_transaction_read_only") != "on"
    ):
        fail()
    evidence_authority = evidence.get("evidence_authority")
    if (
        not isinstance(evidence_authority, Mapping)
        or set(evidence_authority) != {
            "status", "connection_id", "read_only", "isolated_guard",
        }
        or evidence_authority.get("status") != "verified"
        or not isinstance(evidence_authority.get("connection_id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", evidence_authority["connection_id"]) is None
        or evidence_authority.get("read_only") is not True
        or evidence_authority.get("isolated_guard") is not False
    ):
        fail()
    detail_data = detail.get("data")
    if not isinstance(detail_data, dict):
        fail()
    visible_run = detail_data.get("run")
    source_run = dashboard.get("run")
    if not isinstance(visible_run, dict) or not isinstance(source_run, dict):
        fail()
    for field in ("id", "kind", "status", "finished_at", "data_as_of"):
        if visible_run.get(field) != source_run.get(field):
            fail()
    if visible_run.get("id") != run_id:
        fail()
    if detail_data.get("write_counts") != source_run.get("write_counts"):
        fail()
    if detail_data.get("telegram_message_ids") != (source_run.get("telegram_message_ids") or []):
        fail()
    requests = detail_data.get("request_receipts")
    evaluations = detail_data.get("evaluations")
    if not isinstance(requests, list) or len(requests) != dashboard.get("gateway_request_count"):
        fail()
    if not isinstance(evaluations, list) or len(evaluations) != dashboard.get("evaluation_count"):
        fail()
    runs_data = payloads.get("/v1/runs", {}).get("data")
    runs = runs_data.get("runs") if isinstance(runs_data, dict) else None
    visible_summary = next((row for row in runs or [] if isinstance(row, dict) and row.get("id") == run_id), None)
    if not isinstance(visible_summary, dict) or visible_summary.get("suggestion_count") != dashboard.get("suggestion_count"):
        fail()

    alerts_data = payloads.get("/v1/alerts", {}).get("data")
    visible_alerts = alerts_data.get("alerts") if isinstance(alerts_data, dict) else None
    source_alerts = dashboard.get("alerts")
    if not isinstance(visible_alerts, list) or not isinstance(source_alerts, list):
        fail()
    source_by_id = {row.get("id"): row for row in source_alerts if isinstance(row, dict)}
    for alert in visible_alerts:
        if not isinstance(alert, dict) or not isinstance(source_by_id.get(alert.get("id")), dict):
            fail()
        row = source_by_id[alert["id"]]
        source_ids = row.get("telegram_message_ids") or []
        source_template_version = row.get("template_version")
        if type(source_template_version) is not int or source_template_version <= 0:
            fail()
        if row.get("status") == "suppressed" and source_ids:
            fail()
        if (
            alert.get("state") != _publication_state(row)
            or alert.get("telegram_message_ids") != source_ids
            or alert.get("rendered_hash") != row.get("rendered_hash")
            or alert.get("template_version") != str(source_template_version)
            or alert.get("event_status") != row.get("event_status")
        ):
            fail()

    system_data = payloads.get("/v1/system", {}).get("data")
    if not isinstance(system_data, dict) or system_data.get("policy_version") != dashboard.get("policy_version"):
        fail()

    portfolio_data = payloads.get("/v1/portfolio", {}).get("data")
    today_data = payloads.get("/v1/today", {}).get("data")
    today_portfolio = today_data.get("portfolio") if isinstance(today_data, dict) else None
    visible_holdings = portfolio_data.get("holdings") if isinstance(portfolio_data, dict) else None
    today_holdings = today_portfolio.get("holdings") if isinstance(today_portfolio, dict) else None
    source_holdings = dashboard.get("holdings")
    if not isinstance(visible_holdings, list) or not isinstance(today_holdings, list) or not isinstance(source_holdings, list):
        fail()
    source_by_ticker = {row.get("ticker"): row for row in source_holdings if isinstance(row, dict)}
    for holding_set in (visible_holdings, today_holdings):
        if {row.get("ticker") for row in holding_set if isinstance(row, dict)} != set(source_by_ticker):
            fail()
        for holding in holding_set:
            if not isinstance(holding, dict):
                fail()
            row = source_by_ticker[holding.get("ticker")]
            for visible_field, source_field in (
                ("shares", "shares"), ("average_cost", "average_cost"),
                ("price_as_of", "price_as_of"), ("price_source", "price_source"),
            ):
                if holding.get(visible_field) != row.get(source_field):
                    fail()
            if holding.get("price") is not None and holding.get("price") != row.get("price"):
                fail()
    if today_portfolio.get("data_as_of") != dashboard.get("portfolio_data_as_of"):
        fail()
    intelligence = payloads.get("/v1/intelligence", {}).get("data")
    reports_view = payloads.get("/v1/reports", {}).get("data")
    intelligence_run_id = dashboard.get("intelligence_run_id")
    if (
        "intelligence_run_id" not in dashboard
        or (
            intelligence_run_id is not None
            and (
                not isinstance(intelligence_run_id, str)
                or UUID_PATTERN.fullmatch(intelligence_run_id) is None
            )
        )
        or not isinstance(intelligence, dict)
        or "run_id" not in intelligence
        or intelligence.get("run_id") != intelligence_run_id
        or not isinstance(reports_view, dict)
    ):
        fail()
    visible_reports = reports_view.get("reports")
    dashboard_reports = dashboard.get("reports")
    evidence_reports = evidence.get("reports")
    if (
        not isinstance(visible_reports, list)
        or not isinstance(dashboard_reports, list)
        or not isinstance(evidence_reports, list)
        or len(dashboard_reports) > 50
        or len(dashboard_reports) != len(evidence_reports)
        or dashboard_reports != [
            {field: row.get(field) for field in REPORT_PUBLIC_SOURCE_FIELDS}
            for row in evidence_reports if isinstance(row, Mapping)
        ]
        or visible_reports != [
            report_summary_source_view(row)
            for row in dashboard_reports if isinstance(row, Mapping)
        ]
        or len(visible_reports) != len(dashboard_reports)
    ):
        fail()
    report_ids: set[object] = set()
    for row in evidence_reports:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("id"), str)
            or UUID_PATTERN.fullmatch(row["id"]) is None
            or row.get("id") in report_ids
            or not isinstance(row.get("run_id"), str)
            or UUID_PATTERN.fullmatch(row["run_id"]) is None
            or not isinstance(row.get("packet_id"), str)
            or UUID_PATTERN.fullmatch(row["packet_id"]) is None
            or not isinstance(row.get("canonical"), Mapping)
            or canonical_sha256(row["canonical"]) != row.get("report_hash")
            or not isinstance(row.get("rendered_text"), str)
            or hashlib.sha256(row["rendered_text"].encode()).hexdigest()
            != row.get("rendered_hash")
        ):
            fail()
        report_ids.add(row["id"])
    return {
        "status": "verified", "database_role": RUNTIME_ROLE,
        "evidence_database_role": EVIDENCE_ROLE, "claims_checked": 11,
        "evidence_reader_authority": dict(evidence_authority),
        "source_reconciliation_receipt": {
            "status": "verified",
            "dashboard": {
                "role": RUNTIME_ROLE,
                "transaction_read_only": True,
            },
            "evidence": {
                "role": EVIDENCE_ROLE,
                "transaction_read_only": True,
                "authority": dict(evidence_authority),
            },
            "canonical_hashes": "verified",
            "run_relationships": "verified",
            "claims_checked": 11,
        },
        "counts": {"reports": len(evidence_reports)},
        "relationships_verified": True, "hashes_verified": True,
        "canonical_records": evidence.get("canonical_records"),
        "scheduled_readiness": scheduled_readiness,
    }


def validate_api_boundary(api_url: str, origin: str) -> tuple[str, str]:
    parsed_api = urlparse(api_url)
    parsed_origin = urlparse(origin)
    if (
        parsed_api.scheme != "https"
        or not parsed_api.hostname
        or not parsed_api.hostname.endswith(".supabase.co")
        or parsed_api.path != "/functions/v1/owner-dashboard-api"
        or parsed_api.params
        or parsed_api.query
        or parsed_api.fragment
    ):
        raise ValueError("dashboard API URL is not canonical")
    if (
        parsed_origin.scheme != "https"
        or not parsed_origin.hostname
        or parsed_origin.path
        or parsed_origin.params
        or parsed_origin.query
        or parsed_origin.fragment
        or origin != f"https://{parsed_origin.netloc}"
    ):
        raise ValueError("dashboard origin is not exact")
    return api_url, origin


def _boundary_is_exact(actual: object, expected: Mapping[str, object]) -> bool:
    return (
        isinstance(actual, dict)
        and set(actual) == set(expected)
        and all(
            type(actual[key]) is type(expected_value) and actual[key] == expected_value
            for key, expected_value in expected.items()
        )
    )


def validate_owner_payloads(payloads: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    if set(payloads) != set(CANARY_ROUTES):
        raise RuntimeError("owner canary route set is incomplete")
    for route, payload in payloads.items():
        if payload.get("contract_version") != 1:
            raise RuntimeError(f"{route} contract receipt is invalid")
        if payload.get("freshness") not in {"fresh", "stale", "partial", "unavailable"}:
            raise RuntimeError(f"{route} freshness receipt is invalid")
        if "data_as_of" not in payload or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"{route} receipt metadata is incomplete")
        data = payload["data"]
        boundary_present = "boundaries" in data
        boundaries = data.get("boundaries")
        expected_boundaries = ROUTE_BOUNDARIES.get(route)
        if expected_boundaries is None:
            if boundary_present:
                raise RuntimeError(f"unexpected boundary receipt for {route}")
        elif not boundary_present or not _boundary_is_exact(boundaries, expected_boundaries):
            raise RuntimeError(f"immutable route boundary changed for {route}")

    today = payloads["/v1/today"]["data"]
    portfolio = today.get("portfolio") if isinstance(today, dict) else None
    if not isinstance(portfolio, dict) or not {"data_as_of", "market_state", "price_sources"} <= set(portfolio):
        raise RuntimeError("portfolio price context receipt is missing")
    runs = payloads["/v1/runs"]["data"]
    completed_runs = runs.get("runs", []) if isinstance(runs, dict) else []
    if not any(isinstance(run, dict) and run.get("status") == "completed" for run in completed_runs):
        raise RuntimeError("a completed run receipt is unavailable")
    alerts = payloads["/v1/alerts"]["data"]
    if isinstance(alerts, dict):
        for alert in alerts.get("alerts", []):
            if isinstance(alert, dict) and alert.get("state") == "delivered" and not alert.get("telegram_message_ids"):
                raise RuntimeError("Telegram delivery claim has no message receipt")
    return {
        "route_count": len(payloads),
        "financial_write_routes": 0,
        "friend_invitations": "disabled",
        "brokerage_authority": "none",
    }


def _request(method: str, url: str, headers: Mapping[str, str]):
    request = Request(url, method=method, headers=dict(headers))
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def run_http_canary(
    api_url: str,
    origin: str,
    owner_access_token: str,
    non_owner_access_token: str | None = None,
    *,
    requester: Callable[[str, str, Mapping[str, str]], tuple[int, Mapping[str, str], bytes]] = _request,
    source_reader: Callable[[str], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    validate_api_boundary(api_url, origin)
    if not owner_access_token or "\n" in owner_access_token or "\r" in owner_access_token:
        raise ValueError("an owner access token is required")
    denied_status, denied_headers, denied_body = requester(CANARY_METHOD, f"{api_url}/v1/meta", {"origin": origin})
    denied_headers = {key.lower(): value for key, value in denied_headers.items()}
    try:
        denied = json.loads(denied_body)
    except json.JSONDecodeError as error:
        raise RuntimeError("unauthenticated denial body is malformed") from error
    if denied_status != 401 or denied.get("error", {}).get("code") != "unauthorized":
        raise RuntimeError("unauthenticated request was not denied")
    if denied_headers.get("access-control-allow-origin") != origin:
        raise RuntimeError("unauthenticated response CORS is not exact")

    non_owner_status = None
    if non_owner_access_token is not None:
        non_owner_status, non_owner_headers, non_owner_body = requester(
            CANARY_METHOD,
            f"{api_url}/v1/meta",
            {"origin": origin, "authorization": f"Bearer {non_owner_access_token}"},
        )
        non_owner_headers = {key.lower(): value for key, value in non_owner_headers.items()}
        try:
            non_owner = json.loads(non_owner_body)
        except json.JSONDecodeError as error:
            raise RuntimeError("non-owner denial body is malformed") from error
        if non_owner_status != 403 or non_owner.get("error", {}).get("code") != "owner_only":
            raise RuntimeError("non-owner request was not denied")
        if non_owner_headers.get("access-control-allow-origin") != origin:
            raise RuntimeError("non-owner response CORS is not exact")

    if source_reader is None:
        raise RuntimeError("independent source receipt reader is required")

    for source_attempt in range(2):
        payloads = {}
        for route in CANARY_ROUTES:
            status, headers, body = requester(
                CANARY_METHOD,
                f"{api_url}{route}",
                {"origin": origin, "authorization": f"Bearer {owner_access_token}"},
            )
            headers = {key.lower(): value for key, value in headers.items()}
            if status != 200:
                code = "unknown"
                if len(body) <= 4096:
                    try:
                        error = json.loads(body).get("error", {})
                        candidate = error.get("code") if isinstance(error, dict) else None
                        if isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate):
                            code = candidate
                    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                        pass
                raise RuntimeError(f"owner GET failed for {route} (status {status}, code {code})")
            if headers.get("access-control-allow-origin") != origin or headers.get("cache-control") != "no-store":
                raise RuntimeError(f"owner headers are unsafe for {route}")
            try:
                payloads[route] = json.loads(body)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"owner payload is malformed for {route}") from error
        validated = validate_owner_payloads(payloads)
        runs = payloads["/v1/runs"]["data"]["runs"]
        completed = next(run for run in runs if isinstance(run, dict) and run.get("status") == "completed")
        run_id = completed.get("id")
        if not isinstance(run_id, str) or len(run_id) > 64:
            raise RuntimeError("completed run identifier is malformed")
        detail_status, detail_headers, detail_body = requester(
            CANARY_METHOD,
            f"{api_url}/v1/runs/{run_id}",
            {"origin": origin, "authorization": f"Bearer {owner_access_token}"},
        )
        detail_headers = {key.lower(): value for key, value in detail_headers.items()}
        if (
            detail_status != 200
            or detail_headers.get("access-control-allow-origin") != origin
            or detail_headers.get("cache-control") != "no-store"
        ):
            raise RuntimeError("completed run detail GET failed")
        try:
            detail = json.loads(detail_body)
        except json.JSONDecodeError as error:
            raise RuntimeError("completed run detail is malformed") from error
        detail_data = detail.get("data") if isinstance(detail, dict) else None
        if (
            detail.get("contract_version") != 1
            or not isinstance(detail_data, dict)
            or not isinstance(detail_data.get("write_counts"), dict)
            or detail_data.get("incomplete_stages")
            or not isinstance(detail_data.get("run"), dict)
            or detail_data["run"].get("id") != run_id
            or detail_data["run"].get("status") != "completed"
            or not detail_data["run"].get("finished_at")
        ):
            raise RuntimeError("completed run detail lacks a complete receipt chain")
        reports_data = payloads["/v1/reports"].get("data")
        visible_reports = (
            reports_data.get("reports") if isinstance(reports_data, dict) else None
        )
        report_ids = [
            row.get("id") for row in visible_reports or [] if isinstance(row, dict)
        ]
        if (
            not isinstance(visible_reports, list)
            or len(report_ids) != len(visible_reports)
            or len(report_ids) > 50
            or any(
                not isinstance(report_id, str)
                or UUID_PATTERN.fullmatch(report_id) is None
                for report_id in report_ids
            )
            or len(set(report_ids)) != len(report_ids)
        ):
            raise RuntimeError("visible report identifiers are malformed")
        try:
            source_receipt = reconcile_source_receipts(
                payloads, detail, source_reader(run_id), run_id,
            )
            break
        except RuntimeError as error:
            if (
                source_attempt == 0
                and str(error) == "dashboard claim differs from its source receipt"
            ):
                continue
            raise
    return {
        "status": "verified",
        "unauthenticated_status": denied_status,
        "non_owner_status": non_owner_status,
        "owner_route_count": validated["route_count"] + 1,
        "run_id_digest": hashlib.sha256(run_id.encode()).hexdigest()[:16],
        "method": CANARY_METHOD,
        "financial_write_routes": 0,
        "source_reconciliation": source_receipt["status"],
        "source_database_role": source_receipt["database_role"],
        "evidence_database_role": source_receipt["evidence_database_role"],
        "evidence_reader_authority": source_receipt["evidence_reader_authority"],
        "source_reconciliation_receipt": source_receipt[
            "source_reconciliation_receipt"
        ],
        "scheduled_readiness": source_receipt["scheduled_readiness"],
        "friend_invitations": "disabled",
        "brokerage_authority": "none",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--deployment-receipt", type=Path)
    parser.add_argument("--auth-config-receipt", type=Path, required=True)
    arguments = parser.parse_args()
    auth_configuration = load_deployment_auth_configuration_receipt(arguments.auth_config_receipt)
    database_url = os.environ.get("DASHBOARD_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DASHBOARD_DATABASE_URL is required")
    evidence_database_url = os.environ.get("RELEASE_READONLY_DATABASE_URL", "").strip()
    if not evidence_database_url:
        raise SystemExit("RELEASE_READONLY_DATABASE_URL is required")
    owner_email = os.environ.get("DASHBOARD_OWNER_EMAIL", "").strip()
    owner_user_id = os.environ.get("DASHBOARD_OWNER_USER_ID", "").strip()
    non_owner_email = os.environ.get("DASHBOARD_NON_OWNER_EMAIL", "").strip()
    non_owner_user_id = os.environ.get("DASHBOARD_NON_OWNER_USER_ID", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    parsed_api = urlparse(arguments.api_url)
    project_url = f"{parsed_api.scheme}://{parsed_api.netloc}"
    auth_inventory = verify_auth_canary_inventory(
        project_url, owner_email, owner_user_id, non_owner_email, non_owner_user_id, service_key,
    )
    tokens = []
    try:
        token = obtain_ephemeral_existing_user_access_token(
            project_url, owner_email, arguments.origin, service_key, publishable_key,
        )
        tokens.append(token)
        non_owner_token = obtain_ephemeral_existing_user_access_token(
            project_url, non_owner_email, arguments.origin, service_key, publishable_key,
        )
        tokens.append(non_owner_token)
        receipt = run_http_canary(
            arguments.api_url, arguments.origin, token, non_owner_token,
            source_reader=lambda run_id: collect_source_receipts(
                database_url, evidence_database_url, arguments.api_url, run_id,
            ),
        )
    finally:
        cleanup_error = None
        for access_token in reversed(tokens):
            try:
                revoke_ephemeral_owner_session(project_url, access_token, publishable_key)
            except Exception as error:
                cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise RuntimeError("ephemeral deployment canary session revocation failed") from cleanup_error
    receipt["auth_inventory"] = auth_inventory
    receipt["owner_session"] = "revoked"
    receipt["non_owner_session"] = "revoked"
    receipt["auth_configuration"] = auth_configuration
    if bool(arguments.candidate_sha) != bool(arguments.deployment_receipt):
        raise SystemExit("candidate SHA and deployment receipt must be supplied together")
    if arguments.candidate_sha and arguments.deployment_receipt:
        try:
            deployment = json.loads(arguments.deployment_receipt.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit("deployment receipt is unavailable or malformed") from error
        if not isinstance(deployment, dict):
            raise SystemExit("deployment receipt must be a JSON object")
        receipt["artifact_verification"] = verify_release_artifact_receipts(
            arguments.candidate_sha, deployment, candidate_migration_manifest(),
        )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
