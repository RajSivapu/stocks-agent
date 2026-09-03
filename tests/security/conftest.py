from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[2]
FOUNDATION_MIGRATION = ROOT / "sql/migrations/20260905000000_multitenancy_foundation.sql"
RLS_MIGRATION = ROOT / "sql/migrations/20260906000000_owner_api_and_machine_roles.sql"
LEDGER_MIGRATION = ROOT / "sql/migrations/20260907000000_ledger_projection_commands.sql"
COMMAND_MIGRATION = ROOT / "sql/migrations/20260908000000_portfolio_command_state_machine.sql"
APP_API_MIGRATION = ROOT / "sql/migrations/20260908010000_app_api_limits.sql"
TELEGRAM_MIGRATION = ROOT / "sql/migrations/20260908020000_telegram_multitenancy.sql"
TELEGRAM_WEBHOOK_MIGRATION = ROOT / "sql/migrations/20260908030000_telegram_webhook_runtime.sql"
PROVIDER_EVIDENCE_MIGRATION = ROOT / "sql/migrations/20260908040000_provider_runs_and_evidence.sql"
RESEARCH_RUN_MIGRATION = ROOT / "sql/migrations/20260909000000_research_run_api.sql"
RETENTION_RECOVERY_MIGRATION = ROOT / "sql/migrations/20260910000000_retention_recovery.sql"


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
    pytest.skip("PostgreSQL 17 test binaries are unavailable")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="session")
def foundation_database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pg-foundation")
    data = directory / "data"
    log = directory / "postgres.log"
    port = _free_port()
    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    subprocess.run(
        [
            initdb,
            "--no-sync",
            "--auth-local=trust",
            "--auth-host=trust",
            "--username=postgres",
            str(data),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
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
    connection = None
    try:
        connection = psycopg.connect(
            dbname="postgres",
            user="postgres",
            host="127.0.0.1",
            port=port,
            autocommit=True,
        )
        connection.execute("CREATE ROLE anon NOLOGIN")
        connection.execute("CREATE ROLE authenticated NOLOGIN")
        connection.execute("CREATE ROLE service_role NOLOGIN")
        connection.execute("CREATE SCHEMA auth")
        connection.execute("CREATE SCHEMA vault")
        connection.execute(
            "CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE)"
        )
        connection.execute("CREATE TABLE vault.secrets (id uuid PRIMARY KEY, secret text)")
        connection.execute("CREATE VIEW vault.decrypted_secrets AS SELECT id, secret FROM vault.secrets")
        connection.execute(
            """
            CREATE FUNCTION vault.create_secret(new_secret text, new_name text, new_description text)
            RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER AS $$
            DECLARE secret_id uuid := gen_random_uuid();
            BEGIN INSERT INTO vault.secrets(id, secret) VALUES (secret_id, new_secret); RETURN secret_id; END
            $$;
            CREATE FUNCTION vault.delete_secret(secret_id uuid)
            RETURNS void LANGUAGE sql SECURITY DEFINER AS $$ DELETE FROM vault.secrets WHERE id = secret_id $$;
            """
        )
        connection.execute((ROOT / "sql/legacy_schema.sql").read_text())
        if FOUNDATION_MIGRATION.exists():
            connection.execute(FOUNDATION_MIGRATION.read_text())
        yield connection
    finally:
        if connection is not None:
            connection.close()
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.fixture(scope="session")
def tenant_database(tmp_path_factory):
    from scripts.migrate_single_owner_to_tenant import migrate_connection

    directory = tmp_path_factory.mktemp("pg-tenant")
    data = directory / "data"
    log = directory / "postgres.log"
    port = _free_port()
    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    subprocess.run(
        [
            initdb,
            "--no-sync",
            "--auth-local=trust",
            "--auth-host=trust",
            "--username=postgres",
            str(data),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
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
    connection = None
    try:
        connection = psycopg.connect(
            dbname="postgres",
            user="postgres",
            host="127.0.0.1",
            port=port,
            autocommit=True,
        )
        connection.execute("CREATE ROLE anon NOLOGIN")
        connection.execute("CREATE ROLE authenticated NOLOGIN")
        connection.execute("CREATE ROLE service_role NOLOGIN")
        connection.execute("CREATE SCHEMA auth")
        connection.execute("CREATE SCHEMA vault")
        connection.execute(
            "CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE)"
        )
        connection.execute("CREATE TABLE vault.secrets (id uuid PRIMARY KEY, secret text)")
        connection.execute("CREATE VIEW vault.decrypted_secrets AS SELECT id, secret FROM vault.secrets")
        connection.execute(
            """
            CREATE FUNCTION vault.create_secret(new_secret text, new_name text, new_description text)
            RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER AS $$
            DECLARE secret_id uuid := gen_random_uuid();
            BEGIN INSERT INTO vault.secrets(id, secret) VALUES (secret_id, new_secret); RETURN secret_id; END
            $$;
            CREATE FUNCTION vault.delete_secret(secret_id uuid)
            RETURNS void LANGUAGE sql SECURITY DEFINER AS $$ DELETE FROM vault.secrets WHERE id = secret_id $$;
            """
        )
        connection.execute(
            """
            CREATE FUNCTION auth.uid() RETURNS uuid
            LANGUAGE sql STABLE
            AS $$
              SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$
            """
        )
        connection.execute("GRANT USAGE ON SCHEMA auth TO authenticated, anon")
        connection.execute("GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated, anon")
        connection.execute((ROOT / "sql/legacy_schema.sql").read_text())
        connection.execute(FOUNDATION_MIGRATION.read_text())

        owner_a = "11111111-1111-4111-8111-111111111111"
        owner_b = "22222222-2222-4222-8222-222222222222"
        connection.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, 'a@example.com'), (%s, 'b@example.com')",
            (owner_a, owner_b),
        )
        connection.execute(
            "INSERT INTO public.holdings (ticker, shares, avg_cost, bucket) VALUES ('TSTAAA', 1, 10, 'growth')"
        )
        connection.execute(
            "INSERT INTO public.transactions (ticker, side, qty, price) VALUES ('TSTAAA', 'buy', 1, 10)"
        )
        connection.execute(
            """
            INSERT INTO public.owner_investment_plans
              (ticker, bucket, amount, cadence, next_due_on, due_day)
            VALUES ('TSTVTI', 'core', 300, 'monthly', DATE '2026-09-21', 21)
            """
        )
        migrate_connection(
            connection, owner_email="a@example.com", rollback_only=False
        )
        connection.execute(
            "INSERT INTO app.profiles (id, display_name, status) VALUES (%s, 'Owner B', 'active')",
            (owner_b,),
        )
        connection.execute(
            "INSERT INTO app.notification_preferences (owner_id) VALUES (%s)",
            (owner_b,),
        )
        connection.execute(
            """
            INSERT INTO app.holdings (owner_id, ticker, shares, avg_cost, bucket)
            VALUES (%s, 'TSTAAA', 9, 20, 'core')
            """,
            (owner_b,),
        )
        connection.execute(
            """
            INSERT INTO app.transactions (owner_id, ticker, side, qty, price)
            VALUES (%s, 'TSTAAA', 'buy', 9, 20)
            """,
            (owner_b,),
        )
        connection.execute(
            """
            INSERT INTO app.owner_investment_plans
              (owner_id, ticker, bucket, amount, cadence, next_due_on, due_day)
            VALUES (%s, 'TSTVTI', 'core', 500, 'monthly', DATE '2026-09-22', 22)
            """,
            (owner_b,),
        )
        connection.execute(
            """
            INSERT INTO app.agent_connections
              (owner_id, provider, credential_type, inbound_token_digest,
               outbound_trigger_secret_id, status)
            VALUES
              (%s, 'claude', 'claude_routine_v1', decode(repeat('aa', 32), 'hex'), %s, 'active'),
              (%s, 'claude', 'claude_routine_v1', decode(repeat('bb', 32), 'hex'), %s, 'active')
            """,
            (
                owner_a,
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                owner_b,
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            ),
        )
        connections = connection.execute(
            "SELECT owner_id, id FROM app.agent_connections ORDER BY owner_id"
        ).fetchall()
        for owner_id, connection_id in connections:
            connection.execute(
                """
                INSERT INTO app.analysis_schedules (owner_id, primary_connection_id)
                VALUES (%s, %s)
                """,
                (owner_id, connection_id),
            )
        connection.execute(
            """
            INSERT INTO app.telegram_links
              (owner_id, telegram_chat_id, telegram_user_id)
            VALUES (%s, 1001, 2001), (%s, 1002, 2002)
            """,
            (owner_a, owner_b),
        )
        if RLS_MIGRATION.exists():
            connection.execute(RLS_MIGRATION.read_text())
        if LEDGER_MIGRATION.exists():
            connection.execute(LEDGER_MIGRATION.read_text())
        if COMMAND_MIGRATION.exists():
            connection.execute(COMMAND_MIGRATION.read_text())
        if APP_API_MIGRATION.exists():
            connection.execute(APP_API_MIGRATION.read_text())
        if TELEGRAM_MIGRATION.exists():
            connection.execute(TELEGRAM_MIGRATION.read_text())
        if TELEGRAM_WEBHOOK_MIGRATION.exists():
            connection.execute(TELEGRAM_WEBHOOK_MIGRATION.read_text())
        if PROVIDER_EVIDENCE_MIGRATION.exists():
            connection.execute(PROVIDER_EVIDENCE_MIGRATION.read_text())
        if RESEARCH_RUN_MIGRATION.exists():
            connection.execute(RESEARCH_RUN_MIGRATION.read_text())
        if RETENTION_RECOVERY_MIGRATION.exists():
            connection.execute(RETENTION_RECOVERY_MIGRATION.read_text())
        yield connection
    finally:
        if connection is not None:
            connection.close()
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
