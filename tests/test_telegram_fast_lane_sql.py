from __future__ import annotations

import json
from pathlib import Path
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_intelligence_controller_sql import databases


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261031_telegram_fast_lane.sql"


def _window(phase: str, market_date: str) -> dict[str, str]:
    return {
        "start": f"{market_date}T10:00:00+00:00",
        "end": f"{market_date}T11:00:00+00:00",
        "timezone": "America/Chicago",
        "market_date": market_date,
        "phase": phase,
    }


def _start(db, *, phase: str, market_date: str, lane: str, scheduled: bool):
    run_id = str(uuid.uuid4())
    if scheduled:
        db.execute(
            "INSERT INTO analysis_runs(id,kind,scheduled_market_date,scheduled_phase) VALUES(%s,%s,%s,%s)",
            (run_id, phase, market_date, phase),
        )
    else:
        db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,%s)", (run_id, phase))
    receipt = db.execute(
        "SELECT public.start_market_intelligence_run(%s,%s,%s,1,%s,%s,%s)",
        (run_id, phase, market_date, Jsonb({"reservations": []}), Jsonb(_window(phase, market_date)), lane),
    ).fetchone()[0]
    return run_id, receipt


def test_migration_is_additive_mirrored_and_least_privilege():
    migration = MIGRATION.read_text()
    schema = (ROOT / "sql" / "schema.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS lane TEXT" in migration
    assert "CHECK (lane IN ('alert','research'))" in migration
    assert "start_market_intelligence_run(" in migration
    assert "p_lane TEXT" in migration
    assert "read_latest_terminal_research_packet" in migration
    assert "research intelligence cannot publish" in migration
    assert "GRANT EXECUTE ON FUNCTION public.read_latest_terminal_research_packet(UUID,DATE,TIMESTAMPTZ) TO service_role" in migration
    assert "-- Consolidated from sql/migrations/20261031_telegram_fast_lane.sql" in schema
    assert migration.strip() in schema


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_lane_identity_is_derived_and_idempotent(databases, kind):
    db = databases[kind]
    alert_run, alert = _start(
        db, phase="pre-market", market_date="2026-11-16", lane="alert", scheduled=True
    )
    research_run, research = _start(
        db, phase="on-demand", market_date="2026-11-16", lane="research", scheduled=False
    )

    assert alert["lane"] == "alert"
    assert research["lane"] == "research"
    assert db.execute(
        "SELECT lane FROM market_intelligence_runs WHERE id=%s", (alert_run,)
    ).fetchone()[0] == "alert"
    assert db.execute(
        "SELECT lane FROM market_intelligence_runs WHERE id=%s", (research_run,)
    ).fetchone()[0] == "research"

    duplicate = db.execute(
        "SELECT public.start_market_intelligence_run(%s,'pre-market','2026-11-16',1,%s,%s,'alert')",
        (alert_run, Jsonb({"reservations": []}), Jsonb(_window("pre-market", "2026-11-16"))),
    ).fetchone()[0]
    assert duplicate["duplicate"] is True
    assert duplicate["lane"] == "alert"

    with pytest.raises(psycopg.Error, match="lane"):
        db.execute(
            "SELECT public.start_market_intelligence_run(%s,'pre-market','2026-11-16',1,%s,%s,'research')",
            (alert_run, Jsonb({"reservations": []}), Jsonb(_window("pre-market", "2026-11-16"))),
        )
    with pytest.raises(psycopg.Error, match="lane"):
        _start(db, phase="on-demand", market_date="2026-11-17", lane="alert", scheduled=False)


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_reader_returns_only_recent_terminal_research_packet(databases, kind):
    db = databases[kind]
    alert_run, _ = _start(
        db, phase="pre-market", market_date="2026-11-20", lane="alert", scheduled=True
    )
    research_run, _ = _start(
        db, phase="on-demand", market_date="2026-11-19", lane="research", scheduled=False
    )
    packet_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO market_intelligence_run_events(id,run_id,status,detail) VALUES(%s,%s,'completed','{}')",
        (str(uuid.uuid4()), research_run),
    )
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) "
        "VALUES(%s,%s,1,'completed',0,0,'{}',%s)",
        (packet_id, research_run, "a" * 64),
    )
    db.execute("UPDATE analysis_runs SET status='completed',finished_at=now() WHERE id=%s", (research_run,))

    latest = db.execute(
        "SELECT public.read_latest_terminal_research_packet(%s,'2026-11-20','2026-11-20T12:00:00Z')",
        (alert_run,),
    ).fetchone()[0]

    assert latest == {
        "age_days": 1,
        "created_at": latest["created_at"],
        "market_date": "2026-11-19",
        "packet_hash": "a" * 64,
        "packet_id": packet_id,
    }


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_research_lane_cannot_create_report_or_publication(databases, kind):
    db = databases[kind]
    research_run, _ = _start(
        db, phase="on-demand", market_date="2026-11-23", lane="research", scheduled=False
    )
    packet_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) "
        "VALUES(%s,%s,1,'completed',0,0,'{}',%s)",
        (packet_id, research_run, "b" * 64),
    )
    with pytest.raises(psycopg.Error, match="research intelligence cannot publish"):
        db.execute(
            "INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) "
            "VALUES(%s,%s,%s,%s,'2026-11-23','on-demand','{}',%s,'research',%s)",
            (str(uuid.uuid4()), "c" * 64, research_run, packet_id, "d" * 64, "e" * 64),
        )


@pytest.mark.parametrize("kind", ["fresh", "ordered"])
def test_recovered_research_packet_retains_lane_and_publication_guard(databases, kind):
    db = databases[kind]
    research_run = str(uuid.uuid4())
    packet_id = str(uuid.uuid4())
    db.execute("INSERT INTO analysis_runs(id,kind) VALUES(%s,'on-demand')", (research_run,))
    db.execute(
        "INSERT INTO market_intelligence_runs(id,phase,lane,market_date,policy_version,reservation_plan,request_window) "
        "VALUES(%s,'on-demand','research','2026-11-24',1,'{}',%s)",
        (research_run, Jsonb(_window("on-demand", "2026-11-24"))),
    )
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) "
        "VALUES(%s,%s,1,'completed',0,0,'{}',%s)",
        (packet_id, research_run, "f" * 64),
    )

    restored = db.execute(
        "SELECT run.lane,packet.id::text,packet.packet_hash "
        "FROM market_intelligence_runs run JOIN market_evidence_packets packet ON packet.run_id=run.id "
        "WHERE run.id=%s",
        (research_run,),
    ).fetchone()
    assert restored == ("research", packet_id, "f" * 64)
    with pytest.raises(psycopg.Error, match="research intelligence cannot publish"):
        db.execute(
            "INSERT INTO market_reports(id,idempotency_key,run_id,packet_id,market_date,kind,report,report_hash,rendered_text,rendered_hash) "
            "VALUES(%s,%s,%s,%s,'2026-11-24','on-demand','{}',%s,'research',%s)",
            (str(uuid.uuid4()), "1" * 64, research_run, packet_id, "2" * 64, "3" * 64),
        )
