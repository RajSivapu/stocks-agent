#!/usr/bin/env python3
"""Purge a grace-expired account and delete its Supabase Auth identity last."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import UUID

import psycopg

from scripts.invite_user import _request, canonical_project_url


class DeletionRejected(RuntimeError):
    """Raised before or during the transactional active-data purge."""


class AuthDeletionPending(RuntimeError):
    """Raised after active data is gone but the Auth identity still needs deletion."""


def validate_confirmation(owner_id: UUID, value: str) -> str:
    expected = f"DELETE AUTH {owner_id}"
    if value != expected:
        raise ValueError("typed account-deletion confirmation does not exactly match the owner")
    return expected


def _prepared_receipt(value: Any, deletion_request_id: UUID) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "status", "deletion_request_id", "deleted_at", "archives_expire_after", "duplicate",
    } or value.get("status") != "ready_for_auth_deletion" or not isinstance(value.get("duplicate"), bool):
        raise DeletionRejected("database did not acknowledge the active-data purge")
    try:
        observed_id = UUID(str(value["deletion_request_id"]))
    except (TypeError, ValueError) as error:
        raise DeletionRejected("database returned a malformed deletion receipt") from error
    if observed_id != deletion_request_id:
        raise DeletionRejected("database returned a mismatched deletion receipt")
    if not all(isinstance(value.get(field), str) and value[field] for field in (
        "deleted_at", "archives_expire_after",
    )):
        raise DeletionRejected("database returned a malformed deletion receipt")
    return value


def preview_connection(connection: Any, owner_id: UUID, deletion_request_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT jsonb_build_object(
          'status', status,
          'grace_expired', cancel_until <= clock_timestamp(),
          'delete_deadline', delete_by
        )
        FROM app.account_deletion_requests
        WHERE owner_id = %s AND id = %s AND status = 'pending'
        """,
        (owner_id, deletion_request_id),
    ).fetchone()
    value = row[0] if row else None
    if not isinstance(value, dict) or set(value) != {"status", "grace_expired", "delete_deadline"}:
        raise DeletionRejected("pending account deletion was not found")
    return value


def delete_connection(
    connection: Any,
    *,
    owner_id: UUID,
    deletion_request_id: UUID,
    confirmation: str,
    supabase_url: str,
    service_role_key: str,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, str]:
    validate_confirmation(owner_id, confirmation)
    endpoint = canonical_project_url(supabase_url)
    if len(service_role_key) < 16 or len(service_role_key) > 16_384:
        raise ValueError("offline service-role credential is missing or malformed")

    try:
        with connection.transaction():
            row = connection.execute(
                "SELECT app.operator_prepare_account_deletion(%s, %s, %s)",
                (owner_id, deletion_request_id, confirmation),
            ).fetchone()
            prepared = _prepared_receipt(row[0] if row else None, deletion_request_id)
    except (DeletionRejected, ValueError):
        raise
    except Exception as error:
        raise DeletionRejected("transactional active-data purge failed") from error

    try:
        _request(
            f"{endpoint}/auth/v1/admin/users/{owner_id}",
            service_role_key,
            method="DELETE",
            body=None,
            opener=opener,
            parse_response=False,
        )
    except HTTPError as error:
        if error.code != 404:
            raise AuthDeletionPending(
                "active data is deleted; retry the same command to finish Auth deletion"
            ) from error
    except Exception as error:
        raise AuthDeletionPending(
            "active data is deleted; retry the same command to finish Auth deletion"
        ) from error

    try:
        remaining = connection.execute(
            "SELECT count(*) FROM app.profiles WHERE id = %s", (owner_id,)
        ).fetchone()
    except Exception as error:
        raise AuthDeletionPending(
            "Auth deletion was requested; retry verification before considering it complete"
        ) from error
    if remaining is None or remaining[0] != 0:
        raise AuthDeletionPending(
            "Auth identity still exists; retry the same command before considering deletion complete"
        )
    return {
        "status": "deleted",
        "deleted_at": prepared["deleted_at"],
        "archives_expire_after": prepared["archives_expire_after"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--deletion-request-id", type=UUID, required=True)
    parser.add_argument("--confirm")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-url-env", default="STOCK_AGENT_OPERATOR_DATABASE_URL")
    parser.add_argument("--supabase-url-env", default="STOCK_AGENT_SUPABASE_URL")
    parser.add_argument("--service-role-key-env", default="STOCK_AGENT_SERVICE_ROLE_KEY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database_url = os.environ.get(args.db_url_env, "")
        if not database_url:
            raise DeletionRejected(f"{args.db_url_env} is not configured")
        with psycopg.connect(database_url, autocommit=False) as connection:
            if args.dry_run:
                with connection.transaction():
                    result = preview_connection(connection, args.owner_id, args.deletion_request_id)
                print(json.dumps(result, sort_keys=True, default=str))
                return 0
            if args.confirm is None:
                raise DeletionRejected("exact owner-bound confirmation is required")
            result = delete_connection(
                connection,
                owner_id=args.owner_id,
                deletion_request_id=args.deletion_request_id,
                confirmation=args.confirm,
                supabase_url=os.environ.get(args.supabase_url_env, ""),
                service_role_key=os.environ.get(args.service_role_key_env, ""),
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except AuthDeletionPending as error:
        print(f"account deletion incomplete: {error}", file=sys.stderr)
        return 3
    except (DeletionRejected, OSError, ValueError) as error:
        print(f"account deletion rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
