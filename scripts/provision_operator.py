#!/usr/bin/env python3
"""Provision one existing staging Auth identity as the private release operator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any
from uuid import UUID

import psycopg


PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class OperatorProvisionRejected(RuntimeError):
    """The staging operator could not be provisioned without broadening authority."""


def operator_digest(operator_id: UUID) -> str:
    return hashlib.sha256(str(operator_id).encode()).hexdigest()


def authorize_target(*, project_ref: str, environment: str, confirmation: str) -> None:
    if environment != "staging" or not PROJECT_RE.fullmatch(project_ref):
        raise OperatorProvisionRejected("operator provisioning is staging-only")
    if confirmation != f"PROVISION STAGING OPERATOR {project_ref}":
        raise OperatorProvisionRejected("staging operator confirmation is invalid")


def provision_connection(
    connection: psycopg.Connection, operator_id: UUID
) -> dict[str, Any]:
    try:
        with connection.transaction():
            auth_count = connection.execute(
                "SELECT count(*) FROM auth.users WHERE id = %s", (operator_id,)
            ).fetchone()[0]
            if auth_count != 1:
                raise OperatorProvisionRejected(
                    "operator identity must resolve to exactly one Auth user"
                )
            connection.execute(
                """
                INSERT INTO app.profiles (id, status)
                VALUES (%s, 'active')
                ON CONFLICT (id) DO NOTHING
                """,
                (operator_id,),
            )
            status = connection.execute(
                "SELECT status FROM app.profiles WHERE id = %s", (operator_id,)
            ).fetchone()[0]
            if status != "active":
                raise OperatorProvisionRejected("operator profile is not active")
            connection.execute(
                """
                INSERT INTO app.app_admins (user_id, role)
                VALUES (%s, 'operator')
                ON CONFLICT (user_id) DO NOTHING
                """,
                (operator_id,),
            )
            role = connection.execute(
                "SELECT role FROM app.app_admins WHERE user_id = %s", (operator_id,)
            ).fetchone()[0]
            if role not in {"operator", "admin"}:
                raise OperatorProvisionRejected("operator role is unavailable")
            connection.execute(
                """
                INSERT INTO app.notification_preferences (owner_id)
                VALUES (%s)
                ON CONFLICT (owner_id) DO NOTHING
                """,
                (operator_id,),
            )
    except OperatorProvisionRejected:
        raise
    except (IndexError, TypeError, psycopg.Error) as error:
        raise OperatorProvisionRejected("operator provisioning failed") from error
    return {
        "status": "provisioned",
        "operator_digest": operator_digest(operator_id),
        "private_data": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--db-url-env", default="STAGING_POSTGRES_URL")
    parser.add_argument("--operator-id-env", default="STAGING_OPERATOR_ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        authorize_target(
            project_ref=args.project_ref,
            environment=args.environment,
            confirmation=args.confirm,
        )
        database_url = os.environ.get(args.db_url_env)
        raw_operator_id = os.environ.get(args.operator_id_env)
        if not database_url or not raw_operator_id:
            raise OperatorProvisionRejected("staging operator environment is incomplete")
        try:
            operator_id = UUID(raw_operator_id)
        except ValueError as error:
            raise OperatorProvisionRejected("staging operator identity is invalid") from error
        with psycopg.connect(database_url, autocommit=True, connect_timeout=10) as connection:
            result = provision_connection(connection, operator_id)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, OperatorProvisionRejected):
        print("operator provisioning rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
