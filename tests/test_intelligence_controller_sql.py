"""Execute both install paths in disposable, socket-only local PostgreSQL.

Never reads credentials or connects to an existing server.
"""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def databases():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="intelligence-sql-") as directory:
        root = Path(directory)
        subprocess.run([binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"], check=True, capture_output=True)
        subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
                        "-o", f"-k {root} -h '' -p 55439", "-w", "start"], check=True, capture_output=True)
        try:
            dsn = f"host={root} port=55439 dbname=postgres"
            with psycopg.connect(dsn, autocommit=True) as admin:
                admin.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
                for kind in ("fresh", "ordered"):
                    admin.execute(f"CREATE DATABASE {kind}")
            schema = (ROOT / "sql/schema.sql").read_text()
            connections = {}
            for kind in ("fresh", "ordered"):
                connection = psycopg.connect(f"host={root} port=55439 dbname={kind}", autocommit=True)
                connections[kind] = connection
                connection.execute("CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'")
                connection.execute("CREATE SCHEMA supabase_migrations; CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY, statements text[])")
                if kind == "fresh":
                    connection.execute(schema)
                else:
                    connection.execute(schema.split("CREATE TABLE IF NOT EXISTS public.market_intelligence_runs (")[0])
                    for migration in sorted((ROOT / "sql/migrations").glob("*.sql")):
                        if migration.name >= "20260907":
                            connection.execute(migration.read_text())
                intelligence = json.loads((ROOT / "config/settings.json").read_text())["intelligence"]
                connection.execute("INSERT INTO public.market_policy_config(version,config) VALUES(1,%s)", (Jsonb({"intelligence": intelligence}),))
            yield connections
            for connection in connections.values():
                connection.close()
        finally:
            subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"], check=True, capture_output=True)


def prepared_run(connection, *, provider="gdelt", cache=False, checkpoint=True, run_id=None, phase="intraday", market_date=None, returned_count=0, accepted_count=0):
    generated_run, reservation, original, hit, completion = [str(uuid.uuid4()) for _ in range(5)]
    run = str(run_id or generated_run)
    now = connection.execute("SELECT statement_timestamp()").fetchone()[0]
    from datetime import timedelta
    window = {"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat(),
              "timezone": "America/Chicago", "market_date": market_date or now.astimezone(__import__('zoneinfo').ZoneInfo("America/Chicago")).date().isoformat(), "phase": phase}
    plan = {"reservations": [{"id": reservation, "provider": provider, "requests": 2, "cache_keys": []}]}
    if run_id is None:
        connection.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,%s)", (run, phase))
    # Fixture setup explicitly uses the immutable policy already installed by the schema.
    connection.execute("SELECT public.start_market_intelligence_run(%s,%s,%s,1,%s,%s)", (run, phase, window["market_date"], Jsonb(plan), Jsonb(window)))
    receipt = {"id": hit if cache else original, "reservation_id": reservation, "status": "cache_hit" if cache else "succeeded",
        "cache_key": "a" * 64, "requested_window": {"start": window["start"], "end": window["end"]},
        "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(minutes=15)).isoformat(),
        "request_cost": 0 if cache else 1, "upstream_remaining": None,
        "returned_count": returned_count, "accepted_count": accepted_count,
        "duplicate_count": 0, "dropped_count": 0, "error": None, "response_hash": "b" * 64,
        "cache_predecessor_receipt_id": original if cache else None}
    checkpoint_row = dict(receipt, provider=provider, status="succeeded", request_cost=1, source_receipt_id=original,
                      requested_limit=20, observed_at=now.isoformat(), error_code=None, cache_predecessor_receipt_id=None)
    checkpoint_row.pop("id"); checkpoint_row.pop("error")
    if checkpoint:
        connection.execute("SELECT public.checkpoint_market_intelligence_collection(%s,%s)", (run, Jsonb({"cache_key": "a"*64, "receipt": checkpoint_row, "items": []})))
    packet = {"candidates": [], "evidence": [], "coverage": {}, "limitations": [], "policy_version": 1}
    canonical = json.dumps(packet, separators=(",", ":"), sort_keys=True)
    payload = {"status": "completed", "coverage": {}, "receipts": [receipt], "items": [], "events": [], "relationships": [], "rankings": [],
        "packet": {"id": str(uuid.uuid4()), "candidate_count": 0, "evidence_count": 0, "packet": packet,
                   "packet_hash": hashlib.sha256(canonical.encode()).hexdigest()}, "error": None}
    return run, completion, original, payload


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_report_suppression_requires_reason_and_preserves_original_delivery(databases, kind):
    db = databases[kind]
    run, completion, _, payload = prepared_run(db)
    db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run, completion, Jsonb(payload)))
    report_id, delivered_id = uuid.uuid4(), uuid.uuid4()
    keys = [hashlib.sha256(str(identity).encode()).hexdigest() for identity in (report_id, delivered_id)]
    for identity, key in zip((report_id, delivered_id), keys):
        db.execute("INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) VALUES(%s,%s,%s,%s,CURRENT_DATE,%s,'{}',repeat('b',64),'text',repeat('c',64))",
                   (identity, key, run, payload["packet"]["id"], "intraday" if identity == report_id else "urgent"))
        db.execute("INSERT INTO market_report_publications(report_id,idempotency_key,status) VALUES(%s,%s,'pending')", (identity, key))
    for reason in (None, "", " ", "caller prose"):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            db.execute("SELECT public.suppress_market_report_publication(%s,%s)", (keys[0], reason))
    result = db.execute("SELECT public.suppress_market_report_publication(%s,'no_trigger')", (keys[0],)).fetchone()[0]
    assert result["suppression_reason"] == "no_trigger"
    assert db.execute("SELECT status,suppression_reason,error FROM market_report_publications WHERE report_id=%s", (report_id,)).fetchone() == ("suppressed", "no_trigger", None)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE market_report_publications SET suppression_reason='' WHERE report_id=%s", (report_id,))
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.execute("SELECT public.suppress_market_report_publication(%s,'not_actionable')", (keys[0],))
    db.execute("UPDATE market_report_publications SET status='delivered',telegram_message_ids='[77]',telegram_accepted_at=now() WHERE report_id=%s", (delivered_id,))
    original = db.execute("SELECT telegram_message_ids,telegram_accepted_at FROM market_report_publications WHERE report_id=%s", (delivered_id,)).fetchone()
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.execute("SELECT public.suppress_market_report_publication(%s,'no_trigger')", (keys[1],))
    assert db.execute("SELECT telegram_message_ids,telegram_accepted_at FROM market_report_publications WHERE report_id=%s", (delivered_id,)).fetchone() == original
    assert db.execute("SELECT to_regprocedure('public.suppress_market_report_publication(text)')").fetchone()[0] is None
    assert not db.execute("SELECT has_function_privilege('authenticated','public.suppress_market_report_publication(text,text)','EXECUTE')").fetchone()[0]
    from scripts.protected_evidence import PostgresReadOnlySource
    source = object.__new__(PostgresReadOnlySource)
    source.connection = db
    rows = source.release_rows(run)
    assert next(row for row in rows["publications"] if row["report_id"] == str(report_id))["suppression_reason"] == "no_trigger"
    assert next(row for row in rows["publications"] if row["report_id"] == str(delivered_id))["telegram_message_ids"] == [77]
    from scripts.verify_owner_dashboard_deployment import REPORT_PUBLICATION_SQL
    dashboard_rows = source.query(REPORT_PUBLICATION_SQL, (run,))
    assert next(row for row in dashboard_rows if row["report_id"] == str(report_id))["suppression_reason"] == "no_trigger"
    assert isinstance(next(row for row in dashboard_rows if row["report_id"] == str(delivered_id))["telegram_accepted_at"], str)
    db.execute("SET ROLE stock_agent_release_reader")
    try:
        assert source.query("SELECT count(*) AS count FROM public.market_report_publications")[0]["count"] >= 2
        from scripts.protected_evidence import RECOVERY_SQL
        role_records = source.query(RECOVERY_SQL["roles"])
        assert next(row for row in role_records if row["role"] == "stock_agent_dashboard")["grants"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("DELETE FROM public.market_report_publications WHERE report_id=%s", (report_id,))
    finally:
        db.execute("RESET ROLE")


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
@pytest.mark.parametrize("cache", [False, True])
def test_exact_key_recorder_preserves_actual_cost_and_distinct_lineage(databases, kind, cache):
    db = databases[kind]
    run, completion, original, payload = prepared_run(db, cache=cache)
    result = db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run, completion, Jsonb(payload))).fetchone()[0]
    assert result["packet_hash"] == payload["packet"]["packet_hash"]
    assert db.execute("SELECT sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run,)).fetchone()[0] == 1
    if cache:
        assert db.execute("SELECT cache_receipt_id::text,cache_predecessor_receipt_id::text FROM market_checkpoint_receipt_lineage WHERE run_id=%s", (run,)).fetchone() == (payload["receipts"][0]["id"], original)
    recovered = db.execute("SELECT public.read_market_intelligence_completion(%s,%s)", (run, completion)).fetchone()[0]
    assert recovered["payload"] == payload
    assert recovered["receipt"]["completion_id"] == completion


def _official_completion_item(payload, *, request_url, canonical_url):
    canonical_content = json.dumps({"title": "Defense industrial award"}, separators=(",", ":"))
    return {
        "id": str(uuid.uuid4()), "run_item_id": str(uuid.uuid4()),
        "receipt_id": payload["receipts"][0]["id"], "provider": "dod",
        "upstream_item_id": "dod-release-1", "canonical_url": canonical_url,
        "request_url": request_url, "published_at": None,
        "retrieved_at": payload["receipts"][0]["retrieved_at"],
        "effective_at": None, "reporting_at": None,
        "entity_ids": [], "security_ids": [], "discovery_status": "no_event",
        "title": "Defense industrial award", "normalized_text": "Official release.",
        "canonical_content": canonical_content,
        "content_hash": hashlib.sha256(canonical_content.encode()).hexdigest(),
        "metadata": {}, "disposition": "accepted", "drop_reason": None,
    }


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
@pytest.mark.parametrize(("host", "content_type", "item_path"), [
    ("www.war.gov", 9, "/News/Releases/Release/Article/1/award/"),
    ("www.defense.gov", 1, "/News/News-Stories/Article/1/update/"),
])
def test_protected_completion_accepts_only_reviewed_defense_feed_and_item_paths(
    databases, kind, host, content_type, item_path,
):
    db = databases[kind]
    run, completion, _original, payload = prepared_run(
        db, provider="dod", phase="pre-market", returned_count=1, accepted_count=1,
    )
    request_url = (
        f"https://{host}/DesktopModules/ArticleCS/RSS.ashx"
        f"?ContentType={content_type}&Site=945&max=10"
    )
    payload["items"] = [_official_completion_item(
        payload, request_url=request_url, canonical_url=f"https://{host}{item_path}",
    )]
    result = db.execute(
        "SELECT public.record_market_intelligence(%s,%s,%s)",
        (run, completion, Jsonb(payload)),
    ).fetchone()[0]
    assert result["counts"]["source_items"] == 1


@pytest.mark.parametrize("bad_url", [
    "https://www.war.gov/search/?ContentType=9&Site=945&max=10",
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=100",
])
def test_protected_completion_rejects_unreviewed_defense_request_paths(databases, bad_url):
    db = databases["fresh"]
    run, completion, _original, payload = prepared_run(
        db, provider="dod", phase="pre-market", returned_count=1, accepted_count=1,
    )
    payload["items"] = [_official_completion_item(
        payload, request_url=bad_url,
        canonical_url="https://www.war.gov/News/Releases/Release/Article/1/award/",
    )]
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        db.execute(
            "SELECT public.record_market_intelligence(%s,%s,%s)",
            (run, completion, Jsonb(payload)),
        )


def test_ordered_and_fresh_final_function_contracts_are_identical(databases):
    names = ["record_market_intelligence", "record_market_intelligence_provider_v2", "record_market_intelligence_legacy",
             "checkpoint_market_intelligence_collection", "read_market_intelligence_completion", "refresh_market_intelligence_context"]
    def definitions(db):
        rows = db.execute("SELECT proname,pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY(%s) ORDER BY proname", (names,)).fetchall()
        assert {name for name, _ in rows} == set(names)
        return rows
    assert definitions(databases["fresh"]) == definitions(databases["ordered"])
    for db in databases.values():
        for signature in ("read_market_evidence_packet(uuid,uuid)", "read_market_report_decisions(uuid,uuid,jsonb)",
                          "record_market_report(uuid,text,jsonb)", "record_market_learning(uuid,jsonb)"):
            assert db.execute("SELECT has_function_privilege('service_role',%s,'EXECUTE')", (signature,)).fetchone()[0]
            assert not db.execute("SELECT has_function_privilege('authenticated',%s,'EXECUTE')", (signature,)).fetchone()[0]
        assert db.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='public.market_report_publications'::regclass AND confrelid='public.market_reports'::regclass AND contype='f'").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_protected_context_producer_uses_persisted_quote_receipts(databases, kind):
    db = databases[kind]
    run, _, original, payload = prepared_run(db, provider="yahoo", checkpoint=False)
    db.execute("INSERT INTO holdings(ticker,shares,avg_cost) VALUES('TEST',2,20) ON CONFLICT(ticker) DO NOTHING")
    r = payload["receipts"][0]
    now = r["retrieved_at"]
    input_value = {"ticker": "TEST", "reservation_id": r["reservation_id"], "source_receipt_id": original, "cache_key": r["cache_key"]}
    # Exercise the legacy quote receipt mechanics directly; the public Task 6
    # wrapper additionally requires a frozen selected descriptor.
    assert db.execute("SELECT public.claim_market_intelligence_quote_v1_internal(%s,%s)", (run, Jsonb(input_value))).fetchone()[0]["status"] == "claimed"
    quote = {"ticker": "TEST", "currency": "USD", "price": "100", "as_of": now,
             "instrument_type": "EQUITY", "average_daily_dollar_volume": "5000000", "response_hash": r["response_hash"]}
    cr = dict(r, provider="yahoo", requested_limit=20, observed_at=now, error_code=None, source_receipt_id=original)
    cr.pop("id"); cr.pop("error")
    item = {"provider": "yahoo", "security_ids": ["TEST"], "request_url": "https://query1.finance.yahoo.com/v8/finance/chart/TEST?range=5d&interval=1d",
        "retrieved_at": now, "metadata": {"ranking_quote": {"ticker": "TEST", "currency": "USD", "price": "100",
        "as_of": now, "instrument_type": "EQUITY", "average_daily_dollar_volume": "5000000"}}}
    # Setup models the trusted adapter's persisted source shape; read operation accepts no input values.
    checkpoint = {"cache_key": r["cache_key"], "receipt": cr, "items": [item]}
    db.execute("SELECT public.record_market_intelligence_quote(%s,%s,%s,%s)", (run, original, Jsonb(quote), Jsonb(checkpoint)))
    context = db.execute("SELECT public.refresh_market_intelligence_context(%s)", (run,)).fetchone()[0]
    assert context["holding_market_values"] == {"TEST": "200"}
    assert context["liquidity_by_ticker"] == {"TEST": "0.500000"}
    assert context["overlap_by_ticker"] == {"TEST": "1.000000"}
    assert context["valuation_status"] == "unavailable"
    assert context["valuation_state_by_ticker"] == {}
    assert context["valuation_provenance_by_ticker"] == {}
    assert context["liquidity_state_by_ticker"] == {"TEST": "passed"}
    assert context["liquidity_provenance_by_ticker"]["TEST"]["source"] == "market_intelligence_quote_attempts"
    assert context["overlap_state_by_ticker"] == {"TEST": "passed"}
    assert context["overlap_provenance_by_ticker"]["TEST"]["source"] == "holdings_and_verified_quotes"
    assert context["current_reference_state"] == "unavailable"
    assert context["quote_receipt_ids"] == [checkpoint["receipt"]["source_receipt_id"]]
    db.execute("SET ROLE authenticated")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SELECT public.refresh_market_intelligence_context(%s)", (run,))
    finally:
        db.execute("RESET ROLE")


def test_authored_checkpoint_quote_metadata_cannot_become_ranking_authority(databases):
    db = databases["fresh"]
    run, _, _, _ = prepared_run(db, provider="yahoo")
    checkpoint = db.execute("SELECT payload FROM market_collection_checkpoints WHERE run_id=%s", (run,)).fetchone()[0]
    checkpoint["items"] = [{"provider": "yahoo", "metadata": {"ranking_quote": {"ticker": "TEST", "currency": "USD", "price": "999999", "as_of": checkpoint["receipt"]["retrieved_at"], "instrument_type": "EQUITY", "average_daily_dollar_volume": "999999999"}}}]
    db.execute("UPDATE market_collection_checkpoints SET payload=%s WHERE run_id=%s", (Jsonb(checkpoint), run))
    context = db.execute("SELECT public.refresh_market_intelligence_context(%s)", (run,)).fetchone()[0]
    assert context["holding_market_values"] == context["liquidity_by_ticker"] == context["overlap_by_ticker"] == {}


def test_pending_server_quote_cannot_be_erased_by_restart_or_terminal_payload(databases):
    db = databases["fresh"]
    run, completion, original, payload = prepared_run(db, provider="yahoo", checkpoint=False)
    r = payload["receipts"][0]
    input_value = {"ticker": "TEST", "reservation_id": r["reservation_id"], "source_receipt_id": original, "cache_key": r["cache_key"]}
    assert db.execute("SELECT public.claim_market_intelligence_quote_v1_internal(%s,%s)", (run, Jsonb(input_value))).fetchone()[0]["status"] == "claimed"
    assert db.execute("SELECT public.claim_market_intelligence_quote_v1_internal(%s,%s)", (run, Jsonb(input_value))).fetchone()[0]["status"] == "uncertain"
    window = db.execute("SELECT request_window FROM market_intelligence_runs WHERE id=%s", (run,)).fetchone()[0]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="uncertain"):
        db.execute("SELECT public.start_market_intelligence_run(%s,'intraday',%s,1,%s,%s)", (run, window["market_date"], Jsonb({"reservations": []}), Jsonb(window)))
    payload["receipts"] = []
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="uncertain"):
        db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run, completion, Jsonb(payload)))


@pytest.mark.parametrize("actual_cost", [0, 1])
def test_quota_blocked_outcome_persists_real_attempt_count(databases, actual_cost):
    db = databases["fresh"]
    run, completion, _, payload = prepared_run(db, checkpoint=False)
    r = payload["receipts"][0]
    r.update(status="quota_blocked", request_cost=actual_cost, error={"code": "QUOTA_BLOCKED"}, expires_at=None, response_hash=None)
    db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run, completion, Jsonb(payload)))
    assert db.execute("SELECT status,request_cost,error FROM market_source_receipts WHERE run_id=%s", (run,)).fetchone() == ("quota_blocked", actual_cost, {"code": "QUOTA_BLOCKED"})


def _independent_worker(dsn, run_id, timestamp, crash, output, provider="gdelt", transport_count=None, registry_fixture=False):
    """Independent process, real pipeline + RPCs; only the outbound provider is a fixture."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os
    from lib.intelligence.pipeline import IntelligencePipeline, PipelineRequest
    from lib.intelligence.providers import build_adapter
    from lib.intelligence.quota import QuotaSession
    from lib.intelligence.http import HttpResult
    now = datetime.fromisoformat(timestamp)
    with psycopg.connect(dsn, autocommit=True) as db:
        class Gateway:
            def call(self, operation, payload, *, run_id=None, request_id=None):
                if operation == "read_intelligence_completion":
                    return {"completion": db.execute("SELECT public.read_market_intelligence_completion(%s,%s)", (run_id, request_id)).fetchone()[0]}
                if operation == "start_intelligence_run":
                    return db.execute("SELECT public.start_market_intelligence_run(%s,%s,%s,%s,%s,%s)",
                        (request_id, payload["phase"], payload["market_date"], payload["policy_version"], Jsonb(payload["reservation_plan"]), Jsonb(payload["request_window"]))).fetchone()[0]
                if operation == "checkpoint_intelligence_collection":
                    checkpoint = db.execute("SELECT public.checkpoint_market_intelligence_collection(%s,%s)", (run_id, Jsonb(payload))).fetchone()[0]
                    if crash == "attempt_barrier" and payload["receipt"].get("error_code") == "TRANSPORT_OUTCOME_UNCERTAIN":
                        os._exit(75)
                    if crash == "checkpoint" and payload["receipt"].get("status") == "succeeded":
                        os._exit(74)
                    return checkpoint
                if operation == "read_intelligence_context":
                    return {"context": {"holdings": [], "intelligence_collection_context": db.execute("SELECT public.refresh_market_intelligence_context(%s)", (run_id,)).fetchone()[0]}}
                if operation == "record_intelligence":
                    receipt = db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run_id, request_id, Jsonb(payload))).fetchone()[0]
                    if crash is True:
                        os._exit(73)
                    return receipt
                raise AssertionError(operation)
        class Source:
            def get(self, request):
                if transport_count is not None:
                    with transport_count.get_lock():
                        transport_count.value += 1
                if crash == "inside_transport":
                    os._exit(76)
                payload = b'{"articles":[]}' if provider == "gdelt" else b'{"feed":[]}' if provider == "alpha_vantage" else b'[]'
                return HttpResult(request.url, 200, {}, payload, now, now)
        adapter = build_adapter(provider, Source(), QuotaSession({provider: ()}),
                                secret_getter=lambda _name: "existing-free-key", clock=lambda: now)
        if registry_fixture and provider not in {"gdelt", "alpha_vantage", "finnhub"}:
            # Exercise the common transport contract even for currently unsupported
            # official queries. No production endpoint/capability is added.
            from lib.intelligence.http import HttpRequest
            from lib.intelligence.providers import CollectionQuery
            adapter._request = lambda query: HttpRequest("https://fixture.invalid/official")
            IntelligencePipeline._query_for = lambda self, adapter, target, request, window: CollectionQuery(
                text=target, symbols=(), start=datetime.fromisoformat(window["start"].replace("Z", "+00:00")),
                end=datetime.fromisoformat(window["end"].replace("Z", "+00:00")), limit=20)
        phase = "intraday" if provider in {"alpha_vantage", "finnhub"} else "on-demand"
        context_data = {"holdings": {"TEST": "1"}} if phase == "intraday" else None
        result = IntelligencePipeline(Gateway(), [adapter], context=context_data).run(PipelineRequest(
            phase, now.astimezone(ZoneInfo("America/Chicago")).date(), now, request_id=run_id))
        output.put(result.to_dict())


@pytest.mark.parametrize("provider", [
    "gdelt", "alpha_vantage", "finnhub", "sec_edgar", "federal_register",
    "white_house", "doe", "dod", "eia", "fred", "bls", "bea",
])
def test_registry_transport_crash_durably_accounts_one_attempt_without_replacement(databases, provider):
    import multiprocessing
    from datetime import datetime, timedelta
    db = databases["ordered"]
    run_id = str(uuid.uuid4())
    phase = "intraday" if provider in {"alpha_vantage", "finnhub"} else "on-demand"
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,%s)", (run_id, phase))
    timestamp = db.execute("SELECT statement_timestamp()").fetchone()[0].isoformat()
    context = multiprocessing.get_context("spawn")
    output, calls = context.Queue(), context.Value("i", 0)
    first = context.Process(target=_independent_worker,
        args=(db.info.dsn, run_id, timestamp, "inside_transport", output, provider, calls, True))
    first.start(); first.join(20)
    assert first.exitcode == 76
    assert calls.value == 1
    checkpoint = db.execute(
        "SELECT payload FROM market_collection_checkpoints WHERE run_id=%s", (run_id,)).fetchall()
    assert len(checkpoint) == 1
    attempt = checkpoint[0][0]["receipt"]
    assert (attempt["provider"], attempt["request_cost"], attempt["error_code"]) == (provider, 1, "TRANSPORT_OUTCOME_UNCERTAIN")
    second = context.Process(target=_independent_worker,
        args=(db.info.dsn, run_id, (datetime.fromisoformat(timestamp) + timedelta(seconds=10)).isoformat(), False, output, provider, calls, True))
    second.start(); second.join(20)
    assert second.exitcode == 0
    assert output.get(timeout=2)["actual_requests"] == 1
    assert calls.value == 1
    assert db.execute("SELECT payload FROM market_collection_checkpoints WHERE run_id=%s", (run_id,)).fetchall() == checkpoint
    assert db.execute("SELECT count(*) FROM market_collection_checkpoint_history WHERE run_id=%s", (run_id,)).fetchone() == (0,)
    assert db.execute("SELECT count(*),sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run_id,)).fetchone() == (1, 1)
    assert db.execute("SELECT id::text,provider,reserved_requests FROM market_source_quota_reservations WHERE run_id=%s", (run_id,)).fetchone() == (attempt["reservation_id"], provider, 1)


def test_sql_attempt_allowlist_is_exact_reviewed_non_yahoo_registry(databases):
    import re
    from lib.intelligence.policy import _PROVIDERS
    from lib.intelligence.providers import RESERVED_OUTBOUND_PROVIDERS
    assert RESERVED_OUTBOUND_PROVIDERS == set(_PROVIDERS) - {"yahoo"}
    for db in databases.values():
        definition = db.execute("SELECT pg_get_functiondef('public.checkpoint_market_intelligence_collection(uuid,jsonb)'::regprocedure)").fetchone()[0]
        allowed = re.search(r"v_reserved.provider NOT IN \(([^)]+)\)", definition).group(1)
        assert set(re.findall(r"'([^']+)'", allowed)) == set(_PROVIDERS) - {"yahoo"}


@pytest.mark.parametrize("provider", ["alpha_vantage", "finnhub"])
def test_paid_provider_attempt_barrier_survives_process_crash_without_replacement_call(databases, provider):
    import multiprocessing
    db = databases["ordered"]
    run_id = str(uuid.uuid4())
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,'intraday')", (run_id,))
    timestamp = db.execute("SELECT statement_timestamp()").fetchone()[0].isoformat()
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    transport_count = context.Value("i", 0)

    first = context.Process(
        target=_independent_worker,
        args=(db.info.dsn, run_id, timestamp, "attempt_barrier", output, provider, transport_count),
    )
    first.start(); first.join(20)
    assert first.exitcode == 75
    checkpoint = db.execute(
        "SELECT payload->'receipt'->>'status',payload->'receipt'->>'error_code',"
        "(payload->'receipt'->>'request_cost')::int FROM market_collection_checkpoints WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    assert checkpoint == ("failed", "TRANSPORT_OUTCOME_UNCERTAIN", 1)
    assert transport_count.value == 0

    second = context.Process(
        target=_independent_worker,
        args=(db.info.dsn, run_id, timestamp, False, output, provider, transport_count),
    )
    second.start(); second.join(20)
    assert second.exitcode == 0
    result = output.get(timeout=2)
    assert result["actual_requests"] == 1 and result["cache_hits"] == 0
    assert result["receipts"][0]["error_code"] == "TRANSPORT_OUTCOME_UNCERTAIN"
    assert transport_count.value == 0
    assert db.execute(
        "SELECT count(*),sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run_id,)
    ).fetchone() == (1, 1)


def test_paid_provider_attempt_barrier_transitions_to_one_terminal_receipt(databases):
    import multiprocessing
    db = databases["ordered"]
    run_id = str(uuid.uuid4())
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,'intraday')", (run_id,))
    timestamp = db.execute("SELECT statement_timestamp()").fetchone()[0].isoformat()
    context = multiprocessing.get_context("spawn")
    output, transport_count = context.Queue(), context.Value("i", 0)
    worker = context.Process(
        target=_independent_worker,
        args=(db.info.dsn, run_id, timestamp, False, output, "alpha_vantage", transport_count),
    )
    worker.start(); worker.join(20)
    assert worker.exitcode == 0
    result = output.get(timeout=2)
    assert result["actual_requests"] == 1 and transport_count.value == 1
    assert db.execute(
        "SELECT payload->'receipt'->>'status',(payload->'receipt'->>'request_cost')::int "
        "FROM market_collection_checkpoints WHERE run_id=%s", (run_id,),
    ).fetchone() == ("succeeded", 1)
    assert db.execute(
        "SELECT count(*) FROM market_collection_checkpoint_history WHERE run_id=%s", (run_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*),sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run_id,),
    ).fetchone() == (1, 1)


def test_event_and_ranking_hashes_survive_typed_postgres_readback_losslessly(databases):
    from decimal import Decimal
    from types import MappingProxyType
    from lib.intelligence.pipeline import _event_row, _ranking_row
    from lib.intelligence.normalize import SourceItem
    from lib.intelligence.ranking import RankedCandidate
    from lib.intelligence.themes import build_market_event
    from scripts.protected_evidence import PostgresReadOnlySource

    db = databases["ordered"]
    run, _completion, _original, _payload = prepared_run(db, checkpoint=False)
    now = db.execute("SELECT statement_timestamp()").fetchone()[0]
    item = SourceItem(
        provider="sec_edgar", upstream_item_id="filing-1", canonical_url="https://sec.gov/filing-1",
        title="Issuer filing", summary="Material update", canonical_content="{}",
        content_hash="a" * 64, published_at=now, effective_at=None, retrieved_at=now,
        authority="official", metadata=MappingProxyType({"item_id": str(uuid.uuid4())}),
    )
    event = build_market_event(
        event_type="filing", title="Material filing", summary="Issuer reports an update.",
        materiality=Decimal("0.700000"), confidence=Decimal("0.800000"), evidence=(item,),
        theme_ids=("earnings_ma",), occurred_at=now, effective_at=now,
    )
    event_row = _event_row(run, event)
    ranking_row = _ranking_row(run, RankedCandidate(
        ticker="TEST", candidate_key="TEST:filing", event_id=event.event_id,
        relationship_type="affects", evidence=(item,), exposure_evidence=(),
        components=MappingProxyType({"materiality": Decimal("0.700000")}),
        missing_reasons=("exposure:missing",), total_score=Decimal("0.700000"),
        authoritative_evidence_count=1, qualified=False,
        veto_reasons=("INSUFFICIENT_EXPOSURE",), rank=1,
    ))
    db.execute(
        "INSERT INTO market_events(id,run_id,event_type,title,summary,occurred_at,effective_at,materiality,confidence,evidence_item_ids,content_hash) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (event_row["id"], run, event_row["event_type"], event_row["title"], event_row["summary"],
         event_row["occurred_at"], event_row["effective_at"], event_row["materiality"], event_row["confidence"],
         Jsonb(event_row["evidence_item_ids"]), event_row["content_hash"]),
    )
    db.execute(
        "INSERT INTO market_candidate_rankings(id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,veto_reasons,exposure_item_ids,content_hash) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ranking_row["id"], run, ranking_row["event_id"], ranking_row["candidate_key"], ranking_row["ticker"],
         ranking_row["rank"], Jsonb(ranking_row["component_scores"]), ranking_row["total_score"], ranking_row["qualified"],
         Jsonb(ranking_row["veto_reasons"]), Jsonb(ranking_row["exposure_item_ids"]), ranking_row["content_hash"]),
    )
    source = object.__new__(PostgresReadOnlySource)
    source.connection = db
    rows = source.release_rows(run)
    assert rows["events"][0]["canonical"] == {key: value for key, value in event_row.items() if key not in {"id", "content_hash"}}
    assert rows["rankings"][0]["canonical"] == {key: value for key, value in ranking_row.items() if key not in {"id", "content_hash"}}
    assert hashlib.sha256(json.dumps(rows["events"][0]["canonical"], sort_keys=True, separators=(",", ":")).encode()).hexdigest() == event_row["content_hash"]
    assert hashlib.sha256(json.dumps(rows["rankings"][0]["canonical"], sort_keys=True, separators=(",", ":")).encode()).hexdigest() == ranking_row["content_hash"]


def test_actual_terminal_commit_survives_lost_response_and_independent_process_retry(databases):
    import multiprocessing
    db = databases["fresh"]
    run_id = str(uuid.uuid4())
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,'on-demand')", (run_id,))
    timestamp = db.execute("SELECT statement_timestamp()").fetchone()[0].isoformat()
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    first = context.Process(target=_independent_worker, args=(db.info.dsn, run_id, timestamp, True, output))
    first.start(); first.join(20)
    assert first.exitcode == 73
    before = db.execute("SELECT completion_id::text,payload->'packet'->>'packet_hash' FROM market_intelligence_collection_completions WHERE run_id=%s", (run_id,)).fetchone()
    second = context.Process(target=_independent_worker, args=(db.info.dsn, run_id, timestamp, False, output))
    second.start(); second.join(20)
    assert second.exitcode == 0
    result = output.get(timeout=2)
    assert (result["completion_id"], result["packet_hash"]) == before
    assert result["actual_requests"] == 1 and result["cache_hits"] == 0
    assert db.execute("SELECT count(*),sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run_id,)).fetchone() == (1, 1)
    assert db.execute("SELECT count(*) FROM market_intelligence_run_events WHERE run_id=%s AND status='completed'", (run_id,)).fetchone()[0] == 1


def test_independent_worker_hydrates_successful_checkpoint_with_distinct_receipt_and_actual_ledger(databases):
    import multiprocessing
    from datetime import timedelta
    db = databases["ordered"]
    run_id = str(uuid.uuid4())
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,'on-demand')", (run_id,))
    now = db.execute("SELECT statement_timestamp()").fetchone()[0]
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    first = context.Process(target=_independent_worker, args=(db.info.dsn, run_id, now.isoformat(), "checkpoint", output))
    first.start(); first.join(20)
    assert first.exitcode == 74
    original = db.execute("SELECT source_receipt_id::text FROM market_collection_checkpoints WHERE run_id=%s", (run_id,)).fetchone()[0]
    second = context.Process(target=_independent_worker, args=(db.info.dsn, run_id, (now+timedelta(seconds=10)).isoformat(), False, output))
    second.start(); second.join(20)
    assert second.exitcode == 0
    result = output.get(timeout=2)
    assert result["actual_requests"] == 0 and result["cache_hits"] == 1
    hit = result["receipts"][0]["receipt_id"]
    assert hit != original
    assert db.execute("SELECT cache_receipt_id::text,cache_predecessor_receipt_id::text FROM market_checkpoint_receipt_lineage WHERE run_id=%s", (run_id,)).fetchone() == (hit, original)
    assert db.execute("SELECT status,request_cost FROM market_source_receipts WHERE id=%s", (original,)).fetchone() == ("succeeded", 1)
    assert db.execute("SELECT sum(request_cost) FROM market_source_receipts WHERE run_id=%s", (run_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_scheduled_lifecycle_uses_one_slot_run_binds_retries_and_keeps_prior_overdue_slots(databases, kind):
    """Exercise the installed final RPCs without an external database or scheduler."""
    db = databases[kind]
    market_date = "2026-09-08"
    first_request, second_request = uuid.uuid4(), uuid.uuid4()
    first_lease, second_lease = uuid.uuid4(), uuid.uuid4()
    for request_id, lease_token in ((first_request, first_lease), (second_request, second_lease)):
        db.execute(
            "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token) VALUES(%s,'start_run','claimed',%s)",
            (request_id, lease_token),
        )

    first = db.execute(
        "SELECT public.start_market_analysis_run(%s,%s,'intraday',%s)",
        (first_request, first_lease, market_date),
    ).fetchone()[0]
    second = db.execute(
        "SELECT public.start_market_analysis_run(%s,%s,'intraday',%s)",
        (second_request, second_lease, market_date),
    ).fetchone()[0]
    assert first["duplicate"] is False
    assert second == {"run_id": first["run_id"], "duplicate": True}
    assert db.execute(
        "SELECT count(DISTINCT run_id), count(*) FROM market_gateway_requests WHERE request_id IN (%s,%s)",
        (first_request, second_request),
    ).fetchone() == (1, 2)
    assert db.execute(
        "SELECT to_regprocedure('public.start_market_analysis_run(uuid,uuid,text)')"
    ).fetchone()[0] is None

    policy = db.execute("SELECT config FROM market_policy_config WHERE version=1").fetchone()[0]
    policy.update({"market_calendar_year": 2026, "nyse_holidays": []})
    db.execute("UPDATE market_policy_config SET config=%s, active=true WHERE version=1", (Jsonb(policy),))
    db.execute("UPDATE market_scheduled_phase_deadlines SET effective_on='2026-09-07'")
    overdue = db.execute(
        "SELECT market_date::text, phase, deadline_at AT TIME ZONE 'America/Chicago' "
        "FROM public.read_overdue_scheduled_market_phases('2026-09-08 23:00:00-05')"
    ).fetchall()
    assert ("2026-09-07", "pre-market", datetime(2026, 9, 7, 6, 45)) in overdue
    assert ("2026-09-07", "intraday", datetime(2026, 9, 7, 12, 15)) in overdue
    assert ("2026-09-07", "post-market", datetime(2026, 9, 7, 15, 25)) in overdue
    assert ("2026-09-08", "intraday", datetime(2026, 9, 8, 12, 15)) in overdue
    db.execute(
        "INSERT INTO market_scheduled_phase_deadlines(phase,deadline_local,grace_minutes,effective_on) "
        "VALUES('intraday','12:30',15,'2026-09-08')"
    )
    versioned = db.execute(
        "SELECT market_date::text, deadline_at AT TIME ZONE 'America/Chicago' "
        "FROM public.read_overdue_scheduled_market_phases('2026-09-08 23:00:00-05') "
        "WHERE phase='intraday' ORDER BY market_date"
    ).fetchall()
    assert versioned == [
        ("2026-09-07", datetime(2026, 9, 7, 12, 15)),
        ("2026-09-08", datetime(2026, 9, 8, 12, 45)),
    ]
    policy["nyse_holidays"] = ["2026-09-07"]
    db.execute("UPDATE market_policy_config SET config=%s WHERE version=1", (Jsonb(policy),))
    assert all(row[0] != "2026-09-07" for row in db.execute(
        "SELECT market_date::text FROM public.read_overdue_scheduled_market_phases('2026-09-08 23:00:00-05')"
    ).fetchall())
    policy["nyse_holidays"] = {"not": "a calendar"}
    db.execute("UPDATE market_policy_config SET config=%s WHERE version=1", (Jsonb(policy),))
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="calendar coverage missing"):
        db.execute("SELECT * FROM public.read_overdue_scheduled_market_phases('2026-09-08 23:00:00-05')")


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_scheduled_finish_requires_successful_date_bound_receipt_chain(databases, kind):
    db = databases[kind]
    market_date = "2026-09-09"
    start_request, start_lease = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token) VALUES(%s,'start_run','claimed',%s)",
        (start_request, start_lease),
    )
    run = db.execute(
        "SELECT public.start_market_analysis_run(%s,%s,'intraday',%s)",
        (start_request, start_lease, market_date),
    ).fetchone()[0]["run_id"]
    run, completion, _, payload = prepared_run(db, run_id=run, phase="intraday", market_date=market_date)
    db.execute("SELECT public.record_market_intelligence(%s,%s,%s)", (run, completion, Jsonb(payload)))

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_EVALUATION_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))

    evaluation_request = uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,run_id,status,lease_token) VALUES(%s,'evaluate_and_publish',%s,'completed',%s)",
        (evaluation_request, run, uuid.uuid4()),
    )
    db.execute(
        "INSERT INTO market_publications(id,idempotency_key,run_id,market_date,phase,kind,template_version,rendered_body,rendered_hash,status) "
        "VALUES(%s,%s,%s,'2026-09-08','intraday','brief',2,'suppressed','a'::text || repeat('a',63),'suppressed')",
        (uuid.uuid4(), evaluation_request, run),
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_EVALUATION_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    db.execute("UPDATE market_publications SET market_date=%s WHERE run_id=%s", (market_date, run))

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_REPORT_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    report_request, report_id = uuid.uuid4(), uuid.uuid4()
    packet_id = db.execute("SELECT id FROM market_evidence_packets WHERE run_id=%s", (run,)).fetchone()[0]
    db.execute(
        "INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) "
        "VALUES(%s,repeat('b',64),%s,%s,'2026-09-08','intraday','{}',repeat('c',64),'suppressed',repeat('d',64))",
        (report_id, run, packet_id),
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_REPORT_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token,response) VALUES(%s,'record_report','completed',%s,%s)",
        (report_request, uuid.uuid4(), Jsonb({"ok": True, "report_id": str(report_id), "report_hash": "c" * 64, "rendered_hash": "d" * 64})),
    )
    wrong_kind_report_id = uuid.uuid4()
    db.execute(
        "INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) "
        "VALUES(%s,repeat('f',64),%s,%s,%s,'morning','{}',repeat('c',64),'suppressed',repeat('d',64))",
        (wrong_kind_report_id, run, packet_id, market_date),
    )
    wrong_kind_request = uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token,response) VALUES(%s,'record_report','completed',%s,%s)",
        (wrong_kind_request, uuid.uuid4(), Jsonb({"ok": True, "report_id": str(wrong_kind_report_id), "report_hash": "c" * 64, "rendered_hash": "d" * 64})),
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_REPORT_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    correct_report_id = uuid.uuid4()
    db.execute(
        "INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) "
        "VALUES(%s,repeat('1',64),%s,%s,%s,'intraday','{}',repeat('c',64),'suppressed',repeat('d',64))",
        (correct_report_id, run, packet_id, market_date),
    )
    correct_report_request = uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token,response) VALUES(%s,'record_report','completed',%s,%s)",
        (correct_report_request, uuid.uuid4(), Jsonb({"ok": True, "report_id": str(correct_report_id), "report_hash": "c" * 64, "rendered_hash": "d" * 64})),
    )

    # A final intraday kind cannot stand in for the immutable scheduled origin.
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_REPORT_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    db.execute(
        "INSERT INTO market_report_request_origins(request_id,run_id,scheduled_phase,market_date,requested_kind,requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash) "
        "VALUES(%s,%s,'pre-market',%s,'morning',%s,%s,repeat('2',64),repeat('3',64))",
        (correct_report_request, run, market_date, uuid.uuid4(), packet_id),
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_REPORT_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    db.execute("DELETE FROM market_report_request_origins WHERE request_id=%s", (correct_report_request,))
    db.execute(
        "INSERT INTO market_report_request_origins(request_id,run_id,scheduled_phase,market_date,requested_kind,requested_report_id,requested_packet_id,requested_idempotency_key,requested_report_hash) "
        "VALUES(%s,%s,'intraday',%s,'intraday',%s,%s,repeat('2',64),repeat('3',64))",
        (correct_report_request, run, market_date, uuid.uuid4(), packet_id),
    )

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="MISSING_PUBLICATION_RECEIPT"):
        db.execute("SELECT public.finish_market_analysis_run(%s)", (run,))
    db.execute(
        "INSERT INTO market_report_publications(report_id,idempotency_key,status,suppression_reason) VALUES(%s,repeat('e',64),'suppressed','no_trigger')",
        (correct_report_id,),
    )
    assert db.execute("SELECT public.finish_market_analysis_run(%s)", (run,)).fetchone()[0]["status"] == "suppressed"


def test_reconciled_cash_snapshot_is_explicit_fresh_and_invalidated_by_ledger_mutation(databases):
    db = databases["ordered"]
    snapshot_id = uuid.uuid4()
    now = db.execute("SELECT statement_timestamp()").fetchone()[0]
    ledger = db.execute(
        "SELECT public.read_portfolio_cash_ledger_watermark()"
    ).fetchone()[0]
    watermark = int(ledger["ledger_watermark"])
    assert ledger["ledger_updated_at"] <= now.isoformat()
    recorded = db.execute(
        "SELECT public.record_reconciled_cash_snapshot(%s,%s,%s,%s,%s)",
        (
            snapshot_id,
            now,
            now + timedelta(minutes=15),
            watermark,
            Jsonb({"core": "300", "growth": "1000", "speculative": "0"}),
        ),
    ).fetchone()[0]
    assert recorded["snapshot_id"] == str(snapshot_id)
    assert datetime.fromisoformat(recorded["as_of"]) == now
    assert datetime.fromisoformat(recorded["fresh_through"]) == now + timedelta(minutes=15)
    assert recorded["ledger_watermark"] == str(watermark)
    assert recorded["spendable_cash"] == {
        "core": "300", "growth": "1000", "speculative": "0"
    }
    assert recorded["duplicate"] is False
    current = db.execute(
        "SELECT public.read_reconciled_cash_snapshot(%s)", (now,)
    ).fetchone()[0]
    assert current["snapshot_id"] == str(snapshot_id)
    assert current["ledger_watermark"] == str(watermark)

    db.execute(
        "INSERT INTO public.transactions(ticker,side,qty,price,source,executed_on) "
        "VALUES('CASHX','buy',1,1,'test',CURRENT_DATE)"
    )
    assert db.execute(
        "SELECT public.read_reconciled_cash_snapshot(%s)", (now,)
    ).fetchone()[0] is None
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState, match="ACTION_LANE_REQUIRED"
    ):
        db.execute(
            "SELECT public.apply_market_decision_bundle_with_cash_snapshot("
            "%s,%s,%s,1,'[{\"policy_status\":\"approved\","
            "\"final_action\":\"buy\"}]'::jsonb,'[]'::jsonb,'{}'::jsonb,%s,%s)",
            (uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), snapshot_id, watermark),
        )
    assert int(db.execute(
        "SELECT public.read_portfolio_cash_ledger_watermark()"
    ).fetchone()[0]["ledger_watermark"]) > watermark
    start_request, start_lease = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token) "
        "VALUES(%s,'start_run','claimed',%s)",
        (start_request, start_lease),
    )
    run = db.execute(
        "SELECT public.start_market_analysis_run(%s,%s,'intraday','2026-09-11')",
        (start_request, start_lease),
    ).fetchone()[0]["run_id"]
    retry_request, retry_lease = uuid.uuid4(), uuid.uuid4()
    publication_id = uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,run_id,status,lease_token) "
        "VALUES(%s,'evaluate_and_publish',%s,'claimed',%s)",
        (retry_request, run, retry_lease),
    )
    db.execute(
        "INSERT INTO market_publications(id,idempotency_key,run_id,market_date,phase,kind,"
        "template_version,rendered_body,rendered_hash,status) "
        "VALUES(%s,%s,%s,'2026-09-11','intraday','brief',2,'',repeat('a',64),'suppressed')",
        (publication_id, retry_request, run),
    )
    recovered = db.execute(
        "SELECT public.apply_market_decision_bundle_with_cash_snapshot("
        "%s,%s,%s,1,'[{\"policy_status\":\"approved\","
        "\"final_action\":\"buy\"}]'::jsonb,'[]'::jsonb,'{}'::jsonb,%s,%s)",
        (retry_request, run, retry_lease, snapshot_id, watermark),
    ).fetchone()[0]
    assert recovered == {
        "publication_id": str(publication_id),
        "status": "suppressed",
        "duplicate": True,
    }
    db.execute(
        "INSERT INTO public.dry_powder(month,growth_available,spec_available,rolled_months) "
        "VALUES('2099-12',999999999,999999999,99) ON CONFLICT(month) DO UPDATE "
        "SET growth_available=EXCLUDED.growth_available"
    )
    assert db.execute(
        "SELECT public.read_reconciled_cash_snapshot(%s)", (now,)
    ).fetchone()[0] is None
    assert not db.execute(
        "SELECT has_function_privilege('authenticated',"
        "'public.record_reconciled_cash_snapshot(uuid,timestamptz,timestamptz,bigint,jsonb)','EXECUTE')"
    ).fetchone()[0]
    assert not db.execute(
        "SELECT has_function_privilege('authenticated',"
        "'public.read_portfolio_cash_ledger_watermark()','EXECUTE')"
    ).fetchone()[0]


def test_quiet_scheduled_intraday_outcome_finishes_without_a_report(databases):
    db = databases["ordered"]
    market_date = "2026-09-10"
    start_request, start_lease = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,status,lease_token) "
        "VALUES(%s,'start_run','claimed',%s)",
        (start_request, start_lease),
    )
    start = db.execute(
        "SELECT public.start_market_analysis_run(%s,%s,'intraday',%s)",
        (start_request, start_lease, market_date),
    ).fetchone()[0]
    assert start["duplicate"] is False
    run, completion, _, payload = prepared_run(
        db, run_id=start["run_id"], phase="intraday", market_date=market_date
    )
    db.execute(
        "SELECT public.record_market_intelligence(%s,%s,%s)",
        (run, completion, Jsonb(payload)),
    )

    evaluation_request, evaluation_lease = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,run_id,status,lease_token) "
        "VALUES(%s,'evaluate_and_publish',%s,'claimed',%s)",
        (evaluation_request, run, evaluation_lease),
    )
    db.execute(
        "INSERT INTO market_publications(id,idempotency_key,run_id,market_date,phase,kind,"
        "template_version,rendered_body,rendered_hash,status) "
        "VALUES(%s,%s,%s,%s,'intraday','brief',2,'',repeat('a',64),'suppressed')",
        (uuid.uuid4(), evaluation_request, run, market_date),
    )
    outcome = db.execute(
        "SELECT public.record_market_run_outcome(%s,%s,%s,'no_trigger')",
        (evaluation_request, evaluation_lease, run),
    ).fetchone()[0]
    assert outcome == {"run_id": str(run), "outcome": "no_trigger", "duplicate": False}
    replay = db.execute(
        "SELECT public.record_market_run_outcome(%s,%s,%s,'no_trigger')",
        (evaluation_request, evaluation_lease, run),
    ).fetchone()[0]
    assert replay["duplicate"] is True
    unrelated_request, unrelated_lease = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO market_gateway_requests(request_id,operation,run_id,status,lease_token) "
        "VALUES(%s,'evaluate_and_publish',%s,'claimed',%s)",
        (unrelated_request, run, unrelated_lease),
    )
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="suppressed evaluation receipt unavailable",
    ):
        db.execute(
            "SELECT public.record_market_run_outcome(%s,%s,%s,'no_trigger')",
            (unrelated_request, unrelated_lease, run),
        )
    db.execute(
        "UPDATE market_gateway_requests SET status='completed',response=%s WHERE request_id=%s",
        (Jsonb({"ok": True, "run_outcome": outcome}), evaluation_request),
    )
    receipt = db.execute(
        "SELECT public.finish_market_analysis_run(%s)", (run,)
    ).fetchone()[0]
    assert receipt["status"] == "suppressed"
    assert db.execute(
        "SELECT count(*) FROM market_reports WHERE run_id=%s", (run,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM market_report_publications p JOIN market_reports r ON r.id=p.report_id "
        "WHERE r.run_id=%s", (run,)
    ).fetchone()[0] == 0
