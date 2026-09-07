from datetime import datetime, timezone
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest
from pglast import parse_sql
from pglast.stream import RawStream

from lib.intelligence.universe import build_reference_transfer, parse_sec_company_tickers


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261006_reference_snapshot_transfer.sql"
SCHEMA = ROOT / "sql" / "schema.sql"
PREVIOUS = ROOT / "sql" / "migrations" / "20261005_market_wide_discovery.sql"

TABLES = (
    "market_reference_chunk_receipts",
    "market_reference_snapshot_memberships",
    "market_reference_finalization_seals",
    "market_reference_run_bindings",
)
RPCS = (
    "begin_market_discovery_reference",
    "record_market_discovery_reference_chunk",
    "finalize_market_discovery_reference",
    "pin_market_discovery_reference",
    "read_market_discovery_reference",
)


def _statements() -> list[str]:
    return [RawStream()(statement) for statement in parse_sql(MIGRATION.read_text())]


def test_transfer_migration_adds_four_protected_ledgers_and_five_static_rpcs():
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
    assert SCHEMA.read_text().endswith(MIGRATION.read_text())
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


def _transfer(run_id: str, count: int, retrieved_at: str, capability_id: str):
    source = json.dumps({
        str(index): {
            "cik_str": index + 1,
            "ticker": f"T{index:05d}",
            "title": f"Fixture Company {index}",
        }
        for index in range(count)
    }, separators=(",", ":")).encode()
    snapshot = parse_sec_company_tickers(
        source, retrieved_at=datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")),
    )
    return build_reference_transfer(
        snapshot, run_id=run_id, capability_version=1, taxonomy_version=1,
        capability_id=capability_id,
    )


def _upload(connection, run_id: str, transfer):
    begin = connection.execute(
        "SELECT public.begin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(transfer.begin)),
    ).fetchone()[0]
    for chunk in transfer.chunks:
        connection.execute(
            "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
            (run_id, Jsonb(chunk)),
        )
    finalized = connection.execute(
        "SELECT public.finalize_market_discovery_reference(%s,%s)",
        (run_id, Jsonb({
            "manifest_id": transfer.begin["manifest"]["id"],
            "root_hash": transfer.begin["root_hash"],
        })),
    ).fetchone()[0]
    return begin, finalized


def test_partial_snapshot_is_invisible_and_unknown_outcome_retries_are_exact(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(run_id, 401, "2026-09-06T12:00:00Z", "sec_company_tickers_partial")
    first = transfer_db.execute(
        "SELECT public.begin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(transfer.begin)),
    ).fetchone()[0]
    replay = transfer_db.execute(
        "SELECT public.begin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(transfer.begin)),
    ).fetchone()[0]
    assert first["duplicate"] is False and replay["duplicate"] is True
    transfer_db.execute(
        "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
        (run_id, Jsonb(transfer.chunks[0])),
    )
    chunk_replay = transfer_db.execute(
        "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
        (run_id, Jsonb(transfer.chunks[0])),
    ).fetchone()[0]
    assert chunk_replay["duplicate"] is True

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="chunk sequence mismatch"):
        transfer_db.execute(
            "SELECT public.finalize_market_discovery_reference(%s,%s)",
            (run_id, Jsonb({
                "manifest_id": transfer.begin["manifest"]["id"],
                "root_hash": transfer.begin["root_hash"],
            })),
        )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not finalized"):
        transfer_db.execute(
            "SELECT public.pin_market_discovery_reference(%s,%s)",
            (run_id, Jsonb({
                "capability_id": "sec_company_tickers_partial",
                "manifest_id": transfer.begin["manifest"]["id"],
                "reference_status": "healthy",
                "reference_as_of": "2026-09-06T12:00:01Z",
            })),
        )
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_manifests WHERE id=%s",
        (transfer.begin["manifest"]["id"],),
    ).fetchone()[0] == 0

    altered = copy.deepcopy(transfer.chunks[0])
    altered["entries"][0]["ticker"] = "DRIFT"
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="chunk hash mismatch|idempotency mismatch"):
        transfer_db.execute(
            "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
            (run_id, Jsonb(altered)),
        )


def test_concurrent_exact_begin_and_chunk_retries_return_existing_receipts(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_receipt_race",
    )

    def concurrent_retry(operation, payload):
        first = psycopg.connect(transfer_db.info.dsn)
        first_result = first.execute(
            f"SELECT public.{operation}(%s,%s)",
            (run_id, Jsonb(payload)),
        ).fetchone()[0]

        def retry():
            with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
                return connection.execute(
                    f"SELECT public.{operation}(%s,%s)",
                    (run_id, Jsonb(payload)),
                ).fetchone()[0]

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
    second_transfer = _transfer(second_run, 401, "2026-09-07T12:00:00Z", capability)
    begin, finalized = _upload(transfer_db, second_run, second_transfer)
    assert begin["predecessor_manifest_id"] == first_manifest
    assert finalized["security_count"] == 401
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_security_reference_revisions "
        "WHERE security_id LIKE 'sec-cik:%'"
    ).fetchone()[0] == 401
    assert transfer_db.execute(
        "SELECT count(*) FROM public.market_reference_snapshot_memberships "
        "WHERE manifest_id IN (%s,%s)",
        (first_manifest, second_transfer.begin["manifest"]["id"]),
    ).fetchone()[0] == 802

    second_manifest = second_transfer.begin["manifest"]["id"]
    transfer_db.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (second_run, Jsonb({
            "capability_id": capability,
            "manifest_id": second_manifest,
            "reference_status": "healthy",
            "reference_as_of": "2026-09-07T12:00:01Z",
        })),
    )
    page = transfer_db.execute(
        "SELECT public.read_market_discovery_reference(%s,%s)",
        (second_run, Jsonb({
            "capability_id": capability,
            "after_security_id": None,
            "limit": 10,
        })),
    ).fetchone()[0]
    assert {row["manifest_id"] for row in page["securities"]} == {second_manifest}


def test_exact_chunk_retry_remains_idempotent_after_finalization(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_late_retry",
    )
    _upload(transfer_db, run_id, transfer)

    replay = transfer_db.execute(
        "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
        (run_id, Jsonb(transfer.chunks[0])),
    ).fetchone()[0]

    assert replay["duplicate"] is True


def test_concurrent_exact_finalization_retry_returns_existing_seal(transfer_db):
    run_id = _run(transfer_db)
    transfer = _transfer(
        run_id, 1, "2026-09-06T12:00:00Z", "sec_company_tickers_finalize_race",
    )
    transfer_db.execute(
        "SELECT public.begin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(transfer.begin)),
    )
    transfer_db.execute(
        "SELECT public.record_market_discovery_reference_chunk(%s,%s)",
        (run_id, Jsonb(transfer.chunks[0])),
    )
    payload = {
        "manifest_id": transfer.begin["manifest"]["id"],
        "root_hash": transfer.begin["root_hash"],
    }

    lock = psycopg.connect(transfer_db.info.dsn)
    lock.execute("BEGIN")
    lock.execute(
        "SELECT 1 FROM public.market_reference_chunk_receipts "
        "WHERE manifest_id=%s AND chunk_index=-1 FOR UPDATE",
        (transfer.begin["manifest"]["id"],),
    )

    def finalize():
        with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
            return connection.execute(
                "SELECT public.finalize_market_discovery_reference(%s,%s)",
                (run_id, Jsonb(payload)),
            ).fetchone()[0]

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
    binding = transfer_db.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (stale_run, Jsonb({
            "capability_id": capability,
            "manifest_id": None,
            "reference_status": "reference_stale",
            "reference_as_of": "2026-09-07T12:00:07Z",
        })),
    ).fetchone()[0]
    assert binding["manifest_id"] == transfer.begin["manifest"]["id"]
    assert binding["reference_age_seconds"] == 86_407

    after = None
    securities = []
    pages = 0
    while True:
        page = transfer_db.execute(
            "SELECT public.read_market_discovery_reference(%s,%s)",
            (stale_run, Jsonb({
                "capability_id": capability,
                "after_security_id": after,
                "limit": 500,
            })),
        ).fetchone()[0]
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
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    }
    pinned = transfer_db.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (stale_run, Jsonb(payload)),
    ).fetchone()[0]

    newer_run = _run(transfer_db)
    newer = _transfer(newer_run, 1, "2026-09-07T11:00:00Z", capability)
    _upload(transfer_db, newer_run, newer)
    replay = transfer_db.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (stale_run, Jsonb(payload)),
    ).fetchone()[0]

    assert replay["duplicate"] is True
    assert replay["manifest_id"] == pinned["manifest_id"] == first.begin["manifest"]["id"]


def test_transfer_rpcs_are_service_only_and_binding_is_immutable(transfer_db):
    capability = "sec_company_tickers_auth"
    run_id = _run(transfer_db)
    transfer = _transfer(run_id, 1, "2026-09-06T12:00:00Z", capability)
    _upload(transfer_db, run_id, transfer)
    payload = {
        "capability_id": capability,
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    }
    transfer_db.execute("SET ROLE service_role")
    try:
        first = transfer_db.execute(
            "SELECT public.pin_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(payload)),
        ).fetchone()[0]
        replay = transfer_db.execute(
            "SELECT public.pin_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(payload)),
        ).fetchone()[0]
        assert first["duplicate"] is False and replay["duplicate"] is True
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_db.execute("SELECT * FROM public.market_reference_run_bindings")
    finally:
        transfer_db.execute("RESET ROLE")

    altered = {**payload, "reference_as_of": "2026-09-06T12:00:02Z"}
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="pin idempotency mismatch"):
        transfer_db.execute(
            "SELECT public.pin_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(altered)),
        )
    transfer_db.execute("SET ROLE authenticated")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_db.execute(
                "SELECT public.read_market_discovery_reference(%s,%s)",
                (run_id, Jsonb({
                    "capability_id": capability,
                    "after_security_id": None,
                    "limit": 10,
                })),
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
        "manifest_id": transfer.begin["manifest"]["id"],
        "reference_status": "healthy",
        "reference_as_of": "2026-09-06T12:00:01Z",
    }

    first = psycopg.connect(transfer_db.info.dsn)
    first_result = first.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]

    def pin():
        with psycopg.connect(transfer_db.info.dsn, autocommit=True) as connection:
            return connection.execute(
                "SELECT public.pin_market_discovery_reference(%s,%s)",
                (run_id, Jsonb(payload)),
            ).fetchone()[0]

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
    payload = {
        "capability_id": "sec_company_tickers_empty",
        "manifest_id": None,
        "reference_status": "reference_stale",
        "reference_as_of": "2026-09-07T12:00:00Z",
    }
    result = transfer_db.execute(
        "SELECT public.pin_market_discovery_reference(%s,%s)",
        (run_id, Jsonb(payload)),
    ).fetchone()[0]
    assert result["manifest_id"] is None
    assert result["reference_status"] == "reference_unavailable"
    assert result["reference_age_seconds"] is None

    altered = {**payload, "reference_status": "reference_unavailable"}
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="pin idempotency mismatch"):
        transfer_db.execute(
            "SELECT public.pin_market_discovery_reference(%s,%s)",
            (run_id, Jsonb(altered)),
        )
