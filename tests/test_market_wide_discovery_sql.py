from pathlib import Path
import copy
import hashlib
import json
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

from scripts.verify_market_intelligence_migration import collect_snapshot
from lib.intelligence.universe import (
    reference_manifest_semantic_document,
    security_revision_semantic_document,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261005_market_wide_discovery.sql"
TRANSFER_MIGRATION = ROOT / "sql" / "migrations" / "20261006_reference_snapshot_transfer.sql"
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
    assert migration in schema
    assert schema.endswith(TRANSFER_MIGRATION.read_text())


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
                "CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions;"
                "CREATE TABLE public.analysis_runs(id uuid PRIMARY KEY,status text NOT NULL);"
                "CREATE TABLE public.market_intelligence_runs(id uuid PRIMARY KEY REFERENCES public.analysis_runs(id))"
            )
            connection.execute(MIGRATION.read_text())
            connection.execute(TRANSFER_MIGRATION.read_text())
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


def reference_payload(*, manifest_id: str, securities: list[tuple[str, str, str]]):
    manifest = {
        "id": manifest_id, "reference_version": f"fixture:{manifest_id}", "revision": 1,
        "capability_version": 1, "taxonomy_version": 1, "source_hash": "c" * 64,
        "valid_from": "2026-09-06T00:00:00.000Z", "valid_to": None,
        "manifest": {"universe": "eligible_us_listed"},
    }
    manifest["content_hash"] = hashlib.sha256(json.dumps(
        reference_manifest_semantic_document(manifest), ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    revisions = [{
            "id": security_id, "manifest_id": manifest_id, "revision": 1,
            "security_id": f"NASDAQ:{ticker}", "entity_id": f"CIK:{index:010d}",
            "ticker": ticker, "exchange": "NASDAQ", "instrument_type": "COMMON_STOCK",
            "eligible": True, "exclusion_reasons": [], "aliases": [f"{ticker} Corp"],
            "source_ids": [f"nasdaq-listed:{ticker}"],
            "valid_from": "2026-09-06T00:00:00.000Z", "valid_to": None,
        } for index, (security_id, ticker, _content_hash) in enumerate(securities, start=1)]
    for revision in revisions:
        revision["content_hash"] = hashlib.sha256(json.dumps(
            security_revision_semantic_document(revision), ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
    return {"manifest": manifest, "security_revisions": revisions}


def record_reference(connection, run_id: str, *, ticker: str = "TEST") -> str:
    security_id = str(uuid.uuid4())
    payload = reference_payload(
        manifest_id=str(uuid.uuid4()), securities=[(security_id, ticker, "e" * 64)],
    )
    connection.execute(
        "SELECT public.record_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    return security_id


def create_attempting_task(connection, run_id: str, *, stage: str) -> tuple[str, dict]:
    task_id = str(uuid.uuid4())
    payload = task_payload(task_id, state="planned", attempt_count=0)
    payload["task"].update(
        stage=stage,
        capability_id=f"gdelt_{stage}_fixture",
        query_kind="theme_search",
        query_hash=uuid.uuid4().hex * 2,
    )
    connection.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    payload["task"].update(state="attempting", attempt_count=1)
    connection.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    return task_id, payload


def exposure_fact_row(*, row_id: str, security_id: str, content_hash: str):
    return {
        "id": row_id, "security_revision_id": security_id,
        "theme_episode_revision_id": None, "exposure_kind": "filing",
        "fact": {"basis": "10-K"}, "source_ids": [f"sec:{row_id}"],
        "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
        "content_hash": content_hash,
    }


def persist_exposure_fact(connection, run_id: str, security_id: str) -> str:
    exposure_id = str(uuid.uuid4())
    _task_id, payload = create_attempting_task(connection, run_id, stage="enrich")
    payload["task"].update(state="succeeded", result={"fact_count": 1})
    payload["exposure_facts"] = [exposure_fact_row(
        row_id=exposure_id, security_id=security_id, content_hash=uuid.uuid4().hex * 2,
    )]
    connection.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    return exposure_id


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
    manifest_id, security_id, second_security_id = (
        str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    )
    payload = reference_payload(
        manifest_id=manifest_id,
        securities=[
            (security_id, "TEST", "e" * 64),
            (second_security_id, "NEXT", "f" * 64),
        ],
    )

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
    assert {row["id"] for row in context["security_revisions"]} == {
        security_id, second_security_id,
    }

    incomplete = {
        "manifest": payload["manifest"],
        "security_revisions": payload["security_revisions"][:1],
    }
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="idempotency mismatch"):
        discovery_db.execute(
            "SELECT public.record_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(incomplete)),
        )

    duplicate_child_replay = copy.deepcopy(payload)
    duplicate_child_replay["security_revisions"] = [
        copy.deepcopy(payload["security_revisions"][0]),
        copy.deepcopy(payload["security_revisions"][0]),
    ]
    duplicate_child_replay["security_revisions"][1]["id"] = security_id.upper()
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="duplicate security revision id"):
        discovery_db.execute(
            "SELECT public.record_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(duplicate_child_replay)),
        )

    payload["security_revisions"][0]["ticker"] = "DRIFT"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="security hash mismatch"):
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


@pytest.mark.parametrize(("stage", "collection"), [
    ("signals", "theme_episode_revisions"),
    ("enrich", "exposure_facts"),
    ("screen", "research_nominations"),
])
def test_discovery_stage_replay_rejects_duplicate_child_identity(
        discovery_db, stage, collection):
    run_id = seeded_run(discovery_db)
    manifest_id = str(uuid.uuid4())
    security_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    reference = reference_payload(
        manifest_id=manifest_id,
        securities=[
            (security_ids[0], "FIRST", uuid.uuid4().hex * 2),
            (security_ids[1], "SECOND", uuid.uuid4().hex * 2),
        ],
    )
    discovery_db.execute(
        "SELECT public.record_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(reference)),
    )
    exposure_id = persist_exposure_fact(discovery_db, run_id, security_ids[0])
    _task_id, payload = create_attempting_task(discovery_db, run_id, stage=stage)
    payload["task"].update(state="succeeded", result={"row_count": 2})
    if collection == "theme_episode_revisions":
        children = [{
            "id": str(uuid.uuid4()), "theme_id": theme_id, "revision": 1,
            "episode": {"summary": theme_id}, "source_ids": [f"gdelt:{theme_id}"],
            "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
            "content_hash": uuid.uuid4().hex * 2,
        } for theme_id in ("power_grid", "data_centers")]
    elif collection == "exposure_facts":
        children = [exposure_fact_row(
            row_id=str(uuid.uuid4()), security_id=security_ids[index],
            content_hash=uuid.uuid4().hex * 2,
        ) for index in range(2)]
    else:
        children = [{
            "id": str(uuid.uuid4()), "security_revision_id": security_ids[index],
            "theme_episode_revision_id": None, "exposure_fact_ids": [exposure_id],
            "state": "nominated", "rationale": {"basis": f"candidate-{index}"},
        } for index in range(2)]
    payload[collection] = children
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    replay = discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]
    assert replay["duplicate"] is True

    duplicate_child_replay = copy.deepcopy(payload)
    duplicate_child_replay[collection] = [
        copy.deepcopy(children[0]), copy.deepcopy(children[0]),
    ]
    duplicate_child_replay[collection][1]["id"] = children[0]["id"].upper()
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="duplicate result child id"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(duplicate_child_replay)),
        )


def test_discovery_checkpoint_rejects_duplicate_nomination_exposure_fact_ids(discovery_db):
    run_id = seeded_run(discovery_db)
    security_id = record_reference(discovery_db, run_id)
    exposure_id = persist_exposure_fact(discovery_db, run_id, security_id)
    _task_id, payload = create_attempting_task(discovery_db, run_id, stage="screen")
    payload["task"].update(state="succeeded", result={"nomination_count": 1})
    payload["research_nominations"] = [{
        "id": str(uuid.uuid4()), "security_revision_id": security_id,
        "theme_episode_revision_id": None,
        "exposure_fact_ids": [exposure_id, exposure_id.upper()],
        "state": "nominated", "rationale": {"basis": "research"},
    }]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="duplicate exposure fact id"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )


def test_discovery_checkpoint_rejects_valid_cross_run_dependency(discovery_db):
    dependency_run, target_run = seeded_run(discovery_db), seeded_run(discovery_db)
    dependency_id, dependency = create_attempting_task(
        discovery_db, dependency_run, stage="signals",
    )
    dependency["task"].update(state="succeeded", result={"count": 0})
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (dependency_run, Jsonb(dependency)),
    )
    target_id = str(uuid.uuid4())
    target = task_payload(target_id, state="planned", attempt_count=0, query_hash="b" * 64)
    target["task"]["dependency_ids"] = [dependency_id]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="dependency mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (target_run, Jsonb(target)),
        )


def test_discovery_checkpoint_rejects_cross_run_security_and_orphan_exposure(discovery_db):
    source_run, target_run = seeded_run(discovery_db), seeded_run(discovery_db)
    cross_run_security_id = record_reference(discovery_db, source_run, ticker="CROSS")
    _task_id, enrich = create_attempting_task(discovery_db, target_run, stage="enrich")
    enrich["task"].update(state="succeeded", result={"fact_count": 1})
    enrich["exposure_facts"] = [{
        "id": str(uuid.uuid4()), "security_revision_id": cross_run_security_id,
        "theme_episode_revision_id": None, "exposure_kind": "filing",
        "fact": {"basis": "10-K"}, "source_ids": ["sec:fixture"],
        "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
        "content_hash": "1" * 64,
    }]
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="lineage mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (target_run, Jsonb(enrich)),
        )

    same_run_security_id = record_reference(discovery_db, target_run, ticker="LOCAL")
    _task_id, signals = create_attempting_task(discovery_db, source_run, stage="signals")
    cross_run_theme_id = str(uuid.uuid4())
    signals["task"].update(state="succeeded", result={"episode_count": 1})
    signals["theme_episode_revisions"] = [{
        "id": cross_run_theme_id, "theme_id": "power_grid", "revision": 1,
        "episode": {"summary": "source run"}, "source_ids": ["gdelt:source"],
        "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
        "content_hash": "2" * 64,
    }]
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (source_run, Jsonb(signals)),
    )
    enrich["exposure_facts"][0].update(
        security_revision_id=same_run_security_id,
        theme_episode_revision_id=cross_run_theme_id,
        content_hash="3" * 64,
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="lineage mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (target_run, Jsonb(enrich)),
        )

    _task_id, source_enrich = create_attempting_task(discovery_db, source_run, stage="enrich")
    cross_run_exposure_id = str(uuid.uuid4())
    source_enrich["task"].update(state="succeeded", result={"fact_count": 1})
    source_enrich["exposure_facts"] = [{
        "id": cross_run_exposure_id, "security_revision_id": cross_run_security_id,
        "theme_episode_revision_id": None, "exposure_kind": "filing",
        "fact": {"basis": "10-K"}, "source_ids": ["sec:source"],
        "valid_from": "2026-09-06T00:00:00Z", "valid_to": None,
        "content_hash": "4" * 64,
    }]
    discovery_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (source_run, Jsonb(source_enrich)),
    )
    _task_id, screen = create_attempting_task(discovery_db, target_run, stage="screen")
    screen["task"].update(state="succeeded", result={"nomination_count": 1})
    screen["research_nominations"] = [{
        "id": str(uuid.uuid4()), "security_revision_id": same_run_security_id,
        "theme_episode_revision_id": None, "exposure_fact_ids": [cross_run_exposure_id],
        "state": "nominated", "rationale": {"basis": "research"},
    }]
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="lineage mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (target_run, Jsonb(screen)),
        )
    screen["research_nominations"][0]["exposure_fact_ids"] = [str(uuid.uuid4())]
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="lineage mismatch"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (target_run, Jsonb(screen)),
        )


@pytest.mark.parametrize(("terminal_status", "advance"), [
    ("completed", False), ("failed", False), ("completed", True), ("failed", True),
])
def test_discovery_checkpoint_rejects_terminal_parent_run(discovery_db, terminal_status, advance):
    run_id = seeded_run(discovery_db)
    task_id = str(uuid.uuid4())
    payload = task_payload(task_id, state="planned", attempt_count=0)
    if advance:
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )
        payload["task"].update(state="attempting", attempt_count=1)
    discovery_db.execute(
        "UPDATE public.analysis_runs SET status=%s WHERE id=%s", (terminal_status, run_id),
    )

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="intelligence run is not running"):
        discovery_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )


def test_verifier_collects_exact_discovery_relation_column_and_function_grants(discovery_db):
    snapshot = collect_snapshot(discovery_db.cursor(), {})
    discovery_table_grants = {
        (row["table"], row["grantee"], row["privilege"])
        for row in snapshot["table_grants"] if not row["is_owner"]
        and row["table"] in TABLES
    }
    assert discovery_table_grants == {
        (table, "stock_agent_release_reader", "SELECT") for table in TABLES
    }
    discovery_column_grants = {
        (row["table"], row["column"], row["grantee"], row["privilege"])
        for row in snapshot["column_grants"] if not row["is_owner"]
    }
    assert discovery_column_grants == {
        (table, column, "stock_agent_dashboard", "SELECT")
        for table, columns in {
            "market_reference_manifests": (
                "id", "reference_version", "revision", "capability_version", "taxonomy_version",
                "source_hash", "valid_from", "valid_to", "manifest", "content_hash", "created_at",
            ),
            "market_security_reference_revisions": (
                "id", "security_id", "entity_id", "ticker", "exchange", "instrument_type",
                "eligible", "exclusion_reasons", "aliases", "valid_from", "valid_to",
                "content_hash", "created_at",
            ),
            "market_discovery_stage_tasks": (
                "id", "stage", "capability_id", "provider", "query_kind", "query_hash", "state",
                "attempt_count", "request_budget", "created_at", "updated_at",
            ),
            "market_exposure_facts": (
                "id", "security_revision_id", "theme_episode_revision_id", "exposure_kind",
                "fact", "source_ids", "valid_from", "valid_to", "content_hash", "created_at",
            ),
            "market_theme_episode_revisions": (
                "id", "theme_id", "revision", "episode", "source_ids", "valid_from", "valid_to",
                "content_hash", "created_at",
            ),
            "market_research_nominations": (
                "id", "security_revision_id", "theme_episode_revision_id", "exposure_fact_ids",
                "state", "rationale", "created_at", "updated_at",
            ),
        }.items()
        for column in columns
    }
    assert {
        (row["signature"], row["grantee"], row["privilege"])
        for row in snapshot["function_grants"] if not row["is_owner"]
    } == {
        ("record_market_discovery_reference(uuid,jsonb)", "service_role", "EXECUTE"),
        ("checkpoint_market_discovery_stage(uuid,jsonb)", "service_role", "EXECUTE"),
        ("read_market_discovery_context(uuid,integer)", "service_role", "EXECUTE"),
        ("begin_market_discovery_reference(uuid,jsonb,uuid,integer,text)", "service_role", "EXECUTE"),
        ("record_market_discovery_reference_chunk(uuid,jsonb,uuid,integer,text)", "service_role", "EXECUTE"),
        ("finalize_market_discovery_reference(uuid,jsonb,uuid,integer,text)", "service_role", "EXECUTE"),
        ("pin_market_discovery_reference(uuid,jsonb,uuid,integer,text)", "service_role", "EXECUTE"),
        ("read_market_discovery_reference(uuid,jsonb,uuid,integer,text)", "service_role", "EXECUTE"),
    }
    assert snapshot["unexpected_grants"] == []
