#!/usr/bin/env python3
"""Fail-closed operator migration from the legacy portfolio to one Auth owner."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import psycopg


DROP_MIGRATION_AUTHORITY_SQL = """
DROP FUNCTION machine.backfill_single_owner_to_tenant(UUID);
DROP FUNCTION machine.single_owner_relationship_digest(NAME);
DROP FUNCTION machine.single_owner_row_digest(NAME);
DROP FUNCTION machine.single_owner_table_counts(NAME);
"""


class MigrationRejected(RuntimeError):
    """Raised when a migration precondition or parity invariant fails."""


def _error_message(error: psycopg.Error) -> str:
    primary = getattr(error.diag, "message_primary", None)
    return primary or "database rejected the owner migration"


def _canonical_email(owner_email: str) -> str:
    if not isinstance(owner_email, str):
        raise MigrationRejected("owner email is required")
    canonical = owner_email.strip().lower()
    if not canonical or len(canonical) > 320 or "@" not in canonical:
        raise MigrationRejected("owner email is malformed")
    return canonical


def resolve_owner_id(connection: psycopg.Connection, owner_email: str) -> UUID:
    """Resolve an operator-supplied email to exactly one immutable Auth UUID."""
    canonical = _canonical_email(owner_email)
    rows = connection.execute(
        "SELECT id FROM auth.users WHERE lower(email) = %s ORDER BY id LIMIT 2",
        (canonical,),
    ).fetchall()
    if len(rows) != 1:
        raise MigrationRejected("owner email must resolve to exactly one Auth user")
    owner_id = rows[0][0]
    return owner_id if isinstance(owner_id, UUID) else UUID(str(owner_id))


def assert_parity(
    *,
    before_counts: Mapping[str, int],
    after_counts: Mapping[str, int],
    before_digest: str,
    after_digest: str,
) -> None:
    if dict(before_counts) != dict(after_counts):
        raise MigrationRejected("row count changed during owner migration")
    if before_digest != after_digest:
        raise MigrationRejected("row digest changed during owner migration")


def _public_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "owner_id_hash",
        "before_counts",
        "after_counts",
        "row_digest",
        "relationship_digest",
        "passed",
        "completed_at",
    }
    if set(receipt) != allowed:
        raise MigrationRejected("migration receipt has an unexpected shape")
    if receipt.get("passed") is not True:
        raise MigrationRejected("migration receipt did not pass")
    assert_parity(
        before_counts=receipt["before_counts"],
        after_counts=receipt["after_counts"],
        before_digest=receipt["row_digest"],
        after_digest=receipt["row_digest"],
    )
    return dict(receipt)


def _existing_receipt(connection: psycopg.Connection, owner_id: UUID) -> dict[str, Any] | None:
    exists = connection.execute(
        "SELECT to_regclass('app.single_owner_migration_receipts') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return None
    row = connection.execute(
        """
        SELECT to_jsonb(r) - 'id' - 'owner_id'
        FROM app.single_owner_migration_receipts AS r
        WHERE owner_id = %s AND passed
        """,
        (owner_id,),
    ).fetchone()
    return _public_receipt(row[0]) if row else None


def migrate_connection(
    connection: psycopg.Connection,
    *,
    owner_email: str,
    rollback_only: bool,
) -> dict[str, Any]:
    """Run one atomic owner migration; rollback-only uses a nested savepoint."""
    try:
        with connection.transaction(force_rollback=rollback_only):
            owner_id = resolve_owner_id(connection, owner_email)
            existing = _existing_receipt(connection, owner_id)
            if existing is not None:
                return existing

            row = connection.execute(
                "SELECT machine.backfill_single_owner_to_tenant(%s)",
                (owner_id,),
            ).fetchone()
            if row is None:
                raise MigrationRejected("migration did not return a receipt")
            receipt = _public_receipt(row[0])
            connection.execute(DROP_MIGRATION_AUTHORITY_SQL)
            return receipt
    except MigrationRejected:
        raise
    except psycopg.Error as error:
        raise MigrationRejected(_error_message(error)) from error


def authorize_target(
    *,
    project_ref: str,
    production_project_ref: str | None,
    production_cutover: bool,
    confirmation: str | None,
    owner_email: str,
) -> None:
    """Require a project-bound phrase before any known production cutover."""
    if not project_ref or any(char.isspace() for char in project_ref):
        raise MigrationRejected("project reference is malformed")
    if production_project_ref and project_ref == production_project_ref:
        if not production_cutover:
            raise MigrationRejected("production cutover requires --production-cutover")
        expected = f"MIGRATE {project_ref} FOR {_canonical_email(owner_email)}"
        if confirmation != expected:
            raise MigrationRejected(f"production confirmation must exactly match: {expected}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Move a paused legacy stock-agent database to one Auth owner."
    )
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument(
        "--db-url-env",
        default="STAGING_POSTGRES_URL",
        help="Environment variable containing the admin PostgreSQL URL.",
    )
    parser.add_argument(
        "--production-project-ref",
        default=os.environ.get("PRODUCTION_SUPABASE_PROJECT_REF"),
    )
    parser.add_argument("--production-cutover", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--rollback-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        authorize_target(
            project_ref=args.project_ref,
            production_project_ref=args.production_project_ref,
            production_cutover=args.production_cutover,
            confirmation=args.confirm,
            owner_email=args.owner_email,
        )
        database_url = os.environ.get(args.db_url_env)
        if not database_url:
            raise MigrationRejected(f"{args.db_url_env} is not configured")
        with psycopg.connect(database_url, autocommit=True) as connection:
            receipt = migrate_connection(
                connection,
                owner_email=args.owner_email,
                rollback_only=args.rollback_only,
            )
        print(json.dumps(receipt, sort_keys=True, default=str))
        return 0
    except MigrationRejected as error:
        print(f"migration rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
