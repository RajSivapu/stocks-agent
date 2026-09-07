from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest
from pglast import parse_sql
from pglast.stream import RawStream


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261005_market_wide_discovery.sql"
SCHEMA = ROOT / "sql" / "schema.sql"

TABLES = (
    "market_reference_manifests",
    "market_security_reference_revisions",
    "market_discovery_stage_tasks",
    "market_exposure_facts",
    "market_theme_episode_revisions",
    "market_research_nominations",
)
IMMUTABLE_TABLES = (
    "market_reference_manifests",
    "market_security_reference_revisions",
    "market_exposure_facts",
    "market_theme_episode_revisions",
)
TRANSITION_TABLES = (
    "market_discovery_stage_tasks",
    "market_research_nominations",
)
RPCS = (
    "record_market_discovery_reference",
    "checkpoint_market_discovery_stage",
    "read_market_discovery_context",
)


def parsed_statements(path: Path) -> list[str]:
    """Parse with PostgreSQL's grammar before inspecting normalized statements."""
    return [RawStream()(statement) for statement in parse_sql(path.read_text())]


def statements_starting(statements: list[str], prefix: str) -> list[str]:
    return [statement for statement in statements if statement.upper().startswith(prefix.upper())]


def test_discovery_migration_adds_all_ledgers_and_protects_them():
    statements = parsed_statements(MIGRATION)
    normalized = "\n".join(statements)
    for table in TABLES:
        assert statements_starting(statements, f"CREATE TABLE IF NOT EXISTS public.{table}")
        assert statements_starting(statements, f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        assert f"public.{table}" in normalized
        assert f"ON TABLE public.{table} TO stock_agent_release_reader" in normalized

    for table in IMMUTABLE_TABLES:
        assert statements_starting(statements, f"CREATE TRIGGER {table}_append_only")
    for table in TRANSITION_TABLES:
        assert statements_starting(statements, f"CREATE TRIGGER {table}_transition_guard")


def test_discovery_schema_preserves_task_identity_and_bounded_state():
    statements = parsed_statements(MIGRATION)
    task_table = statements_starting(
        statements, "CREATE TABLE IF NOT EXISTS public.market_discovery_stage_tasks"
    )[0]
    for column in (
        "capability_id",
        "provider",
        "query_kind",
        "query_hash",
        "dependency_ids",
        "requested_window",
        "state",
        "attempt_count",
        "request_budget",
        "result",
    ):
        assert column in task_table
    assert "attempt_count BETWEEN 0 AND 10" in task_table
    assert "request_budget BETWEEN 0 AND 100" in task_table
    assert "UNIQUE (run_id, stage, query_hash)" in task_table

    guards = "\n".join(
        statement for statement in statements
        if "market_discovery_stage_task_transition" in statement
    )
    assert "discovery task identity mismatch" in guards
    assert "planned" in guards and "attempting" in guards
    for terminal in ("succeeded", "failed", "deferred", "uncertain"):
        assert terminal in guards


def test_discovery_rpcs_are_static_security_definers_with_least_privilege():
    statements = parsed_statements(MIGRATION)
    for rpc in RPCS:
        functions = statements_starting(
            statements, f"CREATE OR REPLACE FUNCTION public.{rpc}("
        )
        assert len(functions) == 1
        body = functions[0]
        assert "SECURITY DEFINER" in body
        assert "SET search_path TO 'pg_catalog'" in body
        assert "EXECUTE format" not in body
        assert statements_starting(
            statements, f"REVOKE ALL PRIVILEGES ON FUNCTION public.{rpc} ("
        )
        assert statements_starting(statements, f"GRANT EXECUTE ON FUNCTION public.{rpc} (")

    normalized = "\n".join(statements)
    assert "GRANT EXECUTE ON FUNCTION public.record_market_discovery_reference (uuid, jsonb) TO service_role" in normalized
    assert "GRANT EXECUTE ON FUNCTION public.checkpoint_market_discovery_stage (uuid, jsonb) TO service_role" in normalized
    assert "GRANT EXECUTE ON FUNCTION public.read_market_discovery_context (uuid, integer) TO service_role" in normalized
    assert "GRANT SELECT" in normalized and "TO stock_agent_dashboard" in normalized
    assert "GRANT INSERT" not in normalized


def test_schema_appends_the_new_immutable_migration_verbatim():
    migration = MIGRATION.read_text()
    schema = SCHEMA.read_text()
    assert schema.endswith(migration)


def test_postgresql_parser_rejects_a_malformed_discovery_fixture():
    with pytest.raises(Exception):
        parse_sql("CREATE TABLE public.market_discovery_stage_tasks (id UUID,,);")


@pytest.fixture(scope="module")
def discovery_db():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="market-discovery-sql-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        subprocess.run(
            [binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"],
            check=True, capture_output=True,
        )
        subprocess.run(
            [binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
             "-o", f"-k {root} -h '' -p {port}", "-w", "start"],
            check=True, capture_output=True,
        )
        connection = None
        try:
            connection = psycopg.connect(f"host={root} port={port} dbname=postgres", autocommit=True)
            connection.execute(
                "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;"
                "CREATE ROLE stock_agent_dashboard; CREATE ROLE stock_agent_release_reader;"
                "CREATE ROLE stock_agent_release_reader_runtime;"
                "CREATE TABLE public.analysis_runs(id uuid PRIMARY KEY,status text NOT NULL);"
                "CREATE TABLE public.market_intelligence_runs(id uuid PRIMARY KEY REFERENCES public.analysis_runs(id))"
            )
            connection.execute(MIGRATION.read_text())
            yield connection
        finally:
            if connection is not None:
                connection.close()
            subprocess.run(
                [binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True,
            )


def seeded_run(connection) -> str:
    run_id = str(uuid.uuid4())
    connection.execute("INSERT INTO public.analysis_runs VALUES(%s,'running')", (run_id,))
    connection.execute("INSERT INTO public.market_intelligence_runs VALUES(%s)", (run_id,))
    return run_id


def task_payload(task_id: str, *, state: str, attempt_count: int, query_hash: str = "a" * 64):
    return {
        "task": {
            "id": task_id, "stage": "signals", "provider": "gdelt",
            "capability_id": "gdelt_theme_search", "query_kind": "theme_search",
            "query_hash": query_hash, "dependency_ids": [],
            "requested_window": {"start": "2026-09-06T00:00:00Z", "end": "2026-09-06T01:00:00Z"},
            "state": state, "attempt_count": attempt_count, "request_budget": 1, "result": {},
        },
        "exposure_facts": [], "theme_episode_revisions": [], "research_nominations": [],
    }


def test_discovery_task_transition_rejects_reselection_after_attempt(discovery_db):
    run_id, task_id = seeded_run(discovery_db), str(uuid.uuid4())
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(task_payload(task_id, state="planned", attempt_count=0))),
    )
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(task_payload(task_id, state="attempting", attempt_count=1))),
    )
    context = discovery_db.execute(
        "SELECT public.read_market_discovery_context(%s,100)", (run_id,),
    ).fetchone()[0]
    assert set(context["tasks"][0]) == {
        "id", "stage", "provider", "capability_id", "query_kind", "query_hash",
        "dependency_ids", "requested_window", "state", "attempt_count", "request_budget", "result",
    }

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="discovery task identity mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(task_payload(task_id, state="succeeded", attempt_count=1, query_hash="b" * 64))),
        )


def test_discovery_reference_replay_is_exact_and_altered_child_fails_closed(discovery_db):
    run_id = seeded_run(discovery_db)
    manifest_id, security_id = str(uuid.uuid4()), str(uuid.uuid4())
    payload = {
        "manifest": {
            "id": manifest_id, "reference_version": "us-listed:v1", "revision": 1,
            "capability_version": 1, "taxonomy_version": 1, "source_hash": "c" * 64,
            "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
            "manifest": {"universe": "eligible_us_listed"}, "content_hash": "d" * 64,
        },
        "security_revisions": [{
            "id": security_id, "manifest_id": manifest_id, "revision": 1,
            "security_id": "NASDAQ:TEST", "entity_id": "CIK:0000000001", "ticker": "TEST",
            "exchange": "NASDAQ", "instrument_type": "COMMON_STOCK", "eligible": True,
            "exclusion_reasons": [], "aliases": ["Test Corp"], "source_ids": ["nasdaq-listed"],
            "valid_from": "2026-09-06T00:00:00Z", "valid_to": None, "content_hash": "e" * 64,
        }],
    }

    first = discovery_db.execute(
        "SELECT public.record_market_discovery_reference(%s,%s)", (run_id, Jsonb(payload)),
    ).fetchone()[0]
    replay = discovery_db.execute(
        "SELECT public.record_market_discovery_reference(%s,%s)", (run_id, Jsonb(payload)),
    ).fetchone()[0]
    assert first["duplicate"] is False
    assert replay["duplicate"] is True
    context = discovery_db.execute(
        "SELECT public.read_market_discovery_context(%s,100)", (run_id,),
    ).fetchone()[0]
    assert context["security_revisions"][0]["id"] == security_id

    payload["security_revisions"][0]["ticker"] = "DRIFT"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="idempotency mismatch"):
        discovery_db.execute(
            "SELECT public.record_market_discovery_reference(%s,%s)", (run_id, Jsonb(payload)),
        )


def test_discovery_stage_replay_is_exact_and_altered_child_fails_closed(discovery_db):
    run_id, task_id = seeded_run(discovery_db), str(uuid.uuid4())
    payload = task_payload(task_id, state="planned", attempt_count=0)
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    payload["task"].update(state="attempting", attempt_count=1)
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    payload["task"].update(state="succeeded", result={"episode_count": 1})
    payload["theme_episode_revisions"] = [{
        "id": str(uuid.uuid4()), "theme_id": "power_grid", "revision": 1,
        "episode": {"summary": "grid investment"}, "source_ids": ["gdelt:fixture"],
        "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
        "content_hash": "f" * 64,
    }]
    first = discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]
    replay = discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]
    assert first["duplicate"] is False
    assert replay["duplicate"] is True

    payload["theme_episode_revisions"][0]["episode"] = {"summary": "drift"}
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="idempotency mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )
