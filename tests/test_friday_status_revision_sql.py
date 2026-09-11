from pathlib import Path
from datetime import date
import hashlib
import json
import os
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
from test_honest_empty_report_sql import canonical_hash, render_with_gateway


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261029_scheduled_friday_status_revision.sql"
SCHEMA = ROOT / "sql/schema.sql"


def test_scheduled_friday_status_revision_is_narrow_and_immutable():
    sql = MIGRATION.read_text()

    assert len(parse_sql(sql)) == 5
    assert SCHEMA.read_bytes().count(MIGRATION.read_bytes()) == 1
    assert "market_date, kind, report_hash" in sql
    assert "v_run.scheduled_phase IS DISTINCT FROM 'post-market'" in sql
    assert "EXTRACT(ISODOW FROM (p_report->>'market_date')::date) <> 5" in sql
    assert "v_publication.status IS DISTINCT FROM 'suppressed'" in sql
    assert "v_publication.suppression_reason IS DISTINCT FROM 'not_actionable'" in sql
    assert "jsonb_array_length(v_publication.telegram_message_ids) <> 0" in sql
    assert "v_publication.telegram_accepted_at IS NOT NULL" in sql
    assert "v_slot_count <> 1" in sql
    assert "UPDATE public.market_reports" not in sql
    assert "UPDATE public.market_report_publications" not in sql
    assert "status='delivered'" not in sql


def test_suppressed_friday_report_gets_one_new_immutable_revision_in_postgres():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()) or shutil.which("npx") is None or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL and the pinned Deno runner are required")

    with tempfile.TemporaryDirectory(prefix="friday-status-revision-") as directory:
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

                run_id = str(uuid.uuid4())
                packet_id = str(uuid.uuid4())
                packet = {
                    "action_candidates": [], "contract_version": 2,
                    "coverage": {"complete_market_coverage": False, "mode": "bounded"},
                    "evidence": [], "execution_allowed": False, "limitations": [],
                    "observed_at": "2026-09-11T20:30:00.000Z", "omissions": [],
                    "policy_version": 1, "research_candidates": [], "run_id": run_id,
                }
                packet_hash = canonical_hash(packet)
                db.execute(
                    "INSERT INTO analysis_runs(id,kind,status,scheduled_market_date,scheduled_phase) "
                    "VALUES(%s,'post-market','partial','2026-09-11','post-market')",
                    (run_id,),
                )
                db.execute(
                    "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) "
                    "VALUES(%s,'post-market','2026-09-11',1,'{}')",
                    (run_id,),
                )
                db.execute(
                    "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
                    (str(uuid.uuid4()), run_id),
                )
                db.execute(
                    "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,"
                    "evidence_count,packet,packet_hash) VALUES(%s,%s,1,'completed',0,0,%s,%s)",
                    (packet_id, run_id, Jsonb(packet), packet_hash),
                )
                authored = build_report(ReportInput(
                    packet_id=packet_id, packet_hash=packet_hash,
                    market_date=date(2026, 9, 11), kind="weekly",
                    title="WEEKLY RESEARCH", summary="No bounded candidate qualified.",
                    full_markdown="No bounded candidate qualified. Suggestion only; no order was placed.",
                    source_ids=(), policy_decision_ids=(), comparison_ids=(),
                    actionable_risk=False, material_thesis_change=False,
                    intraday_triggered=False, research_packet=packet,
                )).to_gateway_payload()
                old_report = render_with_gateway(authored, packet)
                friday_report = render_with_gateway(authored, packet, scheduled=True)
                assert old_report["id"] != friday_report["id"]

                db.execute("SET ROLE service_role")
                old_receipt = db.execute(
                    "SELECT record_market_report(%s,%s,%s)",
                    (run_id, old_report["idempotency_key"], Jsonb({
                        key: value for key, value in old_report.items()
                        if key != "idempotency_key"
                    })),
                ).fetchone()[0]
                db.execute(
                    "SELECT create_market_report_publication(%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, old_report["id"], old_report["idempotency_key"],
                     "2026-09-11", "weekly", old_report["rendered_text"],
                     old_report["rendered_hash"]),
                )
                db.execute(
                    "SELECT suppress_market_report_publication(%s,'not_actionable')",
                    (old_report["idempotency_key"],),
                )
                db.execute("RESET ROLE")
                before = db.execute(
                    "SELECT r.report,r.report_hash,r.rendered_text,r.rendered_hash,p.status,"
                    "p.telegram_message_ids,p.telegram_accepted_at,p.suppression_reason "
                    "FROM market_reports r JOIN market_report_publications p ON p.report_id=r.id "
                    "WHERE r.id=%s",
                    (old_report["id"],),
                ).fetchone()

                db.execute("SET ROLE service_role")
                new_receipt = db.execute(
                    "SELECT record_market_report(%s,%s,%s)",
                    (run_id, friday_report["idempotency_key"], Jsonb({
                        key: value for key, value in friday_report.items()
                        if key != "idempotency_key"
                    })),
                ).fetchone()[0]
                db.execute(
                    "SELECT create_market_report_publication(%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, friday_report["id"], friday_report["idempotency_key"],
                     "2026-09-11", "weekly", friday_report["rendered_text"],
                     friday_report["rendered_hash"]),
                )
                replay = db.execute(
                    "SELECT record_market_report(%s,%s,%s)",
                    (run_id, friday_report["idempotency_key"], Jsonb({
                        key: value for key, value in friday_report.items()
                        if key != "idempotency_key"
                    })),
                ).fetchone()[0]
                db.execute("RESET ROLE")

                assert old_receipt["duplicate"] is False
                assert new_receipt["duplicate"] is False
                assert replay["duplicate"] is True
                assert db.execute(
                    "SELECT count(*) FROM market_reports WHERE run_id=%s AND packet_id=%s "
                    "AND market_date='2026-09-11' AND kind='weekly'",
                    (run_id, packet_id),
                ).fetchone()[0] == 2
                assert db.execute(
                    "SELECT r.report,r.report_hash,r.rendered_text,r.rendered_hash,p.status,"
                    "p.telegram_message_ids,p.telegram_accepted_at,p.suppression_reason "
                    "FROM market_reports r JOIN market_report_publications p ON p.report_id=r.id "
                    "WHERE r.id=%s",
                    (old_report["id"],),
                ).fetchone() == before
                assert db.execute(
                    "SELECT status,telegram_message_ids,telegram_accepted_at,suppression_reason "
                    "FROM market_report_publications WHERE report_id=%s",
                    (friday_report["id"],),
                ).fetchone() == ("pending", [], None, None)

                third = dict(friday_report)
                third["report"] = dict(third["report"])
                third["report"]["summary"] += " Extra revision."
                third["report_hash"] = canonical_hash(third["report"])
                third["idempotency_key"] = hashlib.sha256(
                    f"v2:weekly:2026-09-11:{packet_hash}:{third['report_hash']}".encode()
                ).hexdigest()
                value = list(third["idempotency_key"][:32])
                value[12], value[16] = "5", "8"
                third["id"] = str(uuid.UUID("".join(value)))
                db.execute("SET ROLE service_role")
                with pytest.raises(psycopg.Error, match="market report idempotency mismatch"):
                    db.execute(
                        "SELECT record_market_report(%s,%s,%s)",
                        (run_id, third["idempotency_key"], Jsonb({
                            key: value for key, value in third.items()
                            if key != "idempotency_key"
                        })),
                    )
                db.execute("RESET ROLE")
        finally:
            subprocess.run([
                binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop",
            ], check=True, capture_output=True)
