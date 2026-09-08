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
    assert SCHEMA.read_bytes().count(MIGRATION.read_bytes()) == 1
    for relative, expected in PROTECTED.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    migration = MIGRATION.read_text()
    assert "jsonb_array_length(p_packet->'action_candidates')<>0" in migration
    assert "protected issuer valuation unavailable" in migration
    assert "'valuation_status','unavailable'" in migration


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
                reservation_id = str(uuid.uuid4())
                receipt_id = str(uuid.uuid4())
                item_id = str(uuid.uuid4())
                run_item_id = str(uuid.uuid4())
                event_id = str(uuid.uuid4())
                ranking_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO market_source_quota_reservations(id,run_id,provider,market_date,phase,reserved_requests,cache_keys) VALUES(%s,%s,'gdelt',CURRENT_DATE,'on-demand',1,'[]')",
                    (reservation_id, run2),
                )
                db.execute(
                    "INSERT INTO market_source_receipts(id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,request_cost,returned_count,accepted_count,duplicate_count,dropped_count,response_hash) VALUES(%s,%s,%s,'gdelt','succeeded','forgery-probe','{}','2026-09-07T11:59:00Z','2026-09-08T11:59:00Z',1,1,1,0,0,%s)",
                    (receipt_id, run2, reservation_id, "a" * 64),
                )
                db.execute(
                    "INSERT INTO market_source_items(id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,title,normalized_text,canonical_content,content_hash,metadata) VALUES(%s,%s,'gdelt','event-1','https://example.test/event','2026-09-07T11:58:00Z','Event','Source-backed event','Source-backed event',%s,%s)",
                    (item_id, receipt_id, "b" * 64, Jsonb({"authority": "reported"})),
                )
                db.execute(
                    "INSERT INTO market_intelligence_run_items(id,run_id,source_item_id,source_receipt_id,disposition) VALUES(%s,%s,%s,%s,'accepted')",
                    (run_item_id, run2, item_id, receipt_id),
                )
                db.execute(
                    "INSERT INTO market_source_item_provenance(source_item_id,provider,canonical_item_url,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status) VALUES(%s,'gdelt','https://example.test/event','https://example.test/request','2026-09-07T11:59:00Z',NULL,'[]','[]','qualified')",
                    (item_id,),
                )
                db.execute(
                    "INSERT INTO market_run_source_item_provenance(run_item_id,run_id,source_item_id,source_receipt_id,provider,request_url,retrieved_at,reporting_at,entity_ids,security_ids,discovery_status) VALUES(%s,%s,%s,%s,'gdelt','https://example.test/request','2026-09-07T11:59:00Z',NULL,'[]','[]','qualified')",
                    (run_item_id, run2, item_id, receipt_id),
                )
                db.execute(
                    "INSERT INTO market_events(id,run_id,event_type,title,summary,materiality,confidence,evidence_item_ids,content_hash) VALUES(%s,%s,'thematic_event','Event','Source-backed event',0.5,0.5,%s,%s)",
                    (event_id, run2, Jsonb([item_id]), "c" * 64),
                )
                db.execute(
                    "INSERT INTO market_candidate_rankings(id,run_id,event_id,candidate_key,ticker,rank,component_scores,total_score,qualified,veto_reasons,exposure_item_ids,content_hash) VALUES(%s,%s,%s,'sec:TEST','TEST',1,'{}',1,false,'[]','[]',%s)",
                    (ranking_id, run2, event_id, "d" * 64),
                )
                suitability_body = {
                    "component_scores": {"concentration_penalty": "0.000000", "duplication_penalty": "0.000000", "liquidity": "0.000000", "portfolio_relevance": "0.000000"},
                    "lineage": None, "missing_reasons": ["valuation_missing"],
                    "state": "unknown", "veto_reasons": [],
                }
                suitability = {**suitability_body, "evaluation_hash": hashlib.sha256(json.dumps(suitability_body, separators=(",", ":"), sort_keys=True).encode()).hexdigest()}
                candidate_body = {
                    "adverse_paths": [], "candidate_key": "sec:TEST", "entity_id": "issuer:TEST",
                    "event_ids": [event_id], "evidence": [{"claim_type": "event", "item_id": item_id, "relationship_eligible": False, "role": "supporting"}],
                    "exposure_fact_ids": [], "limitations": ["valuation_missing"],
                    "priority_components": {"authority_corroboration": "1.000000", "exposure": "0.000000", "materiality": "0.500000", "recency": "1.000000"},
                    "priority_score": "2.500000", "research_state": "resolved", "roles": [],
                    "security_id": "sec:TEST", "suitability": suitability, "theme_ids": ["test"], "ticker": "TEST",
                }
                candidate = {**candidate_body, "candidate_hash": hashlib.sha256(json.dumps(candidate_body, separators=(",", ":"), sort_keys=True).encode()).hexdigest()}
                v2 = {
                    "action_candidates": [], "contract_version": 2, "coverage": {}, "evidence": [{
                        "authority": "reported", "canonical_url": "https://example.test/event", "claim_type": "event",
                        "content_hash": "b" * 64, "effective_at": None, "item_id": item_id,
                        "normalized_text": "Source-backed event", "published_at": "2026-09-07T11:58:00.000Z",
                        "reporting_at": None, "retrieved_at": "2026-09-07T11:59:00.000Z",
                        "source_identity": {"provider": "gdelt", "receipt_id": receipt_id, "upstream_item_id": "event-1"},
                    }],
                    "execution_allowed": False, "limitations": [],
                    "observed_at": "2026-09-07T12:00:00.000Z", "omissions": [],
                    "policy_version": 1, "research_candidates": [candidate], "run_id": run2,
                }
                db.execute(
                    "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) VALUES(%s,%s,1,'completed',1,1,%s,%s)",
                    (str(uuid.uuid4()), run2, Jsonb(v2), hashlib.sha256(json.dumps(v2, separators=(",", ":"), sort_keys=True).encode()).hexdigest()),
                )
                forged = {**v2, "execution_allowed": True}
                with pytest.raises(psycopg.Error):
                    db.execute("SELECT validate_market_evidence_packet_v2(%s,1,%s)", (run2, Jsonb(forged)))

                promoted = json.loads(json.dumps(v2))
                promoted_candidate = promoted["research_candidates"][0]
                promoted_candidate["evidence"][0].update(
                    claim_type="issuer_exposure", relationship_eligible=True, role="supporting",
                )
                promoted_candidate["roles"] = ["supplier"]
                promoted_candidate["suitability"]["component_scores"].update(
                    liquidity="1.000000", portfolio_relevance="1.000000",
                )
                promoted_candidate["suitability"].update(
                    state="eligible", missing_reasons=[], veto_reasons=[],
                )
                promoted_suitability = {key: value for key, value in promoted_candidate["suitability"].items() if key != "evaluation_hash"}
                promoted_candidate["suitability"]["evaluation_hash"] = hashlib.sha256(json.dumps(promoted_suitability, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
                promoted_body = {key: value for key, value in promoted_candidate.items() if key != "candidate_hash"}
                promoted_candidate["candidate_hash"] = hashlib.sha256(json.dumps(promoted_body, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
                promoted["action_candidates"] = [{
                    "candidate_hash": promoted_candidate["candidate_hash"], "candidate_key": "sec:TEST",
                    "suitability_hash": promoted_candidate["suitability"]["evaluation_hash"],
                }]
                with pytest.raises(psycopg.Error, match="invalid evidence packet v2 envelope"):
                    db.execute("SELECT validate_market_evidence_packet_v2(%s,1,%s)", (run2, Jsonb(promoted)))

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
