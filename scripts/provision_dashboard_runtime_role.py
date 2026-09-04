#!/usr/bin/env python3
"""Provision the owner-dashboard login without exposing its credential."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
from typing import Callable, Mapping
from urllib.parse import quote, urlparse, urlunparse

import psycopg
from psycopg import sql


PRIVILEGE_ROLE = "stock_agent_dashboard"
RUNTIME_ROLE = "stock_agent_dashboard_runtime"
SUPABASE_CLI_VERSION = "2.116.0"


def _validated_session_template(value: str):
    parsed = urlparse(value)
    username = parsed.username or ""
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.hostname.endswith(".pooler.supabase.com")
        or parsed.port != 5432
        or "." not in username
        or not parsed.password
        or parsed.path != "/postgres"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("a Supavisor session endpoint template on port 5432 is required")
    return parsed


def runtime_url(session_template: str, login_role: str, password: str) -> str:
    """Replace admin credentials with a scoped login while preserving the project suffix."""
    parsed = _validated_session_template(session_template)
    if login_role != RUNTIME_ROLE or len(password) < 24:
        raise ValueError("a scoped dashboard login and strong password are required")
    project_suffix = (parsed.username or "").split(".", 1)[1]
    hostname = parsed.hostname or ""
    netloc = f"{quote(login_role + '.' + project_suffix, safe='')}:{quote(password, safe='')}@{hostname}:5432"
    return urlunparse((parsed.scheme, netloc, "/postgres", "", "", ""))


def _role_row(connection, role_name: str):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
              FROM pg_catalog.pg_roles
             WHERE rolname = %s
            """,
            (role_name,),
        )
        return cursor.fetchone()


def provision_dashboard_role(
    connection,
    session_template: str,
    password_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, str]:
    """Create/rotate the runtime role and return the single deployable secret."""
    privilege = _role_row(connection, PRIVILEGE_ROLE)
    if privilege is None:
        raise RuntimeError("dashboard privilege role is missing; apply the migration first")
    if privilege[1] or any(privilege[2:]):
        raise RuntimeError("dashboard privilege role has unsafe attributes")

    password = password_factory(36)
    if len(password) < 24:
        raise RuntimeError("generated dashboard password is too short")

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (RUNTIME_ROLE,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS").format(
                        sql.Identifier(RUNTIME_ROLE)
                    )
                )
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOBYPASSRLS PASSWORD %s"
                ).format(sql.Identifier(RUNTIME_ROLE)),
                (password,),
            )
            cursor.execute(
                """
                SELECT granted.rolname
                  FROM pg_catalog.pg_auth_members memberships
                  JOIN pg_catalog.pg_roles member ON member.oid = memberships.member
                  JOIN pg_catalog.pg_roles granted ON granted.oid = memberships.roleid
                 WHERE member.rolname = %s
                """,
                (RUNTIME_ROLE,),
            )
            for (membership,) in cursor.fetchall():
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(membership), sql.Identifier(RUNTIME_ROLE)
                    )
                )
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(PRIVILEGE_ROLE), sql.Identifier(RUNTIME_ROLE)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, public").format(
                    sql.Identifier(RUNTIME_ROLE)
                )
            )

    return {
        "DASHBOARD_DATABASE_URL": runtime_url(
            session_template, RUNTIME_ROLE, password
        )
    }


def disable_dashboard_runtime_login(connection) -> dict[str, object]:
    """Remove the deploy credential's login and inherited dashboard privileges."""
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(PRIVILEGE_ROLE), sql.Identifier(RUNTIME_ROLE)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                    sql.Identifier(RUNTIME_ROLE)
                )
            )
    return {
        "status": "disabled",
        "runtime_role": RUNTIME_ROLE,
        "login": False,
        "memberships": 0,
    }


def publish_dashboard_secret(
    values: Mapping[str, str],
    project_ref: str,
    runner: Callable[..., object] = subprocess.run,
) -> list[str]:
    """Publish only the dashboard URL through a private temporary env file."""
    if set(values) != {"DASHBOARD_DATABASE_URL"}:
        raise ValueError("only DASHBOARD_DATABASE_URL may be published")
    if not project_ref:
        raise ValueError("project_ref is required")

    descriptor, raw_path = tempfile.mkstemp(prefix="stocks-dashboard-", suffix=".env")
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"DASHBOARD_DATABASE_URL={values['DASHBOARD_DATABASE_URL']}\n")
        result = runner(
            [
                "npx",
                "--yes",
                f"supabase@{SUPABASE_CLI_VERSION}",
                "secrets",
                "set",
                "--env-file",
                str(path),
                "--project-ref",
                project_ref,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError("Supabase rejected the dashboard secret update")
        return ["DASHBOARD_DATABASE_URL"]
    finally:
        if path.exists():
            try:
                path.write_bytes(b"\0" * path.stat().st_size)
            finally:
                path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    arguments = parser.parse_args()
    admin_url = os.environ.get("POSTGRES_URL", "")
    session_template = os.environ.get("SUPAVISOR_SESSION_URL", "")
    if not admin_url or not session_template:
        raise SystemExit("POSTGRES_URL and SUPAVISOR_SESSION_URL are required")

    with psycopg.connect(admin_url) as connection:
        secret = provision_dashboard_role(connection, session_template)
    names = publish_dashboard_secret(secret, arguments.project_ref)
    print(f"published dashboard secret names: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
