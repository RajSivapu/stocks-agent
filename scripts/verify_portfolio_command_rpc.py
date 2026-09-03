"""Read-only verification for the portfolio command security boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg


EXPECTED_API_FUNCTIONS = {
    "api.preview_portfolio_command(jsonb)",
    "api.confirm_portfolio_command(uuid,text)",
    "api.cancel_portfolio_command(uuid,text)",
}


def verify_database(connection: psycopg.Connection) -> dict[str, int]:
    functions = connection.execute(
        """
        SELECT p.oid::regprocedure::text, p.prosecdef,
               p.proowner::regrole::text, p.proconfig
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'api'
          AND p.proname LIKE '%portfolio_command'
        """
    ).fetchall()
    if {row[0] for row in functions} != EXPECTED_API_FUNCTIONS:
        raise RuntimeError("portfolio command API allow-list mismatch")
    if any(
        not security_definer
        or owner != "stock_agent_migration_owner"
        or "search_path=pg_catalog" not in (settings or [])
        for _, security_definer, owner, settings in functions
    ):
        raise RuntimeError("portfolio command API hardening mismatch")

    executable_internal = connection.execute(
        """
        SELECT count(*)
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'app'
          AND p.proname LIKE '%portfolio_command%'
          AND (
            has_function_privilege('anon', p.oid, 'EXECUTE')
            OR has_function_privilege('authenticated', p.oid, 'EXECUTE')
            OR has_function_privilege('service_role', p.oid, 'EXECUTE')
          )
        """
    ).fetchone()[0]
    if executable_internal:
        raise RuntimeError("private portfolio command function is executable")

    invalid_states = connection.execute(
        """
        SELECT count(*)
        FROM app.portfolio_commands
        WHERE (status = 'submitted' AND preview_digest IS NOT NULL)
           OR (status <> 'submitted' AND preview_digest IS NULL)
           OR (status IN ('confirmed','applied') AND confirmed_at IS NULL)
           OR (status = 'applied' AND (applied_at IS NULL OR result IS NULL))
        """
    ).fetchone()[0]
    if invalid_states:
        raise RuntimeError("invalid portfolio command lifecycle state")

    duplicate_trade_effects = connection.execute(
        """
        SELECT count(*)
        FROM (
          SELECT c.id
          FROM app.portfolio_commands AS c
          LEFT JOIN app.transactions AS t
            ON t.owner_id = c.owner_id AND t.command_id = c.id
          WHERE c.status = 'applied'
            AND c.operation IN ('buy','sell','sell_all')
            AND NOT c.normalized_input ? 'legacy_migrated'
          GROUP BY c.id
          HAVING count(*) <> 1
        ) AS invalid
        """
    ).fetchone()[0]
    if duplicate_trade_effects:
        raise RuntimeError("trade command has a duplicate or missing ledger effect")

    duplicate_correction_effects = connection.execute(
        """
        SELECT count(*)
        FROM (
          SELECT c.id
          FROM app.portfolio_commands AS c
          LEFT JOIN app.transactions AS t
            ON t.owner_id = c.owner_id AND t.command_id = c.id
          WHERE c.status = 'applied'
            AND c.operation = 'correct_transaction'
            AND NOT c.normalized_input ? 'legacy_migrated'
          GROUP BY c.id
          HAVING count(*) FILTER (WHERE t.event_type = 'void') <> 1
             OR count(*) FILTER (WHERE t.event_type = 'trade') <> 1
        ) AS invalid
        """
    ).fetchone()[0]
    if duplicate_correction_effects:
        raise RuntimeError("correction command ledger effects are invalid")

    counts = connection.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE status = 'applied')
        FROM app.portfolio_commands
        """
    ).fetchone()
    return {"commands": counts[0], "applied": counts[1]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.getenv("STAGING_DATABASE_URL"),
        help="Staging session-mode PostgreSQL URL; defaults to STAGING_DATABASE_URL.",
    )
    args = parser.parse_args()
    if not args.database_url:
        print("FAIL: STAGING_DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(args.database_url) as connection:
            with connection.transaction(force_rollback=True):
                connection.execute("SET LOCAL statement_timeout = '5s'")
                summary = verify_database(connection)
        print(json.dumps({"ok": True, **summary}, sort_keys=True))
        return 0
    except Exception as error:
        print(
            json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
