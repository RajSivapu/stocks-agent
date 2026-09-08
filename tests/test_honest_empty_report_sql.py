from datetime import date
import hashlib
import json
import os
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

from lib.intelligence.reports import ReportInput, build_report


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261014_honest_empty_report_persistence.sql"
SCHEMA = ROOT / "sql/schema.sql"


def canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()


def report_id(key):
    value = list(key[:32])
    value[12], value[16] = "5", "8"
    return str(uuid.UUID("".join(value)))


def test_honest_empty_report_migration_is_additive_and_preserves_prior_contract_bytes():
    assert len(parse_sql(MIGRATION.read_text())) == 4
    assert SCHEMA.read_bytes().count(MIGRATION.read_bytes()) == 1
    assert hashlib.sha256((ROOT / "sql/migrations/20261011_research_suitability_packet_contract.sql").read_bytes()).hexdigest() == "f6e178ef986265d6008dcc381293e05e61802c2f90465b4c69b7410ab668dbe6"
    sql = MIGRATION.read_text()
    assert "v_honest_empty" in sql
    assert "disposition IN ('accepted','near_duplicate')" in sql
    assert "market_v2_sorted_unique_text_array(p_report->'report'->'source_ids',96,false)" in sql


def render_with_gateway(payload, packet):
    command = [
        "npx", "--yes", "deno@2.9.6", "run", "--quiet", "--cached-only",
        "--config", "supabase/functions/deno.json",
        "tests/helpers/render_report_payload.ts",
    ]
    result = subprocess.run(
        command, cwd=ROOT, input=json.dumps({"input": payload, "packet": packet}),
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def insert_packet(db, run_id, packet, *, create_run=True):
    packet_id = str(uuid.uuid4())
    packet_hash = canonical_hash(packet)
    if create_run:
        db.execute(
            "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) "
            "VALUES(%s,'on-demand','2026-09-08',1,'{}')", (run_id,),
        )
        db.execute(
            "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
            (str(uuid.uuid4()), run_id),
        )
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,"
        "evidence_count,packet,packet_hash) VALUES(%s,%s,1,'completed',%s,%s,%s,%s)",
        (packet_id, run_id, len(packet["research_candidates"]), len(packet["evidence"]),
         Jsonb(packet), packet_hash),
    )
    return packet_id, packet_hash


def test_actual_gateway_rendered_empty_report_and_near_duplicate_persist_in_postgres():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()) or shutil.which("npx") is None or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL and the pinned Deno runner are required")
    with tempfile.TemporaryDirectory(prefix="honest-empty-report-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        subprocess.run([
            binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8",
            "--no-locale",
        ], check=True, capture_output=True)
        subprocess.run([
            binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
            "-o", f"-k {root} -h '' -p {port}", "-w", "start",
        ], check=True, capture_output=True)
        try:
            with psycopg.connect(
                f"host={root} port={port} dbname=postgres", autocommit=True,
            ) as db:
                db.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
                db.execute("CREATE ROLE stock_agent_dashboard; CREATE ROLE stock_agent_release_reader; CREATE ROLE stock_agent_release_reader_runtime")
                db.execute(SCHEMA.read_text())
                db.execute("INSERT INTO market_policy_config(version,config,active) VALUES(1,'{}',true)")

                empty_run = str(uuid.uuid4())
                empty_packet = {
                    "action_candidates": [], "contract_version": 2,
                    "coverage": {"complete_market_coverage": False, "mode": "bounded"},
                    "evidence": [], "execution_allowed": False, "limitations": [],
                    "observed_at": "2026-09-08T12:00:00.000Z", "omissions": [],
                    "policy_version": 1, "research_candidates": [], "run_id": empty_run,
                }
                empty_packet_id, empty_packet_hash = insert_packet(db, empty_run, empty_packet)
                produced = build_report(ReportInput(
                    packet_id=empty_packet_id, packet_hash=empty_packet_hash,
                    market_date=date(2026, 9, 8), kind="on-demand",
                    title="MARKET RESEARCH", summary="No bounded candidate qualified.",
                    full_markdown="No bounded candidate qualified. Suggestion only; no order was placed.",
                    source_ids=(), policy_decision_ids=(), comparison_ids=(),
                    actionable_risk=False, material_thesis_change=False,
                    intraday_triggered=False, research_packet=empty_packet,
                )).to_gateway_payload()
                rendered = render_with_gateway(produced, empty_packet)
                db.execute("SET ROLE service_role")
                receipt = db.execute(
                    "SELECT record_market_report(%s,%s,%s)->>'report_id'",
                    (empty_run, rendered["idempotency_key"], Jsonb({
                        key: value for key, value in rendered.items()
                        if key != "idempotency_key"
                    })),
                ).fetchone()[0]
                db.execute("RESET ROLE")
                assert receipt == rendered["id"]

                from test_verify_personal_stock_agent_v1 import (
                    _add_unresolved_research_candidate, _capability_rows,
                )
                fixture = _capability_rows()
                _add_unresolved_research_candidate(fixture)
                near_packet = fixture["packets"][0]["packet"]
                near_packet["evidence"][0]["published_at"] = "2026-09-05T19:35:00.000Z"
                near_packet["evidence"][0]["retrieved_at"] = "2026-09-05T19:40:00.000Z"
                candidate = near_packet["research_candidates"][0]
                candidate["suitability"]["missing_reasons"] = [
                    "analysis_not_ready", "valuation_missing",
                ]
                candidate["suitability"]["evaluation_hash"] = canonical_hash({
                    key: value for key, value in candidate["suitability"].items()
                    if key != "evaluation_hash"
                })
                candidate["candidate_hash"] = canonical_hash({
                    key: value for key, value in candidate.items() if key != "candidate_hash"
                })
                near_run = near_packet["run_id"]
                stored = next(row for row in fixture["source_receipts"]
                              if row["id"] == fixture["intelligence_run_items"][0]["source_receipt_id"])
                reservation = next(row for row in fixture["source_quota_reservations"]
                                   if row["id"] == stored["reservation_id"])
                item = fixture["source_items"][0]
                run_item = fixture["intelligence_run_items"][0]
                item_provenance = fixture["source_item_provenance"][0]
                run_provenance = fixture["run_source_item_provenance"][0]
                item_id, receipt_id = item["id"], stored["id"]
                db.execute(
                    "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'post-market','2026-09-05',1,'{}')",
                    (near_run,),
                )
                db.execute(
                    "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
                    (str(uuid.uuid4()), near_run),
                )
                db.execute(
                    "INSERT INTO market_source_quota_reservations(id,run_id,provider,market_date,phase,reserved_requests,cache_keys,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (reservation["id"], near_run, reservation["provider"],
                     reservation["market_date"], reservation["phase"],
                     reservation["reserved_requests"], Jsonb(reservation["cache_keys"]),
                     reservation["created_at"]),
                )
                db.execute(
                    "INSERT INTO market_source_receipts(id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,request_cost,upstream_remaining,returned_count,accepted_count,duplicate_count,dropped_count,error,response_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (stored["id"], near_run, stored["reservation_id"], stored["provider"],
                     stored["status"], stored["cache_key"], Jsonb(stored["requested_window"]),
                     stored["retrieved_at"], stored["expires_at"], stored["request_cost"],
                     stored["upstream_remaining"], stored["returned_count"],
                     stored["accepted_count"], stored["duplicate_count"], stored["dropped_count"],
                     stored["error"], stored["response_hash"], stored["created_at"]),
                )
                db.execute(
                    "INSERT INTO market_source_items(id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,effective_at,title,normalized_text,canonical_content,content_hash,metadata,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (item["id"], item["source_receipt_id"], item["provider"],
                     item["upstream_item_id"], item["canonical_url"], item["published_at"],
                     item["effective_at"], item["title"], item["normalized_text"],
                     item["canonical_content"], item["content_hash"], Jsonb(item["metadata"]),
                     item["created_at"]),
                )
                db.execute(
                    "INSERT INTO market_intelligence_run_items(id,run_id,source_item_id,source_receipt_id,disposition,drop_reason) VALUES(%s,%s,%s,%s,'near_duplicate','same_story_different_source')",
                    (run_item["id"], near_run, item_id, receipt_id),
                )
                db.execute(
                    "INSERT INTO market_source_item_provenance(source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (item_provenance["source_item_id"], item_provenance["provider"],
                     item_provenance["canonical_item_url"], item_provenance["request_url"],
                     item_provenance["retrieved_at"], item_provenance["reporting_at"],
                     Jsonb(item_provenance["entity_ids"]), Jsonb(item_provenance["security_ids"]),
                     item_provenance["discovery_status"], item_provenance["created_at"]),
                )
                db.execute(
                    "INSERT INTO market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_provenance["run_item_id"], near_run, run_provenance["source_item_id"],
                     run_provenance["source_receipt_id"], run_provenance["provider"],
                     run_provenance["request_url"], run_provenance["retrieved_at"],
                     run_provenance["reporting_at"], Jsonb(run_provenance["entity_ids"]),
                     Jsonb(run_provenance["security_ids"]), run_provenance["discovery_status"],
                     run_provenance["created_at"]),
                )
                event_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO market_events(id,run_id,event_type,title,summary,materiality,confidence,evidence_item_ids,content_hash) VALUES(%s,%s,'thematic_event','Market event','Source-backed market event',0.5,0.5,%s,%s)",
                    (event_id, near_run, Jsonb([item_id]), "c" * 64),
                )
                db.execute(
                    "INSERT INTO market_candidate_rankings(id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,veto_reasons,exposure_item_ids,content_hash) VALUES(%s,%s,%s,%s,NULL,1,'{}',1,false,'[]','[]',%s)",
                    (str(uuid.uuid4()), near_run, event_id,
                     near_packet["research_candidates"][0]["candidate_key"], "d" * 64),
                )
                near_packet_id, near_packet_hash = insert_packet(
                    db, near_run, near_packet, create_run=False,
                )
                body = {
                    "title": "THEME RESEARCH", "summary": "Retained corroborating source.",
                    "full_markdown": "Retained corroborating source. Suggestion only; no order was placed.",
                    "source_ids": [item_id], "policy_decision_ids": [],
                    "comparison_ids": [], "actionable_risk": False,
                    "material_thesis_change": False, "intraday_triggered": False,
                    "suggestion_only": True,
                }
                body_hash = canonical_hash(body)
                key = hashlib.sha256(
                    f"v2:theme:2026-09-08:{near_packet_hash}:{body_hash}".encode()
                ).hexdigest()
                payload = {
                    "id": report_id(key), "packet_id": near_packet_id,
                    "market_date": "2026-09-08", "kind": "theme", "report": body,
                    "report_hash": body_hash, "rendered_text": body["full_markdown"],
                    "rendered_hash": hashlib.sha256(body["full_markdown"].encode()).hexdigest(),
                }
                db.execute("SET ROLE service_role")
                saved = db.execute(
                    "SELECT record_market_report(%s,%s,%s)->>'report_id'",
                    (near_run, key, Jsonb(payload)),
                ).fetchone()[0]
                db.execute("RESET ROLE")
                assert saved == payload["id"]
        finally:
            subprocess.run([
                binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop",
            ], check=True, capture_output=True)
