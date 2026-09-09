from datetime import datetime, timezone
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest
from pglast import parse_sql
from pglast.stream import RawStream

from lib.intelligence.universe import (
    build_reference_transfer,
    parse_sec_company_tickers,
    security_revision_semantic_document,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261006_reference_snapshot_transfer.sql"
SCHEMA = ROOT / "sql" / "schema.sql"
PREVIOUS = ROOT / "sql" / "migrations" / "20261005_market_wide_discovery.sql"
CURSOR_CONTEXT = ROOT / "sql" / "migrations" / "20261007_discovery_cursor_context.sql"
OFFICIAL_COMPLETION = ROOT / "sql" / "migrations" / "20261008_official_source_completion_contract.sql"
ISSUER_NAMES = ROOT / "sql" / "migrations" / "20261009_reference_issuer_names.sql"
ENRICHMENT = ROOT / "sql" / "migrations" / "20261010_bounded_adaptive_enrichment.sql"
RESTART = ROOT / "sql" / "migrations" / "20261020_reference_transfer_restart.sql"

TABLES = (
    "market_reference_chunk_receipts",
    "market_reference_snapshot_memberships",
    "market_reference_finalization_seals",
    "market_reference_run_bindings",
    "market_reference_predecessor_pins",
    "market_reference_transfer_requests",
)
RPCS = (
    "begin_market_discovery_reference",
    "record_market_discovery_reference_chunk",
    "finalize_market_discovery_reference",
    "pin_market_discovery_reference",
    "read_market_discovery_reference",
)
RPC_OPERATIONS = dict(zip(RPCS, (
    "begin_discovery_reference",
    "record_discovery_reference_chunk",
    "finalize_discovery_reference",
    "pin_discovery_reference",
    "read_discovery_reference",
), strict=True))


def _statements() -> list[str]:
    return [RawStream()(statement) for statement in parse_sql(MIGRATION.read_text())]


def test_transfer_migration_adds_six_protected_ledgers_and_five_static_rpcs():
    statements = _statements()
    normalized = "\n".join(statements)
    for table in TABLES:
        assert any(
            statement.upper().startswith(
                f"CREATE TABLE IF NOT EXISTS public.{table}".upper()
            )
            for statement in statements
        )
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in normalized
        assert f"CREATE TRIGGER {table}_append_only" in normalized
    assert "GRANT SELECT ON TABLE " in normalized
    assert " TO stock_agent_release_reader" in normalized
    for rpc in RPCS:
        matches = [
            statement
            for statement in statements
            if statement.upper().startswith(
                f"CREATE OR REPLACE FUNCTION public.{rpc}(".upper()
            )
        ]
        assert len(matches) == 1
        assert "SECURITY DEFINER" in matches[0]
        assert "SET search_path TO 'pg_catalog'" in matches[0]
        assert "EXECUTE format" not in matches[0]
        assert f"GRANT EXECUTE ON FUNCTION public.{rpc}" in normalized
    assert " TO service_role" in normalized
    assert " TO stock_agent_dashboard" not in normalized


def test_transfer_requests_are_server_claimed_without_using_decision_quotas():
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.claim_market_reference_transfer_request" in sql
    assert "p_encoded_bytes INT" in sql
    assert "p_request_hash TEXT" in sql
    assert "v_count>=160" in sql
    assert "33554432" in sql
    assert "market_request_claims" not in sql
    assert "hour_bucket" not in sql
    assert "market_reference_predecessor_pins" in sql
    assert "binding_role" in sql


def test_transfer_schema_has_exact_chunk_page_snapshot_and_binding_bounds():
    sql = MIGRATION.read_text()
    assert "octet_length(payload::text)<=196608" in sql
    assert "entry_count BETWEEN 0 AND 200" in sql
    assert "chunk_count BETWEEN 1 AND 512" in sql
    assert "security_count BETWEEN 1 AND 15000" in sql
    assert "NOT BETWEEN 1 AND 500" in sql
    assert "octet_length(v_result::text)>196608" in sql
    assert "reference_unavailable" in sql
    assert "reference_stale" in sql
    assert "scope_not_guaranteed" in sql


def test_transfer_finalization_verifies_root_contiguity_and_unique_membership_server_side():
    sql = MIGRATION.read_text()
    assert "reference chunk sequence mismatch" in sql
    assert "reference snapshot root mismatch" in sql
    assert "duplicate reference security identity" in sql
    assert "reference predecessor membership mismatch" in sql
    assert "reference snapshot is not finalized" in sql
    assert "ORDER BY chunk_index" in sql
    assert "ORDER BY m.security_id" in sql


def test_new_schema_tail_is_additive_and_prior_migrations_are_unchanged():
    schema = SCHEMA.read_text()
    assert MIGRATION.read_text() in schema
    assert CURSOR_CONTEXT.read_text() in schema
    assert OFFICIAL_COMPLETION.read_text() in schema
    assert ENRICHMENT.read_text() in schema
    assert RESTART.read_text() in schema
    import subprocess

    prior_at_base = subprocess.run(
        ["git", "show", "e0411ae:sql/migrations/20261005_market_wide_discovery.sql"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert PREVIOUS.read_bytes() == prior_at_base


@pytest.fixture(scope="module")
def transfer_db():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="reference-transfer-sql-") as directory:
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
            connection = psycopg.connect(
                f"host={root} port={port} dbname=postgres", autocommit=True,
            )
            connection.execute(
                "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;"
                "CREATE ROLE stock_agent_dashboard; CREATE ROLE stock_agent_release_reader;"
                "CREATE ROLE stock_agent_release_reader_runtime;"
                "CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions;"
                "CREATE TABLE public.analysis_runs(id uuid PRIMARY KEY,status text NOT NULL);"
                "CREATE TABLE public.market_intelligence_runs(id uuid PRIMARY KEY REFERENCES public.analysis_runs(id))"
            )
            connection.execute(PREVIOUS.read_text())
            connection.execute(MIGRATION.read_text())
            connection.execute(ISSUER_NAMES.read_text())
            connection.execute(RESTART.read_text())
            yield connection
        finally:
            if connection is not None:
                connection.close()
            subprocess.run(
                [binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True,
            )


def _run(connection) -> str:
    run_id = str(uuid.uuid4())
    connection.execute("INSERT INTO public.analysis_runs VALUES(%s,'running')", (run_id,))
    connection.execute("INSERT INTO public.market_intelligence_runs VALUES(%s)", (run_id,))
    return run_id


def _transfer(
    run_id: str,
    count: int,
    retrieved_at: str,
    capability_id: str,
    *,
    semantic_encoding_version: int = 1,
    shared_cik: bool = False,
):
    source = json.dumps({
        str(index): {
            "cik_str": 1 if shared_cik else index + 1,
            "ticker": f"T{index:05d}",
            "title": "Fixture Company" if shared_cik else f"Fixture Company {index}",
        }
        for index in range(count)
    }, separators=(",", ":")).encode()
    snapshot = parse_sec_company_tickers(
        source, retrieved_at=datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")),
    )
    return build_reference_transfer(
        snapshot, run_id=run_id, capability_version=1, taxonomy_version=1,
        capability_id=capability_id,
        semantic_encoding_version=semantic_encoding_version,
    )


def _rpc(connection, function: str, run_id: str, payload, *, request_id=None):
    operation = RPC_OPERATIONS[function]
    request_id = request_id or str(uuid.uuid4())
    envelope = {
        "dry_run": False,
        "operation": operation,
        "payload": payload,
        "request_id": request_id,
        "run_id": run_id,
        "schema_version": 1,
    }
    encoded = json.dumps(
        envelope, allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode()
    return connection.execute(
        f"SELECT public.{function}(%s,%s,%s,%s,%s)",
        (run_id, Jsonb(payload), request_id, len(encoded), hashlib.sha256(encoded).hexdigest()),
    ).fetchone()[0]


def _pin_predecessor(connection, run_id: str, capability: str, as_of: str):
    return _rpc(connection, "pin_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "predecessor",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": as_of,
    })


def _upload(connection, run_id: str, transfer, *, pin_predecessor: bool = True):
    capability = transfer.begin["capability_id"]
    if pin_predecessor:
        _pin_predecessor(
            connection, run_id, capability,
            transfer.begin["manifest"]["valid_from"],
        )
    predecessor = connection.execute(
        "SELECT manifest_id FROM public.market_reference_predecessor_pins "
        "WHERE run_id=%s AND capability_id=%s", (run_id, capability),
    ).fetchone()[0]
    transfer.begin["predecessor_manifest_id"] = str(predecessor) if predecessor else None
    begin = _rpc(connection, "begin_market_discovery_reference", run_id, transfer.begin)
    for chunk in transfer.chunks:
        _rpc(connection, "record_market_discovery_reference_chunk", run_id, chunk)
    finalized = _rpc(
        connection, "finalize_market_discovery_reference", run_id, {
            "manifest_id": transfer.begin["manifest"]["id"],
            "root_hash": transfer.begin["root_hash"],
        },
    )
    return begin, finalized


def _chunk_hash(entries) -> str:
    framed = "\n".join(
        "\x1f".join((row["security_id"], row["id"], row["content_hash"]))
        for row in entries
    )
    return hashlib.sha256(framed.encode()).hexdigest()


def test_manifest_semantic_hash_is_recomputed_before_first_begin(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_manifest_hash",
    )
    forged = copy.deepcopy(transfer.begin)
    forged["manifest"]["manifest"]["source_url"] = "https://forged.invalid/reference"
    _pin_predecessor(
        transfer_db, run_id, forged["capability_id"], forged["manifest"]["valid_from"],
    )

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="manifest hash"):
        _rpc(transfer_db, "begin_market_discovery_reference", run_id, forged)


def test_security_semantic_hash_is_recomputed_before_first_chunk_write(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_revision_hash",
    )
    forged_begin = copy.deepcopy(transfer.begin)
    forged_chunk = copy.deepcopy(transfer.chunks[0])
    forged_chunk["entries"][0]["ticker"] = "DRIFT"
    forged_chunk["chunk_hash"] = _chunk_hash(forged_chunk["entries"])
    forged_begin["root_hash"] = hashlib.sha256(
        forged_chunk["chunk_hash"].encode()
    ).hexdigest()
    _pin_predecessor(
        transfer_db, run_id, forged_begin["capability_id"],
        forged_begin["manifest"]["valid_from"],
    )
    _rpc(transfer_db, "begin_market_discovery_reference", run_id, forged_begin)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="security hash"):
        _rpc(transfer_db, "record_market_discovery_reference_chunk", run_id, forged_chunk)


def test_v2_reference_round_trip_returns_bound_issuer_names(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_issuer_names_round_trip"
    transfer = _transfer(
        run_id,
        2,
        "2026-09-06T12:00:00Z",
        capability,
        semantic_encoding_version=2,
        shared_cik=True,
    )
    _upload(transfer_db, run_id, transfer)
    _rpc(transfer_db, "pin_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    })

    page = _rpc(transfer_db, "read_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "after_security_id": None,
        "limit": 500,
    })

    assert page["binding"]["issuer_names_status"] == "available"
    assert len(page["securities"]) == 2
    assert all(row["semantic_encoding_version"] == 2 for row in page["securities"])
    assert page["securities"][0]["issuer_names"] == page["securities"][1]["issuer_names"]


def test_v1_reference_round_trip_marks_issuer_names_unavailable(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_legacy_name_limit"
    transfer = _transfer(run_id, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, run_id, transfer)
    _rpc(transfer_db, "pin_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    })

    page = _rpc(transfer_db, "read_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "after_security_id": None,
        "limit": 500,
    })

    assert page["binding"]["issuer_names_status"] == "issuer_names_unavailable"
    assert "semantic_encoding_version" not in page["securities"][0]
    assert "issuer_names" not in page["securities"][0]


def test_v2_finalization_rejects_different_names_for_same_entity_across_chunks(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_cross_chunk_names"
    transfer = _transfer(
        run_id,
        89,
        "2026-09-06T12:00:00Z",
        capability,
        semantic_encoding_version=2,
        shared_cik=True,
    )
    assert len(transfer.chunks) == 2
    forged = copy.deepcopy(transfer)
    entry = forged.chunks[1]["entries"][0]
    entry["issuer_names"] = {
        "canonical_name": "Different Company",
        "observed_names": ["Different Company"],
        "former_names": [],
    }
    entry["content_hash"] = hashlib.sha256(json.dumps(
        security_revision_semantic_document(entry),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()
    forged.chunks[1]["chunk_hash"] = _chunk_hash(forged.chunks[1]["entries"])
    forged.begin["root_hash"] = hashlib.sha256("".join(
        chunk["chunk_hash"] for chunk in forged.chunks
    ).encode()).hexdigest()
    _pin_predecessor(
        transfer_db,
        run_id,
        capability,
        forged.begin["manifest"]["valid_from"],
    )
    _rpc(transfer_db, "begin_market_discovery_reference", run_id, forged.begin)
    for chunk in forged.chunks:
        _rpc(transfer_db, "record_market_discovery_reference_chunk", run_id, chunk)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="identical issuer names"):
        _rpc(transfer_db, "finalize_market_discovery_reference", run_id, {
            "manifest_id": forged.begin["manifest"]["id"],
            "root_hash": forged.begin["root_hash"],
        })


def test_legacy_reference_rpc_recomputes_semantic_hashes_before_first_write(transfer_db):
    manifest_run_id = _run(transfer_db)
    manifest_transfer = _transfer(
        manifest_run_id, 1, "2026-09-06T12:00:00Z",
        "sec_company_tickers_legacy_manifest_hash",
    )
    forged_manifest = copy.deepcopy(manifest_transfer.begin["manifest"])
    forged_manifest["manifest"]["source_url"] = "https://forged.invalid/reference"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="manifest hash"):
        transfer_db.execute(
            "SELECT public.record_market_discovery_reference(%s,%s)",
            (manifest_run_id, Jsonb({
                "manifest": forged_manifest,
                "security_revisions": manifest_transfer.chunks[0]["entries"],
            })),
        )

    security_run_id = _run(transfer_db)
    security_transfer = _transfer(
        security_run_id, 1, "2026-09-06T12:00:00Z",
        "sec_company_tickers_legacy_security_hash",
    )
    forged_security = copy.deepcopy(security_transfer.chunks[0]["entries"])
    forged_security[0]["ticker"] = "DRIFT"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="security hash"):
        transfer_db.execute(
            "SELECT public.record_market_discovery_reference(%s,%s)",
            (security_run_id, Jsonb({
                "manifest": security_transfer.begin["manifest"],
                "security_revisions": forged_security,
            })),
        )


def test_partial_snapshot_is_invisible_and_unknown_outcome_retries_are_exact(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(run_id, 401, "2026-09-06T12:00:00Z", "sec_company_tickers_partial")
    _pin_predecessor(transfer_db, run_id, transfer.begin["capability_id"],
                     transfer.begin["manifest"]["valid_from"])
    begin_request_id = str(uuid.uuid4())
    first = _rpc(transfer_db, "begin_market_discovery_reference", run_id,
                 transfer.begin, request_id=begin_request_id)
    replay = _rpc(transfer_db, "begin_market_discovery_reference", run_id,
                  transfer.begin, request_id=begin_request_id)
    assert first["duplicate"] is False and replay["duplicate"] is True
    chunk_request_id = str(uuid.uuid4())
    _rpc(transfer_db, "record_market_discovery_reference_chunk", run_id,
         transfer.chunks[0], request_id=chunk_request_id)
    chunk_replay = _rpc(
        transfer_db, "record_market_discovery_reference_chunk", run_id,
        transfer.chunks[0], request_id=chunk_request_id,
    )
    assert chunk_replay["duplicate"] is True

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="chunk sequence mismatch"):
        _rpc(transfer_db, "finalize_market_discovery_reference", run_id, {
                "manifest_id": transfer.begin["manifest"]["id"],
                "root_hash": transfer.begin["root_hash"],
            })
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not finalized"):
        _rpc(transfer_db, "pin_market_discovery_reference", run_id, {
                "capability_id": "sec_company_tickers_partial",
                "binding_role": "current",
                "manifest_id": transfer.begin["manifest"]["id"],
                "reference_status": "healthy",
                "reference_as_of": "2026-09-06T12:00:01Z",
            })
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_manifests WHERE id=%s",
        (transfer.begin["manifest"]["id"],),
    ).fetchone()[0] == 0

    altered = copy.deepcopy(transfer.chunks[0])
    altered["entries"][0]["ticker"] = "DRIFT"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="security hash mismatch"):
        _rpc(transfer_db, "record_market_discovery_reference_chunk", run_id, altered)


def test_server_receipts_enforce_exact_retry_and_384_call_transfer_budget(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_transfer_budget"
    pin_payload = {
        "capability_id": capability,
        "binding_role": "predecessor",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    }
    request_id = str(uuid.uuid4())
    first = _rpc(
        transfer_db, "pin_market_discovery_reference", run_id, pin_payload,
        request_id=request_id,
    )
    replay = _rpc(
        transfer_db, "pin_market_discovery_reference", run_id, pin_payload,
        request_id=request_id,
    )
    assert first["duplicate"] is False and replay["duplicate"] is True
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_transfer_requests "
        "WHERE run_id=%s", (run_id,),
    ).fetchone()[0] == 1

    read_payload = {
        "capability_id": capability,
        "binding_role": "predecessor",
        "after_security_id": None,
        "limit": 500,
    }
    for _ in range(383):
        page = _rpc(
            transfer_db, "read_market_discovery_reference", run_id, read_payload,
        )
        assert page["complete"] is True
    with pytest.raises(psycopg.errors.ProgramLimitExceeded, match="aggregate bound"):
        _rpc(transfer_db, "read_market_discovery_reference", run_id, read_payload)
    count, total = transfer_db.execute(
        "SELECT count(*),sum(encoded_bytes) FROM public.market_reference_transfer_requests "
        "WHERE run_id=%s", (run_id,),
    ).fetchone()
    assert count == 384
    assert total <= 48 * 1024 * 1024


def test_reference_transfer_allows_a_bounded_resume_after_the_prior_attempt_window(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_resume"
    _pin_predecessor(
        transfer_db, run_id, capability, "2026-09-09T22:44:22.238Z",
    )
    transfer_db.execute(
        "ALTER TABLE public.market_reference_transfer_requests "
        "DISABLE TRIGGER market_reference_transfer_requests_append_only"
    )
    try:
        transfer_db.execute(
            "UPDATE public.market_reference_transfer_requests "
            "SET created_at=clock_timestamp()-interval '10 minutes' "
            "WHERE run_id=%s", (run_id,),
        )
    finally:
        transfer_db.execute(
            "ALTER TABLE public.market_reference_transfer_requests "
            "ENABLE TRIGGER market_reference_transfer_requests_append_only"
        )

    page = _rpc(transfer_db, "read_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "predecessor",
        "after_security_id": None,
        "limit": 500,
    })

    assert page["complete"] is True
    assert page["binding"]["reference_status"] == "reference_unavailable"


def test_failed_reference_task_can_only_recover_from_its_exact_durable_current_pin(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_universe"
    as_of = "2026-09-09T22:44:22.238Z"
    _pin_predecessor(transfer_db, run_id, capability, as_of)
    binding = _rpc(transfer_db, "pin_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": as_of,
    })
    task_id = str(uuid.uuid4())
    payload = {
        "task": {
            "id": task_id, "stage": "reference", "provider": "sec_edgar",
            "capability_id": capability, "query_kind": "universe",
            "query_hash": "a" * 64, "dependency_ids": [],
            "requested_window": {
                "start": "2026-09-09T20:00:00Z", "end": "2026-09-09T23:00:00Z",
            },
            "state": "planned", "attempt_count": 0, "request_budget": 1,
            "result": {},
        },
        "exposure_facts": [], "theme_episode_revisions": [],
        "research_nominations": [],
    }
    transfer_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    )
    for state, result in (
        ("attempting", {}),
        ("failed", {"error_code": "REFERENCE_STAGE_FAILED"}),
    ):
        payload["task"].update(
            state=state, attempt_count=1, result=result,
        )
        transfer_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )

    coverage = {
        "coverage_status": "scope_not_guaranteed",
        "reference_status": binding["reference_status"],
        "reference_manifest_id": binding["manifest_id"],
        "reference_age_seconds": binding["reference_age_seconds"],
    }
    payload["task"].update(
        state="succeeded", result={"reference_coverage": {
            **coverage, "reference_status": "healthy",
        }},
    )
    with pytest.raises(
        psycopg.errors.InvalidParameterValue,
        match="invalid discovery task state transition",
    ):
        transfer_db.execute(
            "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
            (run_id, Jsonb(payload)),
        )

    payload["task"]["result"] = {"reference_coverage": coverage}
    receipt = transfer_db.execute(
        "SELECT public.checkpoint_market_discovery_stage(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]

    assert receipt["task"]["state"] == "succeeded"
    assert receipt["task"]["result"]["reference_coverage"] == coverage


def test_actual_gateway_repository_and_postgres_transfer_all_15000_members(transfer_db):
    run_id = _run(transfer_db)
    capability = "sec_company_tickers_e2e_capacity"
    transfer = _transfer(run_id, 15_000, "2026-09-07T12:00:00Z", capability)
    secret = "local-reference-e2e-secret"
    operations = [
        ("pin_discovery_reference", {
            "capability_id": capability,
            "binding_role": "predecessor",
            "manifest_id": None,
            "reference_status": "reference_stale",
            "reference_as_of": "2026-09-07T12:00:00.000Z",
        }),
        ("begin_discovery_reference", transfer.begin),
        *(("record_discovery_reference_chunk", chunk) for chunk in transfer.chunks),
        ("finalize_discovery_reference", {
            "manifest_id": transfer.begin["manifest"]["id"],
            "root_hash": transfer.begin["root_hash"],
        }),
        ("pin_discovery_reference", {
            "capability_id": capability,
            "binding_role": "current",
            "manifest_id": transfer.begin["manifest"]["id"],
            "reference_status": "healthy",
            "reference_as_of": "2026-09-07T12:00:01.000Z",
        }),
    ]
    envelopes = []
    for index, (operation, payload) in enumerate(operations, 1):
        envelopes.append(json.dumps({
            "dry_run": False,
            "operation": operation,
            "payload": payload,
            "request_id": f"dddddddd-dddd-4ddd-8ddd-{index:012d}",
            "run_id": run_id,
            "schema_version": 1,
        }, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True))

    allowed = {
        "begin_market_discovery_reference",
        "record_market_discovery_reference_chunk",
        "finalize_market_discovery_reference",
        "pin_market_discovery_reference",
        "read_market_discovery_reference",
    }

    class RpcHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            try:
                length = int(self.headers["content-length"])
                request = json.loads(self.rfile.read(length))
                name = request["name"]
                parameters = request["parameters"]
                if name not in allowed or set(parameters) != {
                    "p_run_id", "p_payload", "p_request_id",
                    "p_encoded_bytes", "p_request_hash",
                }:
                    raise ValueError("unexpected RPC")
                row = self.server.connection.execute(
                    f"SELECT public.{name}(%s,%s,%s,%s,%s)",
                    (
                        parameters["p_run_id"], Jsonb(parameters["p_payload"]),
                        parameters["p_request_id"], parameters["p_encoded_bytes"],
                        parameters["p_request_hash"],
                    ),
                ).fetchone()[0]
                body = json.dumps({"data": row, "error": None}, separators=(",", ":")).encode()
                self.send_response(200)
            except Exception as exc:  # pragma: no cover - failure surfaced by Deno
                body = json.dumps({"data": None, "error": str(exc)}, separators=(",", ":")).encode()
                self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    rpc_connection = psycopg.connect(transfer_db.info.dsn, autocommit=True)
    rpc_connection.execute("SET ROLE service_role")
    server = HTTPServer(("127.0.0.1", 0), RpcHandler)
    server.connection = rpc_connection
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="reference-gateway-e2e-") as directory:
            fixture = Path(directory) / "transfer.json"
            fixture.write_text(json.dumps({
                "run_id": run_id,
                "secret": secret,
                "capability_id": capability,
                "envelopes": envelopes,
            }, separators=(",", ":")))
            completed = subprocess.run(
                [
                    "npx", "--no-install", "deno", "run", "--allow-read",
                    "--allow-net=127.0.0.1",
                    str(ROOT / "tests/fixtures/reference_transfer_gateway_e2e.ts"),
                    f"http://127.0.0.1:{server.server_port}", str(fixture),
                ],
                cwd=ROOT, check=True, capture_output=True, text=True, timeout=90,
            )
        summary = json.loads(completed.stdout)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        rpc_connection.close()

    entries = [entry for chunk in transfer.chunks for entry in chunk["entries"]]
    expected_hash = hashlib.sha256("\n".join(
        "\x1f".join((entry["security_id"], entry["id"], entry["content_hash"]))
        for entry in entries
    ).encode()).hexdigest()
    manifest_id = transfer.begin["manifest"]["id"]
    assert summary["members"] == 15_000
    assert summary["membership_hash"] == expected_hash
    assert summary["calls"] <= 160
    assert summary["total_bytes"] <= 32 * 1024 * 1024
    assert summary["max_request_bytes"] <= 262_144
    assert summary["max_page_bytes"] <= 192 * 1024
    assert transfer_db.execute(
        "SELECT security_count,root_hash FROM public.market_reference_finalization_seals "
        "WHERE manifest_id=%s", (manifest_id,),
    ).fetchone() == (15_000, transfer.begin["root_hash"])
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_snapshot_memberships "
        "WHERE manifest_id=%s", (manifest_id,),
    ).fetchone()[0] == 15_000
    receipt_count, receipt_bytes = transfer_db.execute(
        "SELECT count(*),sum(encoded_bytes) FROM public.market_reference_transfer_requests "
        "WHERE run_id=%s", (run_id,),
    ).fetchone()
    assert receipt_count == summary["calls"]
    assert receipt_bytes == summary["total_bytes"]


def test_v2_real_15000_predecessor_upload_and_restart_hydration_stay_bounded(transfer_db):
    capability = "sec_company_tickers_v2_full_capacity"
    first_run = _run(transfer_db)
    first = _transfer(
        first_run,
        15_000,
        "2026-09-06T12:00:00Z",
        capability,
        semantic_encoding_version=2,
    )
    _upload(transfer_db, first_run, first)

    run_id = _run(transfer_db)
    predecessor = _pin_predecessor(
        transfer_db, run_id, capability, "2026-09-07T12:00:00Z",
    )
    assert predecessor["manifest_id"] == first.begin["manifest"]["id"]

    def read_all(binding_role):
        after = None
        rows = []
        while True:
            page = _rpc(transfer_db, "read_market_discovery_reference", run_id, {
                "capability_id": capability,
                "binding_role": binding_role,
                "after_security_id": after,
                "limit": 500,
            })
            rows.extend(page["securities"])
            if page["complete"]:
                return rows
            after = page["next_after_security_id"]

    assert len(read_all("predecessor")) == 15_000
    current = _transfer(
        run_id,
        15_000,
        "2026-09-07T11:00:00Z",
        capability,
        semantic_encoding_version=2,
    )
    _upload(transfer_db, run_id, current, pin_predecessor=False)
    _rpc(transfer_db, "pin_market_discovery_reference", run_id, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": current.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-07T12:00:01Z",
    })
    assert len(read_all("current")) == 15_000

    request_count, request_bytes = transfer_db.execute(
        "SELECT count(*),sum(encoded_bytes) FROM public.market_reference_transfer_requests "
        "WHERE run_id=%s", (run_id,),
    ).fetchone()
    response_bytes = transfer_db.execute(
        "SELECT sum(encoded_bytes) FROM public.market_reference_transfer_responses "
        "WHERE run_id=%s", (run_id,),
    ).fetchone()[0]
    assert request_count <= 384
    assert request_bytes <= 48 * 1024 * 1024
    assert response_bytes <= 64 * 1024 * 1024


def test_concurrent_exact_begin_and_chunk_retries_return_existing_receipts(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_receipt_race",
    )
    _pin_predecessor(transfer_db, run_id, transfer.begin["capability_id"],
                     transfer.begin["manifest"]["valid_from"])

    def concurrent_retry(operation, payload):
        request_id = str(uuid.uuid4())
        first = psycopg.connect(transfer_db.info.dsn)
        first_result = _rpc(first, operation, run_id, payload, request_id=request_id)

        def retry():
            with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
                return _rpc(connection, operation, run_id, payload, request_id=request_id)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(retry)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    waiting = transfer_db.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE query LIKE %s AND wait_event_type='Lock'",
                        (f"SELECT public.{operation}%",),
                    ).fetchone()[0]
                    if waiting == 1:
                        break
                    time.sleep(0.01)
                assert waiting == 1
                first.commit()
                replay = future.result(timeout=5)
        finally:
            if first.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                first.rollback()
            first.close()
        assert first_result["duplicate"] is False
        assert replay["duplicate"] is True

    concurrent_retry("begin_market_discovery_reference", transfer.begin)
    concurrent_retry(
        "record_market_discovery_reference_chunk", transfer.chunks[0],
    )


def test_finalized_snapshot_reuses_unchanged_cross_run_revisions(transfer_db):
    capability = "sec_company_tickers_reuse"
    first_run = _run(transfer_db)
    first_transfer = _transfer(first_run, 401, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, first_run, first_transfer)
    first_manifest = first_transfer.begin["manifest"]["id"]

    second_run = _run(transfer_db)
    second_transfer = _transfer(second_run, 401, "2026-09-06T12:00:00Z", capability)
    begin, finalized = _upload(transfer_db, second_run, second_transfer)
    assert begin["predecessor_manifest_id"] == first_manifest
    assert finalized["security_count"] == 401
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_security_reference_revisions "
        "WHERE security_id LIKE 'sec-cik:%%' AND run_id IN (%s,%s)",
        (first_run, second_run),
    ).fetchone()[0] == 401
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_snapshot_memberships "
        "WHERE manifest_id IN (%s,%s)",
        (first_manifest, second_transfer.begin["manifest"]["id"]),
    ).fetchone()[0] == 802

    second_manifest = second_transfer.begin["manifest"]["id"]
    _rpc(transfer_db, "pin_market_discovery_reference", second_run, {
            "capability_id": capability,
            "binding_role": "current",
            "manifest_id": second_manifest,
            "reference_status": "healthy",
            "reference_as_of": "2026-09-07T12:00:01Z",
        })
    page = _rpc(transfer_db, "read_market_discovery_reference", second_run, {
            "capability_id": capability,
            "binding_role": "current",
            "after_security_id": None,
            "limit": 10,
        })
    assert {row["manifest_id"] for row in page["securities"]} == {second_manifest}


def test_exact_chunk_retry_remains_idempotent_after_finalization(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_late_retry",
    )
    _upload(transfer_db, run_id, transfer)

    replay = _rpc(
        transfer_db, "record_market_discovery_reference_chunk", run_id,
        transfer.chunks[0],
    )

    assert replay["duplicate"] is True


def test_concurrent_exact_finalization_retry_returns_existing_seal(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_finalize_race",
    )
    _pin_predecessor(transfer_db, run_id, transfer.begin["capability_id"],
                     transfer.begin["manifest"]["valid_from"])
    _rpc(transfer_db, "begin_market_discovery_reference", run_id, transfer.begin)
    _rpc(transfer_db, "record_market_discovery_reference_chunk", run_id,
         transfer.chunks[0])
    payload = {
        "manifest_id": transfer.begin["manifest"]["id"],
        "root_hash": transfer.begin["root_hash"],
    }
    request_id = str(uuid.uuid4())

    lock = psycopg.connect(transfer_db.info.dsn)
    lock.execute("BEGIN")
    lock.execute(
        "SELECT 1 FROM public.market_reference_chunk_receipts "
        "WHERE manifest_id=%s AND chunk_index=-1 FOR UPDATE",
        (transfer.begin["manifest"]["id"],),
    )

    def finalize():
        with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
            return _rpc(
                connection, "finalize_market_discovery_reference", run_id,
                payload, request_id=request_id,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(finalize) for _ in range(2)]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = transfer_db.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE query LIKE 'SELECT public.finalize_market_discovery_reference%' "
                    "AND wait_event_type='Lock'"
                ).fetchone()[0]
                if waiting == 2:
                    break
                time.sleep(0.01)
            assert waiting == 2
            lock.commit()
            results = [future.result(timeout=5) for future in futures]
    finally:
        if lock.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            lock.rollback()
        lock.close()

    assert sorted(result["duplicate"] for result in results) == [False, True]


def test_stale_fallback_pins_finalized_predecessor_and_pages_only_that_snapshot(transfer_db):
    capability = "sec_company_tickers_paging"
    source_run = _run(transfer_db)
    transfer = _transfer(source_run, 1005, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, source_run, transfer)
    stale_run = _run(transfer_db)
    binding = _rpc(transfer_db, "pin_market_discovery_reference", stale_run, {
            "capability_id": capability,
            "binding_role": "predecessor",
            "manifest_id": None,
            "reference_status": "reference_stale",
            "reference_as_of": "2026-09-07T12:00:07Z",
        })
    assert binding["manifest_id"] == transfer.begin["manifest"]["id"]
    assert binding["reference_age_seconds"] == 86_407

    after = None
    securities = []
    pages = 0
    while True:
        page = _rpc(transfer_db, "read_market_discovery_reference", stale_run, {
                "capability_id": capability,
                "binding_role": "predecessor",
                "after_security_id": after,
                "limit": 500,
            })
        assert len(json.dumps(page, separators=(",", ":")).encode()) <= 196_608
        assert page["binding"]["reference_status"] == "reference_stale"
        securities.extend(page["securities"])
        pages += 1
        if page["complete"]:
            break
        after = page["next_after_security_id"]
    assert pages >= 3
    assert len(securities) == 1005
    assert len({row["security_id"] for row in securities}) == 1005


def test_stale_pin_replay_keeps_original_snapshot_after_a_newer_finalize(transfer_db):
    capability = "sec_company_tickers_stable_pin"
    first_run = _run(transfer_db)
    first = _transfer(first_run, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, first_run, first)
    stale_run = _run(transfer_db)
    payload = {
        "capability_id": capability,
        "binding_role": "predecessor",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    }
    request_id = str(uuid.uuid4())
    pinned = _rpc(transfer_db, "pin_market_discovery_reference", stale_run,
                  payload, request_id=request_id)

    newer_run = _run(transfer_db)
    newer = _transfer(newer_run, 1, "2026-09-07T11:00:00Z", capability)
    _upload(transfer_db, newer_run, newer)
    replay = _rpc(transfer_db, "pin_market_discovery_reference", stale_run,
                  payload, request_id=request_id)

    assert replay["duplicate"] is True
    assert replay["manifest_id"] == pinned["manifest_id"] == first.begin["manifest"]["id"]


def test_current_stale_pin_uses_the_run_pinned_predecessor_not_a_newer_head(transfer_db):
    capability = "sec_company_tickers_stale_predecessor"
    first_run = _run(transfer_db)
    first = _transfer(first_run, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, first_run, first)

    stale_run = _run(transfer_db)
    predecessor = _pin_predecessor(
        transfer_db, stale_run, capability, "2026-09-07T12:00:00Z",
    )
    assert predecessor["manifest_id"] == first.begin["manifest"]["id"]

    newer_run = _run(transfer_db)
    newer = _transfer(newer_run, 1, "2026-09-07T11:00:00Z", capability)
    _upload(transfer_db, newer_run, newer)

    current = _rpc(transfer_db, "pin_market_discovery_reference", stale_run, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    })
    assert current["manifest_id"] == first.begin["manifest"]["id"]
    assert current["manifest_id"] != newer.begin["manifest"]["id"]


def test_current_stale_pin_rejects_an_alternate_finalized_manifest(transfer_db):
    capability = "sec_company_tickers_stale_alternate"
    first_run = _run(transfer_db)
    first = _transfer(first_run, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, first_run, first)

    stale_run = _run(transfer_db)
    _pin_predecessor(
        transfer_db, stale_run, capability, "2026-09-07T12:00:00Z",
    )
    newer_run = _run(transfer_db)
    newer = _transfer(newer_run, 1, "2026-09-07T11:00:00Z", capability)
    _upload(transfer_db, newer_run, newer)

    with pytest.raises(
        psycopg.errors.InvalidParameterValue, match="predecessor pin mismatch",
    ):
        _rpc(transfer_db, "pin_market_discovery_reference", stale_run, {
            "capability_id": capability,
            "binding_role": "current",
            "manifest_id": newer.begin["manifest"]["id"],
            "reference_status": "reference_stale",
            "reference_as_of": "2026-09-07T12:00:00Z",
        })


def test_unavailable_predecessor_cannot_be_upgraded_by_a_later_finalization(transfer_db):
    capability = "sec_company_tickers_pinned_unavailable"
    stale_run = _run(transfer_db)
    predecessor = _pin_predecessor(
        transfer_db, stale_run, capability, "2026-09-07T12:00:00Z",
    )
    assert predecessor["reference_status"] == "reference_unavailable"

    later_run = _run(transfer_db)
    later = _transfer(later_run, 1, "2026-09-07T11:00:00Z", capability)
    _upload(transfer_db, later_run, later)

    current = _rpc(transfer_db, "pin_market_discovery_reference", stale_run, {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    })
    assert current["manifest_id"] is None
    assert current["reference_status"] == "reference_unavailable"


def test_transfer_rpcs_are_service_only_and_binding_is_immutable(transfer_db):
    capability = "sec_company_tickers_auth"
    run_id = _run(transfer_db)
    transfer = _transfer(run_id, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, run_id, transfer)
    payload = {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    }
    transfer_db.execute("SET ROLE service_role")
    try:
        request_id = str(uuid.uuid4())
        first = _rpc(transfer_db, "pin_market_discovery_reference", run_id,
                     payload, request_id=request_id)
        replay = _rpc(transfer_db, "pin_market_discovery_reference", run_id,
                      payload, request_id=request_id)
        assert first["duplicate"] is False and replay["duplicate"] is True
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_db.execute("SELECT * FROM public.market_reference_run_bindings")
    finally:
        transfer_db.execute("RESET ROLE")

    altered = {**payload, "reference_as_of": "2026-09-06T12:00:02Z"}
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="pin idempotency mismatch"):
        _rpc(transfer_db, "pin_market_discovery_reference", run_id, altered)
    transfer_db.execute("SET ROLE authenticated")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_db.execute(
                "SELECT public.read_market_discovery_reference(%s,%s,%s,%s,%s)",
                (run_id, Jsonb({
                    "capability_id": capability,
                    "binding_role": "current",
                    "after_security_id": None,
                    "limit": 10,
                }), str(uuid.uuid4()), 1, "a" * 64),
            )
    finally:
        transfer_db.execute("RESET ROLE")


def test_concurrent_exact_pin_retry_returns_existing_binding(transfer_db):
    capability = "sec_company_tickers_pin_race"
    run_id = _run(transfer_db)
    transfer = _transfer(run_id, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, run_id, transfer)
    payload = {
        "capability_id": capability,
        "binding_role": "current",
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    }

    first = psycopg.connect(transfer_db.info.dsn)
    request_id = str(uuid.uuid4())
    first_result = _rpc(first, "pin_market_discovery_reference", run_id,
                        payload, request_id=request_id)

    def pin():
        with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
            return _rpc(connection, "pin_market_discovery_reference", run_id,
                        payload, request_id=request_id)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(pin)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = transfer_db.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE query LIKE 'SELECT public.pin_market_discovery_reference%' "
                    "AND wait_event_type='Lock'"
                ).fetchone()[0]
                if waiting == 1:
                    break
                time.sleep(0.01)
            assert waiting == 1
            first.commit()
            replay = future.result(timeout=5)
    finally:
        if first.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            first.rollback()
        first.close()

    assert first_result["duplicate"] is False
    assert replay["duplicate"] is True


def test_stale_pin_without_any_healthy_snapshot_records_reference_unavailable(transfer_db):
    run_id = _run(transfer_db)
    _pin_predecessor(
        transfer_db, run_id, "sec_company_tickers_empty",
        "2026-09-07T12:00:00Z",
    )
    payload = {
        "capability_id": "sec_company_tickers_empty",
        "binding_role": "current",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    }
    result = _rpc(transfer_db, "pin_market_discovery_reference", run_id, payload)
    assert result["manifest_id"] is None
    assert result["reference_status"] == "reference_unavailable"
    assert result["reference_age_seconds"] is None

    altered = {**payload, "reference_status": "reference_unavailable"}
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="pin idempotency mismatch"):
        _rpc(transfer_db, "pin_market_discovery_reference", run_id, altered)
