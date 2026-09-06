#!/usr/bin/env python3
"""GET-only production canary for the owner-only dashboard API."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.provision_owner_dashboard_auth import (
    validate_configuration as validate_auth_admin_configuration,
    validate_email_otp_configuration,
)
from lib.intelligence.canonical import EVENT_CANONICAL_SQL, RANKING_CANONICAL_SQL


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
RELEASE_FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")
RUNTIME_ROLE = "stock_agent_dashboard_runtime"
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
        "sha256": migration_statements_sha256(normalize_migration_statements(path.read_text(encoding="utf-8"))),
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
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _auth_request,
) -> str:
    """Generate and directly verify an owner link without sending an email or logging the token."""
    project_url, owner_email, service_key = validate_auth_admin_configuration(
        project_url, owner_email, service_key,
    )
    validate_api_boundary(f"{project_url}/functions/v1/owner-dashboard-api", redirect_origin)
    if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{24,128}", publishable_key):
        raise ValueError("Supabase publishable key is invalid")
    admin_headers = {
        "apikey": service_key,
        "authorization": f"Bearer {service_key}",
        "content-type": "application/json",
    }
    link_body = json.dumps({
        "type": "magiclink",
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
    verify_body = json.dumps({"type": "magiclink", "token_hash": token_hash}, separators=(",", ":")).encode()
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


def _fetch_one(connection, query: str, parameters: tuple[object, ...] = ()) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
    return dict(row) if row else {}


def _fetch_all(connection, query: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]


def collect_source_receipts(database_url: str, api_url: str, run_id: str) -> dict[str, object]:
    """Read the source rows through the scoped dashboard login in a read-only transaction."""
    validate_source_database_url(database_url, api_url)
    if not UUID_PATTERN.fullmatch(run_id):
        raise ValueError("completed run identifier is malformed")
    timestamp = "to_char({field} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
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
        intelligence_runs = _fetch_all(connection,
            "SELECT id::text AS id, phase, market_date::text AS market_date, policy_version FROM public.market_intelligence_runs WHERE id=%s::uuid",
            (run_id,))
        intelligence_events = _fetch_all(connection,
            f"""SELECT id::text AS id, run_id::text AS run_id, content_hash,
                      {EVENT_CANONICAL_SQL} AS canonical
                 FROM public.market_events WHERE run_id=%s::uuid ORDER BY id""", (run_id,))
        intelligence_rankings = _fetch_all(connection,
            f"""SELECT id::text AS id, run_id::text AS run_id, event_id::text AS event_id, content_hash,
                      {RANKING_CANONICAL_SQL} AS canonical
                 FROM public.market_candidate_rankings WHERE run_id=%s::uuid ORDER BY rank""", (run_id,))
        intelligence_packets = _fetch_all(connection,
            "SELECT id::text AS id, run_id::text AS run_id, packet_hash, candidate_count, evidence_count, packet AS canonical FROM public.market_evidence_packets WHERE run_id=%s::uuid", (run_id,))
        reports = _fetch_all(connection,
            "SELECT id::text AS id, run_id::text AS run_id, packet_id::text AS packet_id, report_hash, rendered_hash, report AS canonical, rendered_text FROM public.market_reports WHERE run_id=%s::uuid ORDER BY created_at", (run_id,))
        report_publications = _fetch_all(connection, REPORT_PUBLICATION_SQL, (run_id,))
        overdue_scheduled_phases = _fetch_all(
            connection,
            "SELECT market_date::text AS market_date, phase, to_char(deadline_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') AS deadline_at FROM public.read_overdue_scheduled_market_phases()",
        )
    for row in holdings:
        row["price_as_of"] = normalize_receipt_timestamp(row.get("price_as_of"))
        for field in ("shares", "average_cost", "price", "price_source"):
            if row.get(field) is not None:
                row[field] = str(row[field])
    price_times = [row.get("price_as_of") for row in holdings if row.get("price_as_of")]
    canonical_records = []
    for kind, rows, field in (
        ("event", intelligence_events, "content_hash"),
        ("ranking", intelligence_rankings, "content_hash"),
        ("packet", intelligence_packets, "packet_hash"),
        ("report", reports, "report_hash"),
    ):
        canonical_records.extend({"kind": kind, "body": row["canonical"], "sha256": row[field]} for row in rows)
    canonical_records.extend({
        "kind": "publication", "body": row["canonical"], "sha256": canonical_sha256(row["canonical"]),
    } for row in report_publications)
    return {
        **identity,
        "run": run,
        **{key: int(value) for key, value in counts.items()},
        "alerts": alerts,
        "policy_version": policy.get("version"),
        "holdings": holdings,
        "portfolio_data_as_of": max(price_times) if price_times else None,
        "intelligence_runs": intelligence_runs,
        "intelligence_events": intelligence_events,
        "intelligence_rankings": intelligence_rankings,
        "intelligence_packets": intelligence_packets,
        "reports": reports,
        "report_publications": report_publications,
        "canonical_records": canonical_records,
        "overdue_scheduled_phases": overdue_scheduled_phases,
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

    overdue = source.get("overdue_scheduled_phases")
    if not isinstance(overdue, list):
        fail()
    if overdue:
        raise RuntimeError("overdue scheduled phase is missing a completed or suppressed receipt")

    if source.get("database_user") != RUNTIME_ROLE or source.get("transaction_read_only") != "on":
        fail()
    detail_data = detail.get("data")
    if not isinstance(detail_data, dict):
        fail()
    visible_run = detail_data.get("run")
    source_run = source.get("run")
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
    if not isinstance(requests, list) or len(requests) != source.get("gateway_request_count"):
        fail()
    if not isinstance(evaluations, list) or len(evaluations) != source.get("evaluation_count"):
        fail()
    runs_data = payloads.get("/v1/runs", {}).get("data")
    runs = runs_data.get("runs") if isinstance(runs_data, dict) else None
    visible_summary = next((row for row in runs or [] if isinstance(row, dict) and row.get("id") == run_id), None)
    if not isinstance(visible_summary, dict) or visible_summary.get("suggestion_count") != source.get("suggestion_count"):
        fail()

    alerts_data = payloads.get("/v1/alerts", {}).get("data")
    visible_alerts = alerts_data.get("alerts") if isinstance(alerts_data, dict) else None
    source_alerts = source.get("alerts")
    if not isinstance(visible_alerts, list) or not isinstance(source_alerts, list):
        fail()
    source_by_id = {row.get("id"): row for row in source_alerts if isinstance(row, dict)}
    for alert in visible_alerts:
        if not isinstance(alert, dict) or not isinstance(source_by_id.get(alert.get("id")), dict):
            fail()
        row = source_by_id[alert["id"]]
        source_ids = row.get("telegram_message_ids") or []
        if row.get("status") == "suppressed" and source_ids:
            fail()
        if (
            alert.get("state") != _publication_state(row)
            or alert.get("telegram_message_ids") != source_ids
            or alert.get("rendered_hash") != row.get("rendered_hash")
            or alert.get("template_version") != row.get("template_version")
            or alert.get("event_status") != row.get("event_status")
        ):
            fail()

    system_data = payloads.get("/v1/system", {}).get("data")
    if not isinstance(system_data, dict) or system_data.get("policy_version") != source.get("policy_version"):
        fail()

    portfolio_data = payloads.get("/v1/portfolio", {}).get("data")
    today_data = payloads.get("/v1/today", {}).get("data")
    today_portfolio = today_data.get("portfolio") if isinstance(today_data, dict) else None
    visible_holdings = portfolio_data.get("holdings") if isinstance(portfolio_data, dict) else None
    today_holdings = today_portfolio.get("holdings") if isinstance(today_portfolio, dict) else None
    source_holdings = source.get("holdings")
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
    if today_portfolio.get("data_as_of") != source.get("portfolio_data_as_of"):
        fail()
    chains = {key: source.get(key) for key in (
        "intelligence_runs", "intelligence_events", "intelligence_rankings",
        "intelligence_packets", "reports", "report_publications",
    )}
    if any(not isinstance(rows, list) or not rows for rows in chains.values()):
        fail()
    if len(chains["intelligence_runs"]) != 1 or chains["intelligence_runs"][0].get("id") != run_id:
        fail()
    if any(row.get("run_id") != run_id or not isinstance(row.get("canonical"), Mapping)
           or canonical_sha256(row["canonical"]) != row.get("content_hash") for row in chains["intelligence_events"]):
        fail()
    packet = chains["intelligence_packets"][0]
    if (packet.get("run_id") != run_id or not isinstance(packet.get("canonical"), Mapping)
            or canonical_sha256(packet["canonical"]) != packet.get("packet_hash")):
        fail()
    event_ids = {row.get("id") for row in chains["intelligence_events"]}
    if any(row.get("run_id") != run_id or row.get("event_id") not in event_ids
           or not isinstance(row.get("canonical"), Mapping)
           or canonical_sha256(row["canonical"]) != row.get("content_hash")
           for row in chains["intelligence_rankings"]):
        fail()
    report_ids = set()
    for row in chains["reports"]:
        if (row.get("run_id") != run_id or row.get("packet_id") != packet.get("id")
                or not isinstance(row.get("canonical"), Mapping)
                or canonical_sha256(row["canonical"]) != row.get("report_hash")
                or not isinstance(row.get("rendered_text"), str)
                or hashlib.sha256(row["rendered_text"].encode()).hexdigest() != row.get("rendered_hash")):
            fail()
        report_ids.add(row.get("id"))
    for row in chains["report_publications"]:
        if (row.get("run_id") != run_id or row.get("report_id") not in report_ids
                or row.get("status") not in {"delivered", "suppressed"}
                or not isinstance(row.get("canonical"), Mapping)):
            fail()
        ids = row.get("telegram_message_ids")
        if row.get("status") == "delivered" and (not isinstance(ids, list) or not ids
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids)):
            fail()
        if row.get("status") == "suppressed" and ids != []:
            fail()
        if row.get("status") == "suppressed" and (not isinstance(row.get("suppression_reason"), str) or not row["suppression_reason"].strip()):
            fail()
    report = chains["reports"][-1]
    publication = next((row for row in chains["report_publications"] if row.get("report_id") == report.get("id")), None)
    if not isinstance(publication, Mapping):
        fail()
    intelligence = payloads.get("/v1/intelligence", {}).get("data")
    reports_view = payloads.get("/v1/reports", {}).get("data")
    if not isinstance(intelligence, dict) or intelligence.get("run_id") != run_id or not isinstance(reports_view, dict):
        fail()
    visible_reports = reports_view.get("reports")
    if not isinstance(visible_reports, list) or not report_ids.issubset({row.get("id") for row in visible_reports if isinstance(row, dict)}):
        fail()
    return {
        "status": "verified", "database_role": RUNTIME_ROLE, "claims_checked": 11,
        "counts": {"runs": len(chains["intelligence_runs"]), "events": len(chains["intelligence_events"]),
                   "rankings": len(chains["intelligence_rankings"]), "packets": len(chains["intelligence_packets"]),
                   "reports": len(chains["reports"]), "report_publications": len(chains["report_publications"])},
        "relationships_verified": True, "hashes_verified": True,
        "canonical_records": source.get("canonical_records"),
        "scheduled_chain": {
            "run_id": run_id,
            "intelligence_run_id": chains["intelligence_runs"][0]["id"],
            "packet_id": packet["id"], "packet_hash": packet["packet_hash"],
            "report_id": report["id"], "report_hash": report["report_hash"],
            "publication_receipt": {
                "status": "accepted_by_telegram" if publication["status"] == "delivered" else "suppressed",
                "telegram_message_ids": publication["telegram_message_ids"],
                **({"original_delivery_receipt": {"telegram_message_ids": publication["telegram_message_ids"], "telegram_accepted_at": publication["telegram_accepted_at"]}} if publication["status"] == "delivered" else {"suppression_reason": publication["suppression_reason"]}),
            },
        },
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


def validate_owner_payloads(payloads: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    if set(payloads) != set(CANARY_ROUTES):
        raise RuntimeError("owner canary route set is incomplete")
    observed_boundaries = []
    for route, payload in payloads.items():
        if payload.get("contract_version") != 1:
            raise RuntimeError(f"{route} contract receipt is invalid")
        if payload.get("freshness") not in {"fresh", "stale", "partial", "unavailable"}:
            raise RuntimeError(f"{route} freshness receipt is invalid")
        if "data_as_of" not in payload or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"{route} receipt metadata is incomplete")
        data = payload["data"]
        boundaries = data.get("boundaries")
        if boundaries is not None:
            if boundaries != BOUNDARIES:
                raise RuntimeError("immutable product boundaries changed")
            observed_boundaries.append(boundaries)
    if not observed_boundaries:
        raise RuntimeError("immutable product boundary receipt is missing")

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

    payloads = {}
    for route in CANARY_ROUTES:
        status, headers, body = requester(
            CANARY_METHOD,
            f"{api_url}{route}",
            {"origin": origin, "authorization": f"Bearer {owner_access_token}"},
        )
        headers = {key.lower(): value for key, value in headers.items()}
        if status != 200:
            raise RuntimeError(f"owner GET failed for {route}")
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
    if source_reader is None:
        raise RuntimeError("independent source receipt reader is required")
    source_receipt = reconcile_source_receipts(payloads, detail, source_reader(run_id), run_id)
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
    token = os.environ.get("DASHBOARD_OWNER_ACCESS_TOKEN", "").strip()
    if not token:
        parsed_api = urlparse(arguments.api_url)
        project_url = f"{parsed_api.scheme}://{parsed_api.netloc}"
        token = obtain_ephemeral_owner_access_token(
            project_url,
            os.environ.get("DASHBOARD_OWNER_EMAIL", ""),
            arguments.origin,
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
        )
    database_url = os.environ.get("DASHBOARD_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DASHBOARD_DATABASE_URL is required")
    non_owner_token = os.environ.get("DASHBOARD_NON_OWNER_ACCESS_TOKEN", "").strip()
    if not non_owner_token:
        raise SystemExit("DASHBOARD_NON_OWNER_ACCESS_TOKEN is required")
    receipt = run_http_canary(
        arguments.api_url, arguments.origin, token, non_owner_token,
        source_reader=lambda run_id: collect_source_receipts(database_url, arguments.api_url, run_id),
    )
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
