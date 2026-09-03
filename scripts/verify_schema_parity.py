#!/usr/bin/env python3
"""Regenerate and compare the canonical schema with the ordered migration path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCHEMA = ROOT / "sql/legacy_schema.sql"
CANONICAL_SCHEMA = ROOT / "sql/schema.sql"
MIGRATIONS = tuple(sorted((ROOT / "sql/migrations").glob("*.sql")))
POST_LEGACY_MIGRATIONS = tuple(
    path for path in MIGRATIONS if path.name > "20260904_outcome_evaluation.sql"
)
FRESH_BOOTSTRAP = ROOT / "sql/bootstrap/fresh_multitenancy.sql"
HEADER = """-- GENERATED CANONICAL FRESH-INSTALL SCHEMA.
-- Source of truth: sql/legacy_schema.sql followed by every reviewed migration after 20260904.
-- Regenerate with: python scripts/verify_schema_parity.py --write
-- Do not add credentials, data rows, or platform-owned Auth definitions here.
"""


class SchemaParityRejected(RuntimeError):
    """Canonical and migration schemas could not be safely proved equivalent."""


def render_canonical_schema() -> str:
    if not LEGACY_SCHEMA.is_file() or not FRESH_BOOTSTRAP.is_file() or not POST_LEGACY_MIGRATIONS:
        raise SchemaParityRejected("schema sources are incomplete")
    sections = [HEADER.rstrip(), LEGACY_SCHEMA.read_text(encoding="utf-8").rstrip()]
    for path in POST_LEGACY_MIGRATIONS:
        relative = path.relative_to(ROOT).as_posix()
        sections.extend((
            f"-- BEGIN REVIEWED MIGRATION: {relative}",
            path.read_text(encoding="utf-8").rstrip(),
            f"-- END REVIEWED MIGRATION: {relative}",
        ))
        if path.name == "20260905000000_multitenancy_foundation.sql":
            bootstrap_relative = FRESH_BOOTSTRAP.relative_to(ROOT).as_posix()
            sections.extend((
                f"-- BEGIN FRESH-INSTALL BOOTSTRAP: {bootstrap_relative}",
                FRESH_BOOTSTRAP.read_text(encoding="utf-8").rstrip(),
                f"-- END FRESH-INSTALL BOOTSTRAP: {bootstrap_relative}",
            ))
    rendered = "\n\n".join(sections) + "\n"
    lowered = rendered.lower()
    for forbidden in (
        "postgresql://",
        "supabase_service_role_key=",
        "telegram_bot_token=",
        "copy auth.users",
        "insert into auth.users",
    ):
        if forbidden in lowered:
            raise SchemaParityRejected("canonical schema contains forbidden runtime material")
    return rendered


def write_canonical_schema() -> dict[str, str | int | bool]:
    rendered = render_canonical_schema()
    temporary = CANONICAL_SCHEMA.with_suffix(".sql.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, CANONICAL_SCHEMA)
    return {
        "status": "written",
        "bytes": len(rendered.encode()),
        "digest": hashlib.sha256(rendered.encode()).hexdigest(),
        "private_data": False,
    }


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
    raise SchemaParityRejected("PostgreSQL 17 test binaries are unavailable")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _cluster() -> Iterator[tuple[int, Path]]:
    pg_ctl = _postgres_bin("pg_ctl")
    with tempfile.TemporaryDirectory(prefix="stock-agent-schema-parity-") as name:
        directory = Path(name)
        data = directory / "data"
        log = directory / "postgres.log"
        port = _free_port()
        subprocess.run(
            [
                _postgres_bin("initdb"), "--no-sync", "--auth-local=trust",
                "--auth-host=trust", "--username=postgres", str(data),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                pg_ctl, "-D", str(data), "-l", str(log), "-o",
                f"-F -h 127.0.0.1 -p {port}", "-w", "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            yield port, data
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
                check=False,
                capture_output=True,
                text=True,
            )


def _connect(port: int, database: str = "postgres") -> psycopg.Connection:
    return psycopg.connect(
        dbname=database,
        user="postgres",
        host="127.0.0.1",
        port=port,
        autocommit=True,
        connect_timeout=10,
    )


def _platform_stubs(connection: psycopg.Connection) -> None:
    connection.execute("CREATE SCHEMA auth")
    connection.execute("CREATE SCHEMA vault")
    connection.execute("CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE)")
    connection.execute(
        """
        CREATE FUNCTION auth.uid() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$
          SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
        $$
        """
    )
    connection.execute("CREATE TABLE vault.secrets (id uuid PRIMARY KEY, secret text)")
    connection.execute(
        "CREATE VIEW vault.decrypted_secrets AS SELECT id, secret FROM vault.secrets"
    )
    connection.execute(
        """
        CREATE FUNCTION vault.create_secret(
          new_secret text, new_name text, new_description text
        ) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER AS $$
        DECLARE secret_id uuid := gen_random_uuid();
        BEGIN
          INSERT INTO vault.secrets(id, secret) VALUES (secret_id, new_secret);
          RETURN secret_id;
        END
        $$;
        CREATE FUNCTION vault.delete_secret(secret_id uuid)
        RETURNS void LANGUAGE sql SECURITY DEFINER
        AS $$ DELETE FROM vault.secrets WHERE id = secret_id $$;
        """
    )


def _normalize_dump(value: str) -> bytes:
    lines = []
    for line in value.splitlines():
        if line.startswith("--") or line.startswith("\\restrict") or line.startswith("\\unrestrict"):
            continue
        lines.append(line.rstrip())
    return ("\n".join(lines).strip() + "\n").encode()


def _dump(port: int, database: str) -> bytes:
    completed = subprocess.run(
        [
            _postgres_bin("pg_dump"), "--schema-only", "--no-owner", "--no-comments",
            "--host=127.0.0.1", f"--port={port}", "--username=postgres", database,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return _normalize_dump(completed.stdout)


def verify_disposable_catalog_parity() -> dict[str, str | bool]:
    rendered = render_canonical_schema()
    if CANONICAL_SCHEMA.read_text(encoding="utf-8") != rendered:
        raise SchemaParityRejected("canonical schema is not regenerated")
    try:
        with _cluster() as (port, _data):
            with _connect(port) as admin:
                for role in ("anon", "authenticated", "service_role"):
                    admin.execute(f"CREATE ROLE {role} NOLOGIN")
                admin.execute("CREATE DATABASE migration_path TEMPLATE template0")
                admin.execute("CREATE DATABASE canonical_path TEMPLATE template0")

            with _connect(port, "migration_path") as migrated:
                _platform_stubs(migrated)
                migrated.execute(LEGACY_SCHEMA.read_text(encoding="utf-8"))
                for path in POST_LEGACY_MIGRATIONS:
                    migrated.execute(path.read_text(encoding="utf-8"))
                    if path.name == "20260905000000_multitenancy_foundation.sql":
                        migrated.execute(
                            "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                            (
                                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                                "schema-parity@invalid.example",
                            ),
                        )
                        migrated.execute(
                            "SELECT machine.backfill_single_owner_to_tenant(%s)",
                            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",),
                        )
                        migrated.execute(
                            """
                            DROP FUNCTION machine.backfill_single_owner_to_tenant(UUID);
                            DROP FUNCTION machine.single_owner_relationship_digest(NAME);
                            DROP FUNCTION machine.single_owner_row_digest(NAME);
                            DROP FUNCTION machine.single_owner_table_counts(NAME);
                            """
                        )

            with _connect(port, "canonical_path") as canonical:
                _platform_stubs(canonical)
                canonical.execute(rendered)

            migrated_dump = _dump(port, "migration_path")
            canonical_dump = _dump(port, "canonical_path")
    except (psycopg.Error, OSError, subprocess.SubprocessError) as error:
        raise SchemaParityRejected("disposable schema verification failed") from error
    migration_digest = hashlib.sha256(migrated_dump).hexdigest()
    canonical_digest = hashlib.sha256(canonical_dump).hexdigest()
    if not migration_digest == canonical_digest:
        raise SchemaParityRejected("canonical and migration catalogs differ")
    return {
        "status": "passed",
        "migration_digest": migration_digest,
        "canonical_digest": canonical_digest,
        "private_data": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = write_canonical_schema() if args.write else verify_disposable_catalog_parity()
        print(json.dumps(result, sort_keys=True))
        return 0
    except SchemaParityRejected:
        print("schema parity rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
