"""Print one bounded weekly portfolio-audit packet; perform no writes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.rows import dict_row

from lib.weekly_audit import build_packet


_PROJECT_REF = re.compile(r"^[a-z0-9]{20}$")
_RUNTIME_ROLE = "stock_agent_dashboard_runtime"
_QUERIES = {
    "holdings": """
        SELECT ticker, shares, avg_cost, bucket, stop, target
          FROM public.holdings ORDER BY ticker
    """,
    "transactions": """
        SELECT id, ts, ticker, side, qty, price, source, executed_on
          FROM public.transactions ORDER BY ts DESC LIMIT 50
    """,
    "suggestions": """
        SELECT id, ts, date, ticker, action, bucket, confidence, run_id,
               evaluation_id, decision_source
          FROM public.suggestions ORDER BY ts DESC LIMIT 50
    """,
    "grades": """
        SELECT id, suggestion_id, graded_at, result, price_then, price_later,
               horizon_days, note, benchmark_ticker, stock_return_pct,
               benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
               entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at,
               coverage_status, horizon_sessions, policy_version, final_action,
               direction_success
          FROM public.suggestion_grades ORDER BY graded_at DESC LIMIT 100
    """,
    "lessons": """
        SELECT id, entry_date, category, content, created_at
          FROM public.lessons ORDER BY entry_date DESC, id DESC LIMIT 40
    """,
    "snapshots": """
        SELECT id, snap_date, ticker, close, day_move_pct, rsi14, sma50, sma200,
               macd_hist
          FROM public.daily_snapshots ORDER BY snap_date DESC, id DESC LIMIT 150
    """,
    "evaluations": """
        SELECT id, request_id, run_id, candidate_id, policy_version, raw_action,
               final_action, policy_status, reason_codes, created_at
          FROM public.decision_evaluations ORDER BY created_at DESC LIMIT 50
    """,
    "publications": """
        SELECT id, idempotency_key, run_id, market_date, phase, kind,
               template_version, status, telegram_message_ids, attempt_count,
               created_at, updated_at
          FROM public.market_publications ORDER BY created_at DESC LIMIT 50
    """,
}


def weekly_audit_database_url(environ, project_ref):
    """Accept only the existing least-privilege dashboard runtime login."""
    value = str(environ.get("DASHBOARD_DATABASE_URL", "")).strip()
    parsed = urlparse(value)
    username = unquote(parsed.username or "")
    if (
        not _PROJECT_REF.fullmatch(project_ref)
        or parsed.scheme not in {"postgres", "postgresql"}
        or username != f"{_RUNTIME_ROLE}.{project_ref}"
        or not parsed.password
        or not re.fullmatch(
            r"[a-z0-9-]+\.pooler\.supabase\.com",
            parsed.hostname or "",
        )
        or parsed.port != 5432
        or parsed.path != "/postgres"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "DASHBOARD_DATABASE_URL must use the project-matched scoped runtime role"
        )
    return value


def read_weekly_audit_inputs(connection):
    """Execute the packet read in one database-enforced read-only transaction."""
    connection.execute("SET TRANSACTION READ ONLY")
    return {
        name: connection.execute(query).fetchall()
        for name, query in _QUERIES.items()
    }


def main():
    project_ref = os.environ.get("SUPABASE_PROJECT_REF", "").strip()
    try:
        database_url = weekly_audit_database_url(os.environ, project_ref)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        inputs = read_weekly_audit_inputs(connection)
    packet = build_packet(
        **inputs,
        generated_at=datetime.now(timezone.utc),
    )
    print(json.dumps(packet, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
