import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

import psycopg
import pytest

from scripts import deploy_owner_dashboard_api as deploy


UNTRUSTED_ROLES = ("anon", "authenticated", "service_role")


@pytest.fixture(scope="module")
def release_database():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local binaries and a non-root user")
    with tempfile.TemporaryDirectory(prefix="release-metadata-security-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        subprocess.run(
            [
                binaries["initdb"],
                "-D",
                str(root / "db"),
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--no-locale",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                binaries["pg_ctl"],
                "-D",
                str(root / "db"),
                "-l",
                str(root / "postgres.log"),
                "-o",
                f"-k {root} -h '' -p {port}",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        dsn = f"host={root} port={port} dbname=postgres"
        try:
            with psycopg.connect(dsn, autocommit=True) as database:
                database.execute(
                    "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role; "
                    "CREATE SCHEMA supabase_migrations; "
                    "CREATE TABLE supabase_migrations.schema_migrations("
                    "version text PRIMARY KEY, statements text[])"
                )
                database.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL ON TABLES TO PUBLIC, anon, authenticated, service_role"
                )
            yield dsn
        finally:
            subprocess.run(
                [
                    binaries["pg_ctl"],
                    "-D",
                    str(root / "db"),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ],
                check=True,
                capture_output=True,
            )


def assert_release_table_is_private(database, table):
    assert database.execute(
        "SELECT relrowsecurity FROM pg_catalog.pg_class WHERE oid=%s::regclass",
        (table,),
    ).fetchone() == (True,)
    assert database.execute(
        "SELECT coalesce(array_agg(DISTINCT coalesce(role.rolname, 'PUBLIC')), "
        "ARRAY[]::text[]) "
        "FROM pg_catalog.pg_class AS relation "
        "CROSS JOIN LATERAL aclexplode(coalesce("
        "relation.relacl, acldefault('r', relation.relowner))) AS acl "
        "LEFT JOIN pg_catalog.pg_roles AS role ON role.oid=acl.grantee "
        "WHERE relation.oid=%s::regclass "
        "AND (acl.grantee=0 OR role.rolname=ANY(%s))",
        (table, list(UNTRUSTED_ROLES)),
    ).fetchone() == ([],)


def test_durable_release_lease_is_private_at_dynamic_creation(release_database):
    with deploy.DurableMutationLease(
        release_database, "recovery-900000001-1", "recovery"
    ) as lease:
        lease.resolve()

    with psycopg.connect(release_database, autocommit=True) as database:
        assert_release_table_is_private(
            database, "public.stock_agent_release_mutation_lease"
        )


def test_migration_ledger_is_private_at_bootstrap(
    release_database, tmp_path, monkeypatch
):
    monkeypatch.setattr(deploy, "legacy_migration_receipts", lambda: ())
    migration = tmp_path / "20261101_security_probe.sql"
    migration.write_text("SELECT 1;\n")
    manifest = deploy.candidate_migration_manifest(tmp_path)

    with psycopg.connect(release_database) as database:
        with database.cursor() as cursor:
            deploy.apply_release_migrations(cursor, manifest, tmp_path)

    with psycopg.connect(release_database, autocommit=True) as database:
        assert_release_table_is_private(
            database, "public.stock_agent_release_migration_ledger"
        )
