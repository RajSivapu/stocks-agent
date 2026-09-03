#!/usr/bin/env python3
"""Provision least-privilege runtime logins and publish only their database URLs."""

from __future__ import annotations

import argparse
import json
import os
import secrets as secret_generator
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import psycopg
from psycopg import sql


ROLE_BINDINGS = (
    ("stock_agent_gateway_runtime", "stock_agent_gateway", "AGENT_DATABASE_URL"),
    ("stock_agent_scheduler_runtime", "stock_agent_scheduler", "SCHEDULER_DATABASE_URL"),
    ("stock_agent_telegram_runtime", "stock_agent_telegram", "TELEGRAM_DATABASE_URL"),
    ("stock_agent_backup_runtime", "stock_agent_backup", "BACKUP_DATABASE_URL"),
)


def _session_template(value: str):
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("session database URL is malformed") from error
    valid = (
        parsed.scheme in ("postgres", "postgresql")
        and parsed.hostname is not None
        and parsed.hostname.endswith(".pooler.supabase.com")
        and port == 5432
        and parsed.username is not None
        and "." in parsed.username
        and parsed.password is not None
        and parsed.path == "/postgres"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    if not valid:
        raise ValueError("session database URL must be the Supavisor port-5432 endpoint")
    return parsed


def _runtime_url(template: str, login_role: str, password: str) -> str:
    parsed = _session_template(template)
    assert parsed.username is not None
    project_suffix = parsed.username.split(".", 1)[1]
    username = f"{login_role}.{project_suffix}"
    host = parsed.hostname or ""
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}:5432"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def provision_database_roles(
    connection: psycopg.Connection,
    session_url_template: str,
    *,
    password_factory: Callable[[], str] = lambda: secret_generator.token_urlsafe(36),
) -> dict[str, str]:
    _session_template(session_url_template)
    published: dict[str, str] = {}
    for login_role, privilege_role, secret_name in ROLE_BINDINGS:
        password = password_factory()
        if not isinstance(password, str) or len(password) < 24 or any(char.isspace() for char in password):
            raise ValueError("generated runtime password is malformed")
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (login_role,)
        ).fetchone()
        if exists is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                ).format(sql.Identifier(login_role))
            )
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(login_role), sql.Literal(password))
        )
        memberships = connection.execute(
            """
            SELECT granted.rolname
            FROM pg_auth_members membership
            JOIN pg_roles member ON member.oid = membership.member
            JOIN pg_roles granted ON granted.oid = membership.roleid
            WHERE member.rolname = %s
            """,
            (login_role,),
        ).fetchall()
        for (granted_role,) in memberships:
            connection.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(granted_role), sql.Identifier(login_role)
                )
            )
        connection.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(privilege_role), sql.Identifier(login_role)
            )
        )
        published[secret_name] = _runtime_url(session_url_template, login_role, password)
    return published


def _wipe_and_unlink(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open("r+b", buffering=0) as output:
            output.write(b"\0" * size)
            output.flush()
            os.fsync(output.fileno())
    finally:
        path.unlink(missing_ok=True)


def publish_secrets(
    values: Mapping[str, str],
    *,
    project_ref: str,
    runner: Callable[..., Any] = subprocess.run,
) -> list[str]:
    if not project_ref or any(char.isspace() for char in project_ref):
        raise ValueError("project reference is malformed")
    if not values:
        raise ValueError("no runtime secrets were generated")
    descriptor, filename = tempfile.mkstemp(prefix="stock-agent-runtime-", suffix=".env")
    path = Path(filename)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            for name in sorted(values):
                value = values[name]
                if not name.isupper() or not value or "\n" in value or "\r" in value:
                    raise ValueError("runtime secret is malformed")
                output.write(f"{name}={value}\n")
            output.flush()
            os.fsync(output.fileno())
        result = runner(
            [
                "npx",
                "--yes",
                "supabase@2.116.0",
                "secrets",
                "set",
                "--env-file",
                str(path),
                "--project-ref",
                project_ref,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Supabase runtime-secret publication failed")
        return sorted(values)
    finally:
        _wipe_and_unlink(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--admin-db-url-env", default="STAGING_POSTGRES_URL")
    parser.add_argument(
        "--session-url-template-env", default="STAGING_SUPAVISOR_SESSION_URL"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        admin_url = os.environ.get(args.admin_db_url_env, "")
        session_template = os.environ.get(args.session_url_template_env, "")
        if not admin_url or not session_template:
            raise ValueError("required staging database environment is not configured")
        with psycopg.connect(admin_url, autocommit=True) as connection:
            with connection.transaction():
                values = provision_database_roles(connection, session_template)
                names = publish_secrets(values, project_ref=args.project_ref)
        print(json.dumps({"status": "published", "credential_names": names}, sort_keys=True))
        return 0
    except (OSError, psycopg.Error, RuntimeError, ValueError):
        print("runtime-role provisioning failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
