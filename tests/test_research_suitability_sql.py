import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest
from pglast import parse_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261011_research_suitability_packet_contract.sql"
SCHEMA = ROOT / "sql/schema.sql"
PROTECTED = {
    "sql/reconciliation/20261004_production_schema_reconciliation.sql": "db8486083b6c36a7d574a6135e432f01fa0d1602a3ca560b57743949c6fedc87",
    "sql/migrations/20261005_market_wide_discovery.sql": "708df0bf998e025158294c1902d147cead6cc1e08dd3e246aa9dc8f5465dafea",
    "sql/migrations/20261006_reference_snapshot_transfer.sql": "97548a0dbd92a19b7a8e8cac60fe5024a93ee93eaa5257c71932274e3cd25f33",
    "sql/migrations/20261007_discovery_cursor_context.sql": "789896fe6eef69de41eca22339c98e1f46f6f717ca086878c79910572f89a8a6",
    "sql/migrations/20261008_official_source_completion_contract.sql": "4e63c3aea0ba21b1e646f550fb712a91cd4dd98a0c4618a3657cb7171263d8f8",
    "sql/migrations/20261009_reference_issuer_names.sql": "a6f20e7b50f6ecf091e0d5ef73c2772587caebc30135e3add8adefecab2d6094",
    "sql/migrations/20261010_bounded_adaptive_enrichment.sql": "a3a195b99b2059125bd595f2ca2f1a382fb2c84b8a8a212add7ff8144c5a5d65",
}
HASH_VECTORS = json.loads(
    (ROOT / "tests/fixtures/research_suitability_hash_vectors.json").read_text()
)


def test_task8_migration_is_additive_parseable_and_appended_exactly():
    assert len(parse_sql(MIGRATION.read_text())) == 18
    assert SCHEMA.read_bytes().endswith(MIGRATION.read_bytes())
    for relative, expected in PROTECTED.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_v2_guard_v1_read_and_actual_writer_privileges_in_disposable_postgres():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local binaries and a non-root user")
    with tempfile.TemporaryDirectory(prefix="research-suitability-sql-") as directory:
        root = Path(directory)
        subprocess.run([
            binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale",
        ], check=True, capture_output=True)
        subprocess.run([
            binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
            "-o", f"-k {root} -h '' -p 55441", "-w", "start",
        ], check=True, capture_output=True)
        try:
            dsn = f"host={root} port=55441 dbname=postgres"
            with psycopg.connect(dsn, autocommit=True) as db:
                db.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
                db.execute("CREATE ROLE stock_agent_dashboard; CREATE ROLE stock_agent_release_reader; CREATE ROLE stock_agent_release_reader_runtime")
                db.execute(SCHEMA.read_text())
                for vector in HASH_VECTORS["vectors"]:
                    assert db.execute(
                        "SELECT market_canonical_jsonb(%s::jsonb),"
                        "encode(extensions.digest(convert_to(market_canonical_jsonb(%s::jsonb),'UTF8'),'sha256'),'hex')",
                        (Jsonb(vector["value"]), Jsonb(vector["value"])),
                    ).fetchone() == (vector["canonical_json"], vector["sha256"])
                db.execute("INSERT INTO market_policy_config(version,config,active) VALUES(1,'{}',true)")
                run_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'on-demand',CURRENT_DATE,1,'{}')",
                    (run_id,),
                )
                db.execute(
                    "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
                    (str(uuid.uuid4()), run_id),
                )
                v1 = {"candidates": [], "evidence": [], "coverage": {}, "limitations": [], "policy_version": 1}
                v1_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) VALUES(%s,%s,1,'completed',0,0,%s,%s)",
                    (v1_id, run_id, Jsonb(v1), hashlib.sha256(json.dumps(v1, separators=(",", ":"), sort_keys=True).encode()).hexdigest()),
                )
                assert db.execute(
                    "SELECT read_market_evidence_packet(%s,%s)->'packet'=%s::jsonb",
                    (v1_id, run_id, Jsonb(v1)),
                ).fetchone() == (True,)

                run2 = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'on-demand',CURRENT_DATE,1,'{}')",
                    (run2,),
                )
                v2 = {
                    "action_candidates": [], "contract_version": 2, "coverage": {}, "evidence": [],
                    "execution_allowed": False, "limitations": [],
                    "observed_at": "2026-09-07T12:00:00.000Z", "omissions": [],
                    "policy_version": 1, "research_candidates": [], "run_id": run2,
                }
                db.execute(
                    "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) VALUES(%s,%s,1,'completed',0,0,%s,%s)",
                    (str(uuid.uuid4()), run2, Jsonb(v2), hashlib.sha256(json.dumps(v2, separators=(",", ":"), sort_keys=True).encode()).hexdigest()),
                )
                forged = {**v2, "execution_allowed": True}
                with pytest.raises(psycopg.Error):
                    db.execute("SELECT validate_market_evidence_packet_v2(%s,1,%s)", (run2, Jsonb(forged)))

                signature = "public.apply_market_decision_bundle_with_cash_snapshot(uuid,uuid,uuid,int,jsonb,jsonb,jsonb,uuid,bigint)"
                internal = "public.apply_market_decision_bundle_with_cash_snapshot_v1_internal(uuid,uuid,uuid,int,jsonb,jsonb,jsonb,uuid,bigint)"
                assert db.execute("SELECT has_function_privilege('service_role',%s,'EXECUTE')", (signature,)).fetchone() == (True,)
                assert db.execute("SELECT has_function_privilege('anon',%s,'EXECUTE')", (signature,)).fetchone() == (False,)
                assert db.execute("SELECT has_function_privilege('service_role',%s,'EXECUTE')", (internal,)).fetchone() == (False,)
                for table in (
                    "market_source_receipts", "market_source_items",
                    "market_intelligence_run_items", "market_source_item_provenance",
                    "market_run_source_item_provenance", "market_events",
                    "market_candidate_rankings",
                ):
                    assert db.execute(
                        "SELECT has_table_privilege('stock_agent_release_reader_runtime',%s,'SELECT')",
                        (f"public.{table}",),
                    ).fetchone() == (True,)
                    assert db.execute(
                        "SELECT has_table_privilege('stock_agent_release_reader_runtime',%s,'INSERT,UPDATE,DELETE')",
                        (f"public.{table}",),
                    ).fetchone() == (False,)
                evaluations = [{
                    "policy_status": "approved", "final_action": "buy",
                    "analyst": {"packet_id": str(uuid.uuid4())},
                    "normalized": {"ticker": "TEST"},
                }]
                with pytest.raises(psycopg.Error, match="ACTION_LANE_REQUIRED"):
                    db.execute(
                        "SELECT apply_market_decision_bundle_with_cash_snapshot("
                        "%s,%s,%s,1,%s,'[]','{}',NULL,NULL)",
                        (str(uuid.uuid4()), run2, str(uuid.uuid4()), Jsonb(evaluations)),
                    )
        finally:
            subprocess.run([
                binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop",
            ], check=True, capture_output=True)
