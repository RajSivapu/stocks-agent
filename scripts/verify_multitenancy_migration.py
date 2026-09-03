#!/usr/bin/env python3
"""Rollback-only verification for the single-owner tenant migration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import psycopg


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_single_owner_to_tenant import (
    MigrationRejected,
    authorize_target,
    migrate_connection,
)


FOUNDATION_SQL = ROOT / "supabase/migrations/20260905000000_multitenancy_foundation.sql"
SYNTHETIC_OWNER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SYNTHETIC_OWNER_EMAIL = "tst-migration@invalid.example"


def _postgres_bin(name: str) -> str:
    configured = os.environ.get("POSTGRES_BIN")
    candidates = [
        Path(configured) / name if configured else None,
        Path("/opt/homebrew/opt/postgresql@17/bin") / name,
        Path("/usr/local/opt/postgresql@17/bin") / name,
    ]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    raise MigrationRejected("PostgreSQL 17 test binaries are unavailable")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class DisposablePostgres:
    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._data: Path | None = None
        self._pg_ctl = _postgres_bin("pg_ctl")
        self.connection: psycopg.Connection | None = None

    def __enter__(self) -> Self:
        self._temporary = tempfile.TemporaryDirectory(prefix="stock-agent-migration-")
        directory = Path(self._temporary.name)
        self._data = directory / "data"
        log = directory / "postgres.log"
        port = _free_port()
        subprocess.run(
            [
                _postgres_bin("initdb"),
                "--no-sync",
                "--auth-local=trust",
                "--auth-host=trust",
                "--username=postgres",
                str(self._data),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                self._pg_ctl,
                "-D",
                str(self._data),
                "-l",
                str(log),
                "-o",
                f"-F -h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.connection = psycopg.connect(
            dbname="postgres",
            user="postgres",
            host="127.0.0.1",
            port=port,
            autocommit=True,
        )
        self.connection.execute("CREATE ROLE anon NOLOGIN")
        self.connection.execute("CREATE ROLE authenticated NOLOGIN")
        self.connection.execute("CREATE ROLE service_role NOLOGIN")
        self.connection.execute("CREATE SCHEMA auth")
        self.connection.execute(
            "CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE)"
        )
        self.connection.execute((ROOT / "sql/legacy_schema.sql").read_text())
        return self

    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self.connection is not None:
            self.connection.close()
        if self._data is not None:
            subprocess.run(
                [self._pg_ctl, "-D", str(self._data), "-m", "fast", "-w", "stop"],
                check=False,
                capture_output=True,
                text=True,
            )
        if self._temporary is not None:
            self._temporary.cleanup()


def _seed_synthetic_records(connection: psycopg.Connection) -> None:
    run_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    request_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    evaluation_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    connection.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
        (SYNTHETIC_OWNER_ID, SYNTHETIC_OWNER_EMAIL),
    )
    connection.execute(
        "INSERT INTO public.holdings (ticker, shares, avg_cost, bucket) VALUES ('TSTAAA', 2, 10, 'growth')"
    )
    connection.execute(
        "INSERT INTO public.transactions (ticker, side, qty, price) VALUES ('TSTAAA', 'buy', 2, 10)"
    )
    connection.execute(
        "INSERT INTO public.analysis_runs (id, kind, status) VALUES (%s, 'pre-market', 'completed')",
        (run_id,),
    )
    connection.execute(
        """
        INSERT INTO public.market_gateway_requests
          (request_id, operation, run_id, status, lease_token, finished_at)
        VALUES (%s, 'finish_run', %s, %s, %s, now())
        """,
        (
            request_id,
            run_id,
            "completed",
            UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        ),
    )
    connection.execute(
        "INSERT INTO public.market_policy_config (version, config, active) VALUES (1, '{}', true)"
    )
    connection.execute(
        """
        INSERT INTO public.decision_evaluations
          (id, request_id, run_id, candidate_id, policy_version, input_digest,
           raw_action, final_action, policy_status)
        VALUES (%s, %s, %s, %s, 1, %s, 'watch', 'watch', 'approved')
        """,
        (
            evaluation_id,
            request_id,
            run_id,
            UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            "c" * 64,
        ),
    )
    suggestion_id = connection.execute(
        """
        INSERT INTO public.suggestions
          (date, ticker, action, run_id, evaluation_id, decision_source)
        VALUES (DATE '2026-09-02', 'TSTAAA', 'watch', %s, %s, 'gateway')
        RETURNING id
        """,
        (run_id, evaluation_id),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO public.suggestion_grades (suggestion_id, result) VALUES (%s, 'pending')",
        (suggestion_id,),
    )
    connection.execute(
        """
        INSERT INTO public.portfolio_commands
          (telegram_update_id, chat_id, user_id, operation, ticker, qty, price,
           expected_shares, status)
        VALUES (91001, 92001, 93001, 'buy', 'TSTAAA', 2, 10, 0, 'applied')
        """
    )
    connection.execute(
        """
        INSERT INTO public.owner_investment_plans
          (ticker, bucket, amount, cadence, next_due_on, due_day)
        VALUES ('TSTVTI', 'core', 300, 'monthly', DATE '2026-09-21', 21)
        """
    )
    connection.execute(
        """
        INSERT INTO public.market_publications
          (id, idempotency_key, run_id, market_date, phase, kind, template_version,
           rendered_body, rendered_hash, status)
        VALUES (%s, %s, %s, DATE '2026-09-02', 'pre-market', 'brief', 1,
                'synthetic body', %s, 'delivered')
        """,
        (
            UUID("12345678-1234-4234-8234-123456789abc"),
            request_id,
            run_id,
            "d" * 64,
        ),
    )


def verify_connection_rollback(connection: psycopg.Connection) -> dict[str, Any]:
    receipt: dict[str, Any]
    with connection.transaction(force_rollback=True):
        connection.execute(FOUNDATION_SQL.read_text())
        _seed_synthetic_records(connection)
        receipt = migrate_connection(
            connection,
            owner_email=SYNTHETIC_OWNER_EMAIL,
            rollback_only=False,
        )
        unowned = connection.execute(
            "SELECT count(*) FROM app.holdings WHERE owner_id IS NULL"
        ).fetchone()[0]
        if unowned:
            raise MigrationRejected("rollback verification found unowned rows")
        if connection.execute(
            "SELECT to_regclass('public.holdings') IS NOT NULL"
        ).fetchone()[0]:
            raise MigrationRejected("rollback verification did not move owner tables")

    rolled_back = connection.execute(
        """
        SELECT to_regclass('public.holdings') IS NOT NULL
           AND to_regclass('app.holdings') IS NULL
        """
    ).fetchone()[0]
    if not rolled_back:
        raise MigrationRejected("rollback verification left schema changes behind")
    return {
        "receipt": receipt,
        "rolled_back": True,
        "synthetic_tickers": ["TSTAAA", "TSTVTI"],
    }


def verify_disposable_rollback() -> dict[str, Any]:
    with DisposablePostgres() as database:
        assert database.connection is not None
        return verify_connection_rollback(database.connection)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback-only", action="store_true")
    parser.add_argument("--db-url-env")
    parser.add_argument("--project-ref", default="local-disposable")
    parser.add_argument(
        "--production-project-ref",
        default=os.environ.get("PRODUCTION_SUPABASE_PROJECT_REF"),
    )
    parser.add_argument("--production-cutover", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.rollback_only:
            raise MigrationRejected("verifier requires --rollback-only")
        authorize_target(
            project_ref=args.project_ref,
            production_project_ref=args.production_project_ref,
            production_cutover=args.production_cutover,
            confirmation=args.confirm,
            owner_email=SYNTHETIC_OWNER_EMAIL,
        )
        if args.db_url_env:
            database_url = os.environ.get(args.db_url_env)
            if not database_url:
                raise MigrationRejected(f"{args.db_url_env} is not configured")
            with psycopg.connect(database_url, autocommit=True) as connection:
                result = verify_connection_rollback(connection)
        else:
            result = verify_disposable_rollback()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except (MigrationRejected, subprocess.SubprocessError) as error:
        print(f"verification rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
