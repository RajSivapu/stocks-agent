from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
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
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pglast import parse_sql
import pytest

from lib.intelligence.themes import (
    revise_theme_episode,
    theme_episode_v2_anchor_document,
    theme_episode_v2_episode_id,
    theme_episode_v2_persistence_document,
    theme_episode_v2_revision_id,
)
from scripts.export_recovery_bundle import _validate_theme_memory_v2_lineage
from scripts.protected_evidence import RECOVERY_SQL
from scripts.verify_owner_dashboard_deployment import (
    DASHBOARD_REPORT_SOURCE_SQL,
    EVIDENCE_REPORT_SOURCE_SQL,
)
from lib.config import load_settings
from lib.policy_config import build_policy_config


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261012_theme_memory_research_nominations.sql"
RUNTIME_COMPLETION_MIGRATION = (
    ROOT / "sql/migrations/20261013_v2_runtime_completion.sql"
)
HONEST_EMPTY_REPORT_MIGRATION = (
    ROOT / "sql/migrations/20261014_honest_empty_report_persistence.sql"
)
RELEASE_READER_MIGRATION = (
    ROOT / "sql/migrations/20261015_release_reader_source_tables.sql"
)
DASHBOARD_AUTHORITY_MIGRATION = (
    ROOT / "sql/migrations/20261016_dashboard_runtime_authority_closure.sql"
)
RELEASE_READER_AUTHORITY_MIGRATION = (
    ROOT / "sql/migrations/20261017_release_reader_extension_closure.sql"
)
RUN_ORDER_MIGRATION = (
    ROOT / "sql/migrations/20261018_analysis_context_binding_lifecycle.sql"
)
ACTIVE_INTELLIGENCE_POLICY_MIGRATION = (
    ROOT / "sql/migrations/20261019_active_intelligence_policy.sql"
)
REFERENCE_TRANSFER_RESTART_MIGRATION = (
    ROOT / "sql/migrations/20261020_reference_transfer_restart.sql"
)
RETRY_TASK_CAPACITY_RECOVERY_MIGRATION = (
    ROOT / "sql/migrations/20261021_retry_task_capacity_recovery.sql"
)
SCHEMA = ROOT / "sql/schema.sql"


TABLES = (
    "market_theme_episode_revisions_v2",
    "market_reviewer_identity_receipts_v2",
    "market_research_nomination_requests_v2",
    "market_research_nominations_v2",
    "market_research_nomination_lifecycle_v2",
    "market_intelligence_memory_context_bindings_v2",
)


def test_task9_migration_is_additive_parseable_and_appended_verbatim():
    statements = parse_sql(MIGRATION.read_text())
    assert statements
    assert MIGRATION.read_bytes() in SCHEMA.read_bytes()
    migration = MIGRATION.read_text()
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in migration
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in migration
    assert "20261005_market_wide_discovery" not in migration


def test_v2_runtime_completion_and_honest_empty_tail_are_parseable_and_ordered():
    statements = parse_sql(RUNTIME_COMPLETION_MIGRATION.read_text())
    assert statements
    honest_empty = parse_sql(HONEST_EMPTY_REPORT_MIGRATION.read_text())
    assert honest_empty
    release_reader = parse_sql(RELEASE_READER_MIGRATION.read_text())
    assert release_reader
    dashboard_authority = parse_sql(DASHBOARD_AUTHORITY_MIGRATION.read_text())
    assert dashboard_authority
    release_reader_authority = parse_sql(
        RELEASE_READER_AUTHORITY_MIGRATION.read_text()
    )
    assert release_reader_authority
    run_order = parse_sql(RUN_ORDER_MIGRATION.read_text())
    assert run_order
    schema = SCHEMA.read_bytes()
    assert RUNTIME_COMPLETION_MIGRATION.read_bytes() in schema
    assert HONEST_EMPTY_REPORT_MIGRATION.read_bytes() in schema
    assert RELEASE_READER_MIGRATION.read_bytes() in schema
    assert DASHBOARD_AUTHORITY_MIGRATION.read_bytes() in schema
    assert RELEASE_READER_AUTHORITY_MIGRATION.read_bytes() in schema
    active_policy = parse_sql(ACTIVE_INTELLIGENCE_POLICY_MIGRATION.read_text())
    assert active_policy
    reference_restart = parse_sql(REFERENCE_TRANSFER_RESTART_MIGRATION.read_text())
    assert reference_restart
    retry_capacity = parse_sql(RETRY_TASK_CAPACITY_RECOVERY_MIGRATION.read_text())
    assert retry_capacity
    assert ACTIVE_INTELLIGENCE_POLICY_MIGRATION.read_bytes() in schema
    assert REFERENCE_TRANSFER_RESTART_MIGRATION.read_bytes() in schema
    assert schema.endswith(RETRY_TASK_CAPACITY_RECOVERY_MIGRATION.read_bytes())
    migration = RUNTIME_COMPLETION_MIGRATION.read_text()
    assert "SECURITY DEFINER SET search_path=pg_catalog" in migration
    assert "record_market_intelligence_v2_completion" in migration
    assert "record_market_intelligence_v4_internal" in migration
    assert "GRANT EXECUTE ON FUNCTION public.record_market_intelligence(UUID,UUID,JSONB)" in migration


def test_v2_episode_ledger_has_cross_run_identity_and_no_global_row_collision():
    migration = MIGRATION.read_text()
    assert "UNIQUE (episode_id, revision)" in migration
    assert "UNIQUE (predecessor_revision_id)" in migration
    assert "theme_id TEXT NOT NULL" in migration
    assert "theme_id ~ '^[a-z][a-z0-9_]{2,79}$'" in migration
    assert "theme_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'" in migration
    assert "origin_run_id UUID NOT NULL" in migration
    assert "identity_version INT NOT NULL DEFAULT 2 CHECK (identity_version=2)" in migration
    assert "expires_at<=first_seen+INTERVAL '30 days'" in migration
    assert "execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_allowed)" in migration


def test_nomination_rpc_is_exact_service_only_atomic_and_replay_safe():
    migration = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.record_research_nominations(" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "accepted nomination limit exceeded" in migration
    assert "research nomination request replay mismatch" in migration
    assert "candidate evidence relationship mismatch" in migration
    assert "GRANT EXECUTE ON FUNCTION public.record_research_nominations(UUID,UUID,JSONB) TO service_role" in migration
    assert "TO authenticated" not in migration.split(
        "GRANT EXECUTE ON FUNCTION public.record_research_nominations", 1
    )[1].split(";", 1)[0]


def test_memory_context_is_frozen_bounded_and_preserves_priority_surfaces():
    migration = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.read_theme_memory_context(" in migration
    assert "octet_length(context::text)<=65536" in migration
    for key, cap in (
        ("active_theme_heads", 25),
        ("due_nominations", 12),
        ("urgent_events", 10),
        ("high_materiality_themes", 10),
        ("radar", 20),
        ("source_cursors", 100),
    ):
        assert f"'{key}'" in migration
        assert f"LIMIT {cap}" in migration
    assert "ON CONFLICT (run_id) DO NOTHING" in migration
    assert "memory context hash mismatch" in migration


def test_memory_context_freezes_after_analysis_start_before_intelligence_start(
    theme_memory_dsn,
):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        market_date = date(2099, 10, 18)
        now = db.execute("SELECT statement_timestamp()").fetchone()[0]
        db.execute(
            "INSERT INTO analysis_runs(id,kind,status,scheduled_market_date,scheduled_phase) "
            "VALUES(%s,'post-market','running',%s,'post-market')",
            (run_id, market_date),
        )

        db.execute("SET ROLE service_role")
        first = db.execute(
            "SELECT public.refresh_market_intelligence_context(%s)",
            (run_id,),
        ).fetchone()[0]
        db.execute("RESET ROLE")

        assert first["theme_memory"]["research_only"] is True
        assert first["theme_memory"]["execution_allowed"] is False
        frozen = db.execute(
            "SELECT context,snapshot_hash FROM market_intelligence_memory_context_bindings_v2 "
            "WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        assert frozen is not None

        intelligence = json.loads((ROOT / "config/settings.json").read_text())[
            "intelligence"
        ]
        db.execute(
            "INSERT INTO market_policy_config(version,config) VALUES(91018,%s)",
            (Jsonb({"intelligence": intelligence}),),
        )
        window = {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": now.isoformat(),
            "timezone": "America/Chicago",
            "market_date": market_date.isoformat(),
            "phase": "post-market",
        }
        plan = {
            "reservations": [{
                "id": reservation_id,
                "provider": "gdelt",
                "requests": 1,
                "cache_keys": [],
            }]
        }
        db.execute(
            "SELECT public.start_market_intelligence_run(%s,'post-market',%s,91018,%s,%s)",
            (run_id, market_date, Jsonb(plan), Jsonb(window)),
        )
        db.execute("SET ROLE service_role")
        second = db.execute(
            "SELECT public.refresh_market_intelligence_context(%s)",
            (run_id,),
        ).fetchone()[0]
        db.execute("RESET ROLE")

        assert second["theme_memory_snapshot_hash"] == first[
            "theme_memory_snapshot_hash"
        ]
        assert db.execute(
            "SELECT context,snapshot_hash FROM market_intelligence_memory_context_bindings_v2 "
            "WHERE run_id=%s",
            (run_id,),
        ).fetchone() == frozen


def test_v2_tables_are_append_only_rls_protected_and_dashboard_is_bounded():
    migration = MIGRATION.read_text()
    for table in TABLES:
        assert f"ON public.{table}" in migration
        assert f"REVOKE ALL ON public.{table}" in migration
    assert "CREATE OR REPLACE FUNCTION public.read_owner_intelligence_v2(" in migration
    assert "'intelligence_version',2" in migration
    assert "'research_only',true" in migration
    assert "'execution_disabled',true" in migration
    assert "'valuation_unavailable',true" in migration
    assert "octet_length(v_result::text)>98304" in migration
    assert "GRANT EXECUTE ON FUNCTION public.read_owner_intelligence_v2(INT) TO stock_agent_dashboard" in migration


@pytest.fixture(scope="module")
def theme_memory_dsn():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local binaries and a non-root user")
    with tempfile.TemporaryDirectory(prefix="theme-memory-sql-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        subprocess.run(
            [binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "postgres.log"),
             "-o", f"-k {root} -h '' -p {port}", "-w", "start"],
            check=True,
            capture_output=True,
        )
        dsn = f"host={root} port={port} dbname=postgres"
        try:
            with psycopg.connect(dsn, autocommit=True) as db:
                db.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
                db.execute("CREATE ROLE stock_agent_dashboard; CREATE ROLE stock_agent_release_reader; CREATE ROLE stock_agent_release_reader_runtime")
                db.execute(
                    "CREATE SCHEMA supabase_migrations; "
                    "CREATE TABLE supabase_migrations.schema_migrations("
                    "version text PRIMARY KEY,statements text[])"
                )
                db.execute(SCHEMA.read_text())
            yield dsn
        finally:
            subprocess.run(
                [binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"],
                check=True,
                capture_output=True,
            )


def test_active_v3_is_promoted_to_exact_v4_and_missing_intelligence_fails_closed(
    theme_memory_dsn,
):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        reviewed_v4 = build_policy_config(load_settings(), today=date(2026, 9, 8))
        historical = {}
        for version in (1, 2, 3):
            config = json.loads(json.dumps(reviewed_v4))
            config["version"] = version
            config.pop("intelligence")
            historical[version] = config
            db.execute(
                "INSERT INTO market_policy_config(version,config,active,activated_at) "
                "VALUES(%s,%s,%s,CASE WHEN %s THEN statement_timestamp() END)",
                (version, Jsonb(config), version == 3, version == 3),
            )

        db.execute(ACTIVE_INTELLIGENCE_POLICY_MIGRATION.read_text())
        rows = db.execute(
            "SELECT version,config,active FROM market_policy_config "
            "WHERE version BETWEEN 1 AND 4 ORDER BY version"
        ).fetchall()
        assert [row[0] for row in rows] == [1, 2, 3, 4]
        assert {row[0]: row[1] for row in rows[:3]} == historical
        assert [row[0] for row in rows if row[2]] == [4]
        assert rows[3][1] == reviewed_v4

        before_retry = db.execute(
            "SELECT version,config,active,created_at,activated_at "
            "FROM market_policy_config WHERE version BETWEEN 1 AND 4 ORDER BY version"
        ).fetchall()
        db.execute(ACTIVE_INTELLIGENCE_POLICY_MIGRATION.read_text())
        assert db.execute(
            "SELECT version,config,active,created_at,activated_at "
            "FROM market_policy_config WHERE version BETWEEN 1 AND 4 ORDER BY version"
        ).fetchall() == before_retry

        market_date = date(2099, 10, 19)
        good_run = str(uuid.uuid4())
        db.execute(
            "INSERT INTO analysis_runs(id,kind,status,scheduled_market_date,scheduled_phase) "
            "VALUES(%s,'post-market','running',%s,'post-market')",
            (good_run, market_date),
        )
        plan = {"reservations": [{
            "id": str(uuid.uuid4()),
            "provider": "gdelt",
            "requests": 1,
            "cache_keys": [],
        }]}
        receipt = db.execute(
            "SELECT public.start_market_intelligence_run(%s,'post-market',%s,4,%s)",
            (good_run, market_date, Jsonb(plan)),
        ).fetchone()[0]
        assert receipt["run_id"] == good_run

        old_plan = json.loads(json.dumps(plan))
        old_plan["reservations"][0]["id"] = str(uuid.uuid4())
        with pytest.raises(psycopg.Error, match="intelligence policy unavailable") as error:
            db.execute(
                "SELECT public.start_market_intelligence_run(%s,'post-market',%s,3,%s)",
                (good_run, market_date, Jsonb(old_plan)),
            )
        assert error.value.sqlstate == "22023"


def _seed_protected_nomination_packet(db):
    run_id, packet_id, manifest_id, reviewer_id, evidence_id = [str(uuid.uuid4()) for _ in range(5)]
    candidate = {
        "candidate_key": "ACME",
        "ticker": "ACME",
        "theme_ids": ["theme_one", "theme_two", "theme_three", "theme_four"],
        "entity_id": "CIK:0000000001",
        "security_id": "NASDAQ:ACME",
        "roles": ["program_to_supplier"],
        "evidence": [{"item_id": evidence_id, "role": "supporting", "relationship_eligible": True}],
        "suitability": {"missing_reasons": ["Current issuer valuation is unavailable"]},
    }
    packet = {
        "contract_version": 2,
        "execution_allowed": False,
        "research_candidates": [candidate],
        "evidence": [{
            "item_id": evidence_id,
            "source_identity": {"provider": "fixture"},
            "canonical_url": "javascript:alert(1)",
            "normalized_text": "<img src=x onerror=alert(1)> remains inert evidence text.",
            "retrieved_at": "2026-09-08T12:00:00Z",
        }],
        "coverage": {"source_scope": "fixture"},
    }
    packet_hash = db.execute(
        "SELECT encode(extensions.digest(convert_to(market_canonical_jsonb(%s),'UTF8'),'sha256'),'hex')",
        (Jsonb(packet),),
    ).fetchone()[0]
    db.execute("SET session_replication_role=replica")
    db.execute("INSERT INTO market_policy_config(version,config,active) VALUES(90210,'{}',false) ON CONFLICT DO NOTHING")
    db.execute(
        "INSERT INTO analysis_runs(id,kind,status,finished_at) VALUES(%s,'market-intelligence','completed',statement_timestamp())",
        (run_id,),
    )
    db.execute("INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'on-demand',CURRENT_DATE,90210,'{}')", (run_id,))
    db.execute("INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')", (str(uuid.uuid4()), run_id))
    db.execute(
        "INSERT INTO market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,manifest,content_hash) VALUES(%s,%s,%s,1,1,1,%s,statement_timestamp(),'{}',%s)",
        (manifest_id, run_id, f"fixture:{manifest_id}", "a" * 64, "b" * 64),
    )
    db.execute(
        "INSERT INTO market_reference_finalization_seals(manifest_id,run_id,capability_id,chunk_count,security_count,root_hash) VALUES(%s,%s,'sec_company_tickers_universe',1,1,%s)",
        (manifest_id, run_id, "d" * 64),
    )
    db.execute(
        "INSERT INTO market_reference_run_bindings(run_id,capability_id,manifest_id,reference_status,reference_as_of,source_retrieved_at,reference_age_seconds,request_payload) VALUES(%s,'sec_company_tickers_universe',%s,'healthy',statement_timestamp(),statement_timestamp(),0,'{}')",
        (run_id, manifest_id),
    )
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) VALUES(%s,%s,90210,'completed',1,1,%s,%s)",
        (packet_id, run_id, Jsonb(packet), packet_hash),
    )
    db.execute(
        "INSERT INTO market_reviewer_identity_receipts_v2(receipt_id,run_id,packet_id,packet_hash,reference_manifest_id,actor_identity,reviewed_role,review_hash) VALUES(%s,%s,%s,%s,%s,'analyst-fixture','analyst',%s)",
        (reviewer_id, run_id, packet_id, packet_hash, manifest_id, "c" * 64),
    )
    db.execute("SET session_replication_role=origin")
    return run_id, reviewer_id, evidence_id


def _nomination(theme_id, evidence_id):
    return {
        "theme_id": theme_id,
        "entity_id": "CIK:0000000001",
        "security_id": "NASDAQ:ACME",
        "role": "program_to_supplier",
        "reason": "Confirm the current primary-source relationship.",
        "evidence_ids": [evidence_id],
        "required_evidence_kind": "primary_exposure",
        "priority": 3,
    }


def test_actual_postgres_reviewer_identity_binds_current_packet_role_and_distinct_actor(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id, analyst_id, _evidence_id = _seed_protected_nomination_packet(db)
        checker_id = str(uuid.uuid4())
        db.execute("SET ROLE service_role")
        receipt = db.execute(
            "SELECT record_research_review_identity_v2(%s,%s,%s)",
            (run_id, checker_id, Jsonb({
                "actor_identity": "checker-fixture", "reviewed_role": "checker",
                "predecessor_receipt_id": analyst_id,
            })),
        ).fetchone()[0]
        replay = db.execute(
            "SELECT record_research_review_identity_v2(%s,%s,%s)",
            (run_id, checker_id, Jsonb({
                "actor_identity": "checker-fixture", "reviewed_role": "checker",
                "predecessor_receipt_id": analyst_id,
            })),
        ).fetchone()[0]
        assert replay == {**receipt, "duplicate": True}
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            db.execute(
                "SELECT record_research_review_identity_v2(%s,%s,%s)",
                (run_id, str(uuid.uuid4()), Jsonb({
                    "actor_identity": "analyst-fixture", "reviewed_role": "checker",
                    "predecessor_receipt_id": analyst_id,
                })),
            )
        db.execute("RESET ROLE")
        packet_id, packet_hash, reference_id, role, predecessor = db.execute(
            "SELECT packet_id,packet_hash,reference_manifest_id,reviewed_role,predecessor_receipt_id FROM market_reviewer_identity_receipts_v2 WHERE receipt_id=%s",
            (checker_id,),
        ).fetchone()
        assert role == "checker" and predecessor == uuid.UUID(analyst_id)
        assert db.execute(
            "SELECT id,packet_hash FROM market_evidence_packets WHERE id=%s AND run_id=%s",
            (packet_id, run_id),
        ).fetchone() == (packet_id, packet_hash)
        assert db.execute(
            "SELECT manifest_id FROM market_reference_run_bindings WHERE run_id=%s AND reference_status='healthy'",
            (run_id,),
        ).fetchone() == (reference_id,)


def test_actual_postgres_nomination_three_plus_one_race_and_exact_replay(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id, reviewer_id, evidence_id = _seed_protected_nomination_packet(db)
    request_three, request_one = str(uuid.uuid4()), str(uuid.uuid4())
    payload_three = {"reviewer_receipt_id": reviewer_id, "nominations": [
        _nomination("theme_one", evidence_id), _nomination("theme_two", evidence_id),
        _nomination("theme_three", evidence_id),
    ]}
    payload_one = {"reviewer_receipt_id": reviewer_id, "nominations": [_nomination("theme_four", evidence_id)]}

    def submit(request_id, payload):
        try:
            with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
                db.execute("SET ROLE service_role")
                return db.execute(
                    "SELECT record_research_nominations(%s,%s,%s)",
                    (run_id, request_id, Jsonb(payload)),
                ).fetchone()[0]
        except psycopg.Error as exc:
            return exc.sqlstate

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda pair: submit(*pair), ((request_three, payload_three), (request_one, payload_one))))
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert "22023" in outcomes
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        accepted = db.execute(
            "SELECT count(*) FROM market_research_nominations_v2 WHERE origin_run_id=%s", (run_id,)
        ).fetchone()[0]
        assert accepted in (1, 3)
        winner_index = 0 if isinstance(outcomes[0], dict) else 1
        winner_request, winner_payload = ((request_three, payload_three), (request_one, payload_one))[winner_index]
        replay = submit(winner_request, winner_payload)
        assert replay == outcomes[winner_index]
        changed = dict(winner_payload)
        changed["nominations"] = [dict(changed["nominations"][0], priority=4)]
        assert submit(winner_request, changed) == "22023"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SET ROLE authenticated; SELECT * FROM market_research_nominations_v2")


def test_actual_postgres_owner_projection_is_bounded_redacted_and_independent_of_action(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id, _reviewer_id, evidence_id = _seed_protected_nomination_packet(db)
        db.execute("SET ROLE stock_agent_dashboard")
        projection = db.execute("SELECT read_owner_intelligence_v2(25)").fetchone()[0]
        encoded = json.dumps(projection, ensure_ascii=False, separators=(",", ":")).encode()
        assert len(encoded) <= 98_304
        assert projection["intelligence_version"] == 2
        assert projection["run_id"] == run_id
        assert projection["coverage"] == {"mode": "bounded", "complete_market_coverage": False}
        assert projection["companies"][0]["outside_watchlist"] is True
        assert projection["companies"][0]["evidence_ids"] == [evidence_id]
        assert projection["evidence"][0]["url"] is None
        assert projection["evidence"][0]["passage"].startswith("<img")
        assert not ({"action", "qualified", "score", "price"} & set(projection))
        db.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SET ROLE authenticated; SELECT read_owner_intelligence_v2(25)")


def test_actual_postgres_nomination_deferral_and_later_frozen_selection_barrier(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        origin_run, reviewer_id, evidence_id = _seed_protected_nomination_packet(db)
        request_id = str(uuid.uuid4())
        db.execute("SET ROLE service_role")
        response = db.execute(
            "SELECT record_research_nominations(%s,%s,%s)",
            (origin_run, request_id, Jsonb({
                "reviewer_receipt_id": reviewer_id,
                "nominations": [_nomination("theme_one", evidence_id)],
            })),
        ).fetchone()[0]
        nomination_id = response["nominations"][0]["nomination_id"]
        deferred = db.execute(
            "SELECT transition_research_nomination_v2(%s,%s,%s)",
            (origin_run, nomination_id, Jsonb({
                "state": "pending", "reason": "Official filing remains unavailable.",
                "selection_descriptor": None,
            })),
        ).fetchone()[0]
        assert deferred["state"] == "pending"
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            db.execute(
                "SELECT transition_research_nomination_v2(%s,%s,%s)",
                (origin_run, nomination_id, Jsonb({
                    "state": "selected", "reason": "Same-run selection is forbidden.",
                    "selection_descriptor": {
                        "request_id": str(uuid.uuid4()), "descriptor_hash": "d" * 64,
                        "uncertain_outcome_barrier": True, "execution_allowed": False,
                    },
                })),
            )
        db.execute("RESET ROLE")

        later_run, task_id, manifest_id, descriptor_id = [str(uuid.uuid4()) for _ in range(4)]
        descriptor_hash = "d" * 64
        db.execute("SET session_replication_role=replica")
        db.execute(
            "INSERT INTO analysis_runs(id,kind,status,scheduled_phase,scheduled_market_date) VALUES(%s,'market-intelligence','running','post-market',CURRENT_DATE)",
            (later_run,),
        )
        db.execute(
            "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'post-market',CURRENT_DATE,90210,'{}')",
            (later_run,),
        )
        db.execute(
            "INSERT INTO market_discovery_stage_tasks(id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result) VALUES(%s,%s,'enrich','sec_issuer_submissions','sec_edgar','issuer_submissions',%s,'[]','{}','planned',0,1,'{}')",
            (task_id, later_run, descriptor_hash),
        )
        db.execute(
            "INSERT INTO market_enrichment_selection_manifests(id,run_id,selection_stage,phase,request_count,provider_reservations,deferred_reasons,manifest,content_hash) VALUES(%s,%s,'initial','post-market',1,'{}','{}','{}',%s)",
            (manifest_id, later_run, "e" * 64),
        )
        db.execute(
            "INSERT INTO market_enrichment_request_descriptors(id,manifest_id,run_id,task_id,provider,capability_id,query_kind,descriptor,content_hash) VALUES(%s,%s,%s,%s,'sec_edgar','sec_issuer_submissions','issuer_submissions','{}',%s)",
            (descriptor_id, manifest_id, later_run, task_id, descriptor_hash),
        )
        db.execute("SET session_replication_role=origin")
        db.execute("SET ROLE service_role")
        selected = db.execute(
            "SELECT transition_research_nomination_v2(%s,%s,%s)",
            (later_run, nomination_id, Jsonb({
                "state": "selected", "reason": "Scheduled bounded primary-source follow-up.",
                "selection_descriptor": {
                    "request_id": descriptor_id, "descriptor_hash": descriptor_hash,
                    "uncertain_outcome_barrier": True, "execution_allowed": False,
                },
            })),
        ).fetchone()[0]
        assert selected["state"] == "selected"
        db.execute("RESET ROLE")
        assert db.execute(
            "SELECT state FROM market_discovery_stage_tasks WHERE id=%s", (task_id,)
        ).fetchone() == ("planned",)
        assert db.execute(
            "SELECT count(*) FROM market_source_receipts WHERE run_id=%s", (later_run,)
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT state,reason,selection_descriptor->>'descriptor_hash' FROM market_research_nomination_lifecycle_v2 WHERE nomination_id=%s ORDER BY created_at,receipt_id",
            (nomination_id,),
        ).fetchall()[-2:] == [
            ("pending", "Official filing remains unavailable.", None),
            ("selected", "Scheduled bounded primary-source follow-up.", descriptor_hash),
        ]


def _seed_episode_run(db):
    run_id, reservation_id, receipt_id = [str(uuid.uuid4()) for _ in range(3)]
    evidence_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    db.execute("SET session_replication_role=replica")
    db.execute("INSERT INTO analysis_runs(id,kind,status) VALUES(%s,'market-intelligence','running')", (run_id,))
    db.execute("INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'on-demand',CURRENT_DATE,90210,'{}')", (run_id,))
    db.execute("INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'started')", (str(uuid.uuid4()), run_id))
    db.execute("INSERT INTO market_source_quota_reservations(id,run_id,provider,market_date,phase,reserved_requests,cache_keys) VALUES(%s,%s,'gdelt',CURRENT_DATE,'on-demand',1,'[]')", (reservation_id, run_id))
    db.execute("INSERT INTO market_source_receipts(id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,request_cost,returned_count,accepted_count,duplicate_count,dropped_count,response_hash) VALUES(%s,%s,%s,'gdelt','succeeded','episode-fixture','{}','2026-09-07T12:10:00Z','2026-09-08T12:10:00Z',1,2,2,0,0,%s)", (receipt_id, run_id, reservation_id, "e" * 64))
    for index, evidence_id in enumerate(evidence_ids):
        db.execute("INSERT INTO market_source_items(id,source_receipt_id,provider,upstream_item_id,canonical_url,published_at,title,normalized_text,canonical_content,content_hash,metadata) VALUES(%s,%s,'gdelt',%s,%s,%s,%s,%s,%s,%s,'{}')", (evidence_id, receipt_id, f"story-{index}", f"https://api.gdeltproject.org/api/v2/story-{index}", f"2026-09-07T12:0{index}:00Z", f"Episode source {index}", f"Episode source passage {index}", f"Episode source passage {index}", str(index + 1) * 64))
        db.execute("INSERT INTO market_intelligence_run_items(id,run_id,source_item_id,source_receipt_id,disposition) VALUES(%s,%s,%s,%s,'accepted')", (str(uuid.uuid4()), run_id, evidence_id, receipt_id))
    packet = {
        "contract_version": 2,
        "execution_allowed": False,
        "research_candidates": [],
        "action_candidates": [],
        "evidence": [{"item_id": evidence_id} for evidence_id in evidence_ids],
        "coverage": {"source_scope": "fixture"},
    }
    packet_hash = _episode_hash(db, packet)
    db.execute("INSERT INTO market_policy_config(version,config,active) VALUES(90210,'{}',false) ON CONFLICT DO NOTHING")
    db.execute(
        "INSERT INTO market_evidence_packets(id,run_id,policy_version,status,candidate_count,evidence_count,packet,packet_hash) VALUES(%s,%s,90210,'completed',0,%s,%s,%s)",
        (str(uuid.uuid4()), run_id, len(evidence_ids), Jsonb(packet), packet_hash),
    )
    db.execute("SET session_replication_role=origin")
    return run_id, evidence_ids


def _episode_hash(db, value):
    return db.execute(
        "SELECT encode(extensions.digest(convert_to(market_canonical_jsonb(%s),'UTF8'),'sha256'),'hex')",
        (Jsonb(value),),
    ).fetchone()[0]


def _rehash_episode_row(value, *, anchor=False):
    canonical = lambda document: json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    if anchor:
        value["anchor_hash"] = hashlib.sha256(canonical(
            theme_episode_v2_anchor_document(value),
        ).encode()).hexdigest()
        value["episode_id"] = theme_episode_v2_episode_id(value["anchor_hash"])
    value["content_hash"] = hashlib.sha256(canonical(
        theme_episode_v2_persistence_document(value),
    ).encode()).hexdigest()
    value["revision_id"] = theme_episode_v2_revision_id(
        value["episode_id"], value["revision"], value["content_hash"],
    )
    return value


def _parse_episode_through_gateway(run_id, row):
    source = """
import { canonicalJson } from './supabase/functions/market-briefing-gateway/_shared/intelligence.ts';
import { parseGatewayEnvelope } from './supabase/functions/market-briefing-gateway/_shared/contracts.ts';
const raw = await new Response(Deno.stdin.readable).text();
const value = JSON.parse(raw);
console.log(canonicalJson(parseGatewayEnvelope(value).payload));
"""
    envelope = {
        "schema_version": 1,
        "operation": "record_theme_episode_revision_v2",
        "request_id": str(uuid.uuid4()),
        "run_id": run_id,
        "dry_run": False,
        "payload": row,
    }
    result = subprocess.run(
        ["npx", "--yes", "deno@2.9.6", "eval", source],
        cwd=ROOT, input=json.dumps(envelope), text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_python_gateway_postgres_episode_golden_roundtrip_and_exact_replay(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id, evidence_ids = _seed_episode_run(db)
        event = {
            "theme_id": "critical_minerals_magnets",
            "theme_mechanism": "domestic_magnet_capacity",
            "subject_identity": "entity:niron-magnetics",
            "jurisdiction": "US",
            "effective_period": {"start": "2026-09-01", "end": "2026-12-31"},
            "authoritative_id": "award:doe:MAGNET-2026-17",
            "observed_at": "2026-09-07T12:10:00.000Z",
            "source_evidence": [
                {
                    "evidence_id": evidence_ids[0],
                    "story_identity": "\U00010000-supplementary-story",
                    "polarity": "supporting",
                },
                {
                    "evidence_id": evidence_ids[1],
                    "story_identity": "\ue000-private-use-story",
                    "polarity": "opposing",
                },
            ],
            "investigated_entity_ids": ["entity:niron-magnetics"],
            "missing_questions": ["Which public suppliers have current primary exposure?"],
            "invalidation_conditions": ["Program award is rescinded"],
            "next_review_at": "2026-09-10T12:10:00.000Z",
            "expires_at": "2026-09-27T12:10:00.000Z",
        }
        produced = revise_theme_episode(None, event, origin_run_id=run_id).to_persistence_row()
        parsed = _parse_episode_through_gateway(run_id, produced)
        assert parsed == produced

        db.execute("SET ROLE service_role")
        inserted = db.execute(
            "SELECT record_theme_episode_revision_v2(%s,%s)", (run_id, Jsonb(parsed)),
        ).fetchone()[0]
        replay = db.execute(
            "SELECT record_theme_episode_revision_v2(%s,%s)", (run_id, Jsonb(parsed)),
        ).fetchone()[0]
        db.execute("RESET ROLE")
        assert inserted == {
            "revision_id": produced["revision_id"], "episode_id": produced["episode_id"],
            "revision": 1, "duplicate": False,
        }
        assert replay == {**inserted, "duplicate": True}
        assert db.execute(
            "SELECT origin_run_id::text,anchor_hash,content_hash FROM market_theme_episode_revisions_v2 WHERE revision_id=%s",
            (produced["revision_id"],),
        ).fetchone() == (run_id, produced["anchor_hash"], produced["content_hash"])

        changed_event = {
            **event,
            "missing_questions": ["Which public suppliers have verified primary exposure?"],
        }
        changed = _parse_episode_through_gateway(
            run_id,
            revise_theme_episode(None, changed_event, origin_run_id=run_id).to_persistence_row(),
        )
        db.execute("SET ROLE service_role")
        with pytest.raises(psycopg.Error) as conflict:
            db.execute(
                "SELECT record_theme_episode_revision_v2(%s,%s)",
                (run_id, Jsonb(changed)),
            )
        db.execute("RESET ROLE")
        assert conflict.value.sqlstate == "22023"

        noncanonical_date = dict(produced)
        noncanonical_date["effective_period_start"] = "2026-9-1"
        _rehash_episode_row(noncanonical_date, anchor=True)
        db.execute("SET ROLE service_role")
        with pytest.raises(psycopg.Error) as invalid_date:
            db.execute(
                "SELECT record_theme_episode_revision_v2(%s,%s)",
                (run_id, Jsonb(noncanonical_date)),
            )
        db.execute("RESET ROLE")
        assert invalid_date.value.sqlstate == "22023"

        target_run, _ = _seed_episode_run(db)
        db.execute("SET ROLE service_role")
        with pytest.raises(psycopg.Error) as origin_bypass:
            db.execute(
                "SELECT record_theme_episode_revision_v2(%s,%s)",
                (target_run, Jsonb(parsed)),
            )
        db.execute("RESET ROLE")
        assert origin_bypass.value.sqlstate == "22023"

        db.execute("SET session_replication_role=replica")
        db.execute(
            "UPDATE analysis_runs SET status='completed',finished_at=statement_timestamp() WHERE id=%s",
            (run_id,),
        )
        db.execute(
            "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
            (str(uuid.uuid4()), run_id),
        )
        db.execute("SET session_replication_role=origin")
        db.execute("SET ROLE service_role")
        context = db.execute(
            "SELECT read_theme_memory_context(%s,statement_timestamp())", (target_run,),
        ).fetchone()[0]
        restarted = db.execute(
            "SELECT read_theme_memory_context(%s,statement_timestamp()+INTERVAL '1 hour')",
            (target_run,),
        ).fetchone()[0]
        db.execute("RESET ROLE")
        assert restarted == context
        assert [row["revision_id"] for row in context["active_theme_heads"]] == [
            produced["revision_id"],
        ]

        with db.cursor(row_factory=dict_row) as cursor:
            recovered = cursor.execute(
                RECOVERY_SQL["theme_episode_revisions_v2"] + " WHERE revision_id=%s",
                (produced["revision_id"],),
            ).fetchone()
            packet = cursor.execute(
                RECOVERY_SQL["packets"] + " WHERE run_id=%s", (run_id,),
            ).fetchone()
            source_items = cursor.execute(
                RECOVERY_SQL["source_items"] + " WHERE id=ANY(%s::uuid[])",
                (evidence_ids,),
            ).fetchall()
            source_receipts = cursor.execute(
                RECOVERY_SQL["source_receipts"] + " WHERE run_id=%s", (run_id,),
            ).fetchall()
            run_items = cursor.execute(
                RECOVERY_SQL["intelligence_run_items"] + " WHERE run_id=%s", (run_id,),
            ).fetchall()
        assert recovered["content_hash"] == produced["content_hash"]
        assert recovered["revision_id"] == produced["revision_id"]
        _validate_theme_memory_v2_lineage({
            "intelligence_runs": [{"id": run_id}],
            "packets": [dict(packet)],
            "reference_manifests": [],
            "source_items": [dict(row) for row in source_items],
            "source_receipts": [dict(row) for row in source_receipts],
            "intelligence_run_items": [dict(row) for row in run_items],
            "theme_episode_revisions_v2": [dict(recovered)],
            "reviewer_identity_receipts_v2": [],
            "research_nomination_requests_v2": [],
            "research_nominations_v2": [],
            "research_nomination_lifecycle_v2": [],
            "intelligence_memory_context_bindings_v2": [],
        })


def test_open_ended_episode_roundtrips_python_gateway_postgres_read_and_recovery(
        theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        run_id, evidence_ids = _seed_episode_run(db)
        event = {
            "theme_id": "industrial_infrastructure",
            "theme_mechanism": "grid_capacity_program",
            "subject_identity": "program:open-ended-grid-award",
            "jurisdiction": "US",
            "effective_period": {"start": "2026-09-01", "end": None},
            "authoritative_id": "award:doe:OPEN-2026-1",
            "observed_at": "2026-09-07T12:10:00.000Z",
            "source_evidence": [{
                "evidence_id": evidence_ids[0],
                "story_identity": "open-ended-official-program",
                "polarity": "supporting",
            }],
            "investigated_entity_ids": ["program:open-ended-grid-award"],
            "missing_questions": ["When will the agency publish an end date?"],
            "invalidation_conditions": ["Official program cancellation"],
            "next_review_at": "2026-09-10T12:10:00.000Z",
            "expires_at": "2026-09-27T12:10:00.000Z",
        }
        produced = revise_theme_episode(
            None, event, origin_run_id=run_id,
        ).to_persistence_row()
        assert produced["effective_period_end"] is None
        parsed = _parse_episode_through_gateway(run_id, produced)
        assert parsed == produced

        db.execute("SET ROLE service_role")
        inserted = db.execute(
            "SELECT record_theme_episode_revision_v2(%s,%s)",
            (run_id, Jsonb(parsed)),
        ).fetchone()[0]
        db.execute("RESET ROLE")
        assert inserted["revision_id"] == produced["revision_id"]
        assert db.execute(
            "SELECT effective_period_end,anchor_hash,content_hash "
            "FROM market_theme_episode_revisions_v2 WHERE revision_id=%s",
            (produced["revision_id"],),
        ).fetchone() == (None, produced["anchor_hash"], produced["content_hash"])

        with db.cursor(row_factory=dict_row) as cursor:
            recovered = cursor.execute(
                RECOVERY_SQL["theme_episode_revisions_v2"] + " WHERE revision_id=%s",
                (produced["revision_id"],),
            ).fetchone()
            packet = cursor.execute(
                RECOVERY_SQL["packets"] + " WHERE run_id=%s", (run_id,),
            ).fetchone()
            source_items = cursor.execute(
                RECOVERY_SQL["source_items"] + " WHERE id=%s", (evidence_ids[0],),
            ).fetchall()
            source_receipts = cursor.execute(
                RECOVERY_SQL["source_receipts"] + " WHERE run_id=%s", (run_id,),
            ).fetchall()
            run_items = cursor.execute(
                RECOVERY_SQL["intelligence_run_items"] +
                " WHERE run_id=%s AND source_item_id=%s",
                (run_id, evidence_ids[0]),
            ).fetchall()
        assert recovered["effective_period_end"] is None
        assert recovered["content_hash"] == produced["content_hash"]
        _validate_theme_memory_v2_lineage({
            "intelligence_runs": [{"id": run_id}],
            "packets": [dict(packet)],
            "reference_manifests": [],
            "source_items": [dict(row) for row in source_items],
            "source_receipts": [dict(row) for row in source_receipts],
            "intelligence_run_items": [dict(row) for row in run_items],
            "theme_episode_revisions_v2": [dict(recovered)],
            "reviewer_identity_receipts_v2": [],
            "research_nomination_requests_v2": [],
            "research_nominations_v2": [],
            "research_nomination_lifecycle_v2": [],
            "intelligence_memory_context_bindings_v2": [],
        })


def test_actual_postgres_allows_one_cross_run_successor_for_uuid_theme_identity(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        predecessor_run, predecessor_evidence = _seed_episode_run(db)
        successor_run, successor_evidence = _seed_episode_run(db)
        db.execute("SET session_replication_role=replica")
        db.execute(
            "UPDATE market_source_receipts SET retrieved_at='2026-09-07T12:11:00Z' WHERE run_id=%s",
            (successor_run,),
        )
        db.execute("SET session_replication_role=origin")
        theme_id = str(uuid.uuid4())
        base_event = {
            "theme_id": theme_id, "theme_mechanism": "grid_award_program",
            "subject_identity": "entity:united-states-transmission", "jurisdiction": "US",
            "effective_period": {"start": "2026-09-01", "end": "2026-12-31"},
            "authoritative_id": None, "observed_at": "2026-09-07T12:10:00.000Z",
            "source_evidence": [{
                "evidence_id": predecessor_evidence[0], "story_identity": "story-a",
                "polarity": "supporting",
            }],
            "investigated_entity_ids": ["entity:united-states-transmission"],
            "missing_questions": ["Is funding current?"],
            "invalidation_conditions": ["Program cancellation"],
            "next_review_at": "2026-09-10T12:10:00.000Z",
            "expires_at": "2026-09-27T12:10:00.000Z",
        }
        predecessor = revise_theme_episode(None, base_event, origin_run_id=predecessor_run)
        db.execute("SET ROLE service_role")
        db.execute(
            "SELECT record_theme_episode_revision_v2(%s,%s)",
            (predecessor_run, Jsonb(predecessor.to_persistence_row())),
        )
        db.execute("RESET ROLE")

        competing = []
        for index, evidence_id in enumerate(successor_evidence):
            event = {
                **base_event,
                "observed_at": "2026-09-07T12:11:00.000Z",
                "source_evidence": [{
                    "evidence_id": evidence_id, "story_identity": f"story-{index + 1}",
                    "polarity": "opposing",
                }],
            }
            competing.append(
                revise_theme_episode(predecessor, event, origin_run_id=successor_run).to_persistence_row()
            )

    def successor(value):
        with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
            try:
                db.execute("SET ROLE service_role")
                return db.execute("SELECT record_theme_episode_revision_v2(%s,%s)", (successor_run, Jsonb(value))).fetchone()[0]
            except psycopg.Error as exc:
                return exc.sqlstate

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(successor, competing))
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert "22023" in outcomes
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        assert db.execute("SELECT count(*) FROM market_theme_episode_revisions_v2 WHERE predecessor_revision_id=%s", (predecessor.revision_id,)).fetchone() == (1,)
        assert db.execute("SELECT theme_id FROM market_theme_episode_revisions_v2 WHERE predecessor_revision_id=%s", (predecessor.revision_id,)).fetchone() == (theme_id,)


def test_actual_postgres_v2_acl_separates_browser_service_dashboard_and_release_reader(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        for table in TABLES:
            for role in ("anon", "authenticated", "service_role", "stock_agent_dashboard"):
                assert db.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE')",
                    (role, f"public.{table}"),
                ).fetchone() == (False,)
            assert db.execute(
                "SELECT has_table_privilege('stock_agent_release_reader',%s,'SELECT')",
                (f"public.{table}",),
            ).fetchone() == (True,)
            assert db.execute(
                "SELECT has_table_privilege('stock_agent_release_reader_runtime',%s,'SELECT')",
                (f"public.{table}",),
            ).fetchone() == (True,)
        for role in (
            "anon", "authenticated", "service_role", "stock_agent_dashboard",
            "stock_agent_release_reader", "stock_agent_release_reader_runtime",
        ):
            assert db.execute(
                "SELECT has_function_privilege(%s,'public.market_theme_episode_uuid_v5(uuid,text)','EXECUTE')",
                (role,),
            ).fetchone() == (False,)

        assert db.execute(
            "SELECT has_function_privilege('service_role','public.record_research_nominations(uuid,uuid,jsonb)','EXECUTE')"
        ).fetchone() == (True,)
        assert db.execute(
            "SELECT has_function_privilege('authenticated','public.record_research_nominations(uuid,uuid,jsonb)','EXECUTE')"
        ).fetchone() == (False,)
        assert db.execute(
            "SELECT has_function_privilege('stock_agent_dashboard','public.read_owner_intelligence_v2(integer)','EXECUTE')"
        ).fetchone() == (True,)
        for role in ("anon", "authenticated", "service_role", "stock_agent_release_reader"):
            assert db.execute(
                "SELECT has_function_privilege(%s,'public.read_owner_intelligence_v2(integer)','EXECUTE')",
                (role,),
            ).fetchone() == (False,)

        db.execute("SET ROLE stock_agent_dashboard")
        projection = db.execute("SELECT read_owner_intelligence_v2(25)").fetchone()[0]
        assert set(projection) == {
            "intelligence_version", "run_id", "data_as_of", "themes", "companies",
            "evidence", "source_health", "coverage", "reference", "scope", "backlog",
            "omissions", "boundaries",
        }
        assert projection["boundaries"] == {
            "research_only": True, "execution_disabled": True, "valuation_unavailable": True,
        }


def test_actual_postgres_release_canary_reader_can_read_hashes_but_cannot_mutate(
    theme_memory_dsn,
):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute("SET ROLE stock_agent_dashboard")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SELECT content_hash FROM public.market_events LIMIT 1")
        db.execute("RESET ROLE")

        db.execute("SET ROLE stock_agent_release_reader_runtime")
        assert db.execute(
            "SELECT content_hash FROM public.market_events LIMIT 1"
        ).fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("DELETE FROM public.market_events WHERE false")
        db.execute("RESET ROLE")


def test_actual_postgres_release_canary_report_queries_match_role_grants(
    theme_memory_dsn,
):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute("SET ROLE stock_agent_dashboard")
        assert db.execute(DASHBOARD_REPORT_SOURCE_SQL, ([],)).fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(EVIDENCE_REPORT_SOURCE_SQL).fetchall()
        db.execute("RESET ROLE")

        db.execute("SET ROLE stock_agent_release_reader_runtime")
        assert db.execute(EVIDENCE_REPORT_SOURCE_SQL).fetchall() == []
        db.execute("RESET ROLE")


@pytest.mark.parametrize(
    ("mutation", "restore"),
    (
        (
            "GRANT INSERT ON public.market_events TO stock_agent_release_reader_runtime",
            "REVOKE INSERT ON public.market_events FROM stock_agent_release_reader_runtime",
        ),
        (
            "ALTER ROLE stock_agent_release_reader_runtime BYPASSRLS",
            "ALTER ROLE stock_agent_release_reader_runtime NOBYPASSRLS",
        ),
        (
            "ALTER ROLE stock_agent_release_reader_runtime CREATEDB CREATEROLE REPLICATION",
            "ALTER ROLE stock_agent_release_reader_runtime NOCREATEDB NOCREATEROLE NOREPLICATION",
        ),
        (
            "CREATE POLICY canary_restrictive_drift ON public.market_events AS RESTRICTIVE FOR SELECT TO stock_agent_release_reader_runtime USING (false)",
            "DROP POLICY IF EXISTS canary_restrictive_drift ON public.market_events",
        ),
        (
            "ALTER TABLE public.market_events OWNER TO stock_agent_release_reader_runtime",
            "ALTER TABLE public.market_events OWNER TO CURRENT_USER",
        ),
    ),
)
def test_actual_postgres_post_migration_reader_attestation_rejects_authority_drift(
    theme_memory_dsn, mutation, restore,
):
    from scripts.protected_evidence import PostgresReadOnlySource

    def local_source():
        source = object.__new__(PostgresReadOnlySource)
        source._url = (
            f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        )
        source.project_ref = "local-release-reader"
        source.isolated_guard = False
        source.pre_migration_baseline = False
        source.connection = None
        source._read_tables = ()
        source._pre_migration_omissions = None
        original_query = source.query

        def query(statement, parameters=()):
            rows = original_query(statement, parameters)
            if "current_user AS role" in statement and rows:
                rows[0]["server"] = rows[0].get("server") or "local-socket"
            return rows

        source.query = query
        return source

    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
    try:
        with local_source() as source:
            assert source.identity()["read_only"] is True
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute(mutation)
        with pytest.raises(RuntimeError):
            with local_source():
                pass
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute(restore)
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def test_actual_postgres_post_migration_reader_attestation_rejects_rogue_membership(
    theme_memory_dsn,
):
    from scripts.protected_evidence import PostgresReadOnlySource

    def local_source():
        source = object.__new__(PostgresReadOnlySource)
        source._url = (
            f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        )
        source.project_ref = "local-release-reader"
        source.isolated_guard = False
        source.pre_migration_baseline = False
        source.connection = None
        source._read_tables = ()
        source._pre_migration_omissions = None
        original_query = source.query

        def query(statement, parameters=()):
            rows = original_query(statement, parameters)
            if "current_user AS role" in statement and rows:
                rows[0]["server"] = rows[0].get("server") or "local-socket"
            return rows

        source.query = query
        return source

    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
    try:
        with local_source() as source:
            assert source.identity()["read_only"] is True
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute("CREATE ROLE rogue_release_reader")
            admin.execute("CREATE SCHEMA private_release_data")
            admin.execute("CREATE TABLE private_release_data.secrets(value text)")
            admin.execute("GRANT USAGE ON SCHEMA private_release_data TO rogue_release_reader")
            admin.execute("GRANT SELECT ON private_release_data.secrets TO rogue_release_reader")
            admin.execute("GRANT rogue_release_reader TO stock_agent_release_reader_runtime")
        with pytest.raises(RuntimeError, match="membership|schema|relation"):
            with local_source():
                pass
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute(
                "REVOKE rogue_release_reader FROM stock_agent_release_reader_runtime"
            )
            admin.execute("DROP SCHEMA IF EXISTS private_release_data CASCADE")
            admin.execute("DROP ROLE IF EXISTS rogue_release_reader")
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def _release_reader_authority_snapshot(theme_memory_dsn):
    from scripts.protected_evidence import (
        PostgresReadOnlySource, RELEASE_READER_AUTHORITY_SQL,
    )

    source = object.__new__(PostgresReadOnlySource)
    with psycopg.connect(
        f"{theme_memory_dsn} user=stock_agent_release_reader_runtime",
        row_factory=dict_row,
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source.connection = connection
        return source.query(RELEASE_READER_AUTHORITY_SQL)[0]


def test_actual_postgres_legacy_extension_surface_is_exact_and_unknown_extension_fails(
    theme_memory_dsn,
):
    from scripts.protected_evidence import (
        READ_TABLES, verify_release_reader_authority,
    )

    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
        admin.execute("CREATE EXTENSION pg_stat_statements WITH SCHEMA extensions")
        admin.execute("GRANT USAGE ON SCHEMA extensions TO stock_agent_release_reader")
    try:
        snapshot = _release_reader_authority_snapshot(theme_memory_dsn)
        receipt = verify_release_reader_authority(
            snapshot, READ_TABLES, allow_legacy_extension_authority=True,
        )
        assert receipt["status"] == "verified"
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute("CREATE EXTENSION dblink WITH SCHEMA extensions")
        snapshot = _release_reader_authority_snapshot(theme_memory_dsn)
        with pytest.raises(RuntimeError, match="function authority"):
            verify_release_reader_authority(
                snapshot, READ_TABLES, allow_legacy_extension_authority=True,
            )
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute("DROP EXTENSION IF EXISTS dblink")
            admin.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
            admin.execute("REVOKE USAGE ON SCHEMA extensions FROM stock_agent_release_reader")
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def test_actual_postgres_release_reader_extension_closure_revokes_both_grant_paths(
    theme_memory_dsn,
):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute(
            "GRANT USAGE ON SCHEMA extensions TO "
            "stock_agent_release_reader,stock_agent_release_reader_runtime"
        )
        assert admin.execute(
            "SELECT has_schema_privilege("
            "'stock_agent_release_reader_runtime','extensions','USAGE')"
        ).fetchone() == (True,)

        admin.execute(RELEASE_READER_AUTHORITY_MIGRATION.read_text())

        assert admin.execute(
            "SELECT has_schema_privilege("
            "'stock_agent_release_reader','extensions','USAGE'),"
            "has_schema_privilege("
            "'stock_agent_release_reader_runtime','extensions','USAGE')"
        ).fetchone() == (False, False)


def test_actual_postgres_complete_read_scope_without_closure_ledger_accepts_legacy_extension(
    theme_memory_dsn, monkeypatch,
):
    import scripts.protected_evidence as evidence
    from lib.release_reader_closure_contract import (
        CLOSURE_PRE_MIGRATION_ABSENT_TABLES,
        CLOSURE_PRE_MIGRATION_UNREADABLE_TABLES,
        CLOSURE_READ_TABLES,
        CLOSURE_READER_CONTRACT,
    )

    PostgresReadOnlySource = evidence.PostgresReadOnlySource
    release_reader_authority_closure_manifest = (
        evidence.release_reader_authority_closure_manifest
    )
    monkeypatch.setattr(
        evidence, "READ_TABLES", (*evidence.READ_TABLES, "future_table"),
    )

    def local_source():
        source = object.__new__(PostgresReadOnlySource)
        source._url = f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        source.project_ref = "local-release-reader"
        source.isolated_guard = False
        source.pre_migration_baseline = True
        source.reader_contract = CLOSURE_READER_CONTRACT
        source._contract_read_tables = CLOSURE_READ_TABLES
        source._contract_absent_tables = CLOSURE_PRE_MIGRATION_ABSENT_TABLES
        source._contract_unreadable_tables = (
            CLOSURE_PRE_MIGRATION_UNREADABLE_TABLES
        )
        source.connection = None
        source._read_tables = ()
        source._pre_migration_omissions = None
        original_query = source.query

        def query(statement, parameters=()):
            rows = original_query(statement, parameters)
            if "current_user AS role" in statement and rows:
                rows[0]["server"] = rows[0].get("server") or "local-socket"
            return rows

        source.query = query
        return source

    closure = release_reader_authority_closure_manifest()
    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        assert admin.execute(
            "SELECT count(*) FROM public.stock_agent_release_migration_ledger "
            "WHERE path=%s OR version=%s",
            (closure["path"], closure["version"]),
        ).fetchone() == (0,)
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
        admin.execute("GRANT USAGE ON SCHEMA extensions TO stock_agent_release_reader")
    try:
        with local_source() as source:
            assert source.authority_receipt()["status"] == "verified"
            assert source.pre_migration_scope() == {
                "reason": "candidate read scope is already present",
                "absent_tables": [],
                "unreadable_tables": [],
            }
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute(RELEASE_READER_AUTHORITY_MIGRATION.read_text())
            admin.execute(
                "INSERT INTO public.stock_agent_release_migration_ledger "
                "(path,version,sha256) VALUES(%s,%s,%s)",
                (closure["path"], closure["version"], closure["sha256"]),
            )
        with local_source() as source:
            assert source.authority_receipt()["status"] == "verified"
            assert source.pre_migration_scope()["absent_tables"] == []
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute(
                "DELETE FROM public.stock_agent_release_migration_ledger "
                "WHERE path=%s AND version=%s AND sha256=%s",
                (closure["path"], closure["version"], closure["sha256"]),
            )
            admin.execute("REVOKE USAGE ON SCHEMA extensions FROM stock_agent_release_reader")
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def test_actual_postgres_authority_migration_and_ledger_insert_roll_back_together(
    theme_memory_dsn, tmp_path, monkeypatch,
):
    from scripts.deploy_owner_dashboard_api import (
        apply_release_migrations, candidate_migration_manifest,
    )

    migration = tmp_path / RELEASE_READER_AUTHORITY_MIGRATION.name
    migration.write_bytes(RELEASE_READER_AUTHORITY_MIGRATION.read_bytes())
    manifest = candidate_migration_manifest(tmp_path)
    monkeypatch.setattr(
        "scripts.deploy_owner_dashboard_api.legacy_migration_receipts", lambda: (),
    )

    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("GRANT USAGE ON SCHEMA extensions TO stock_agent_release_reader")
        assert admin.execute(
            "SELECT has_schema_privilege("
            "'stock_agent_release_reader_runtime','extensions','USAGE')"
        ).fetchone() == (True,)

    class FailingLedgerCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, statement, params=None, **kwargs):
            if (
                str(statement).startswith(
                    "INSERT INTO public.stock_agent_release_migration_ledger"
                )
                and params
                and params[0] == manifest[0]["path"]
            ):
                raise RuntimeError("injected ledger insert failure")
            return self.cursor.execute(statement, params, **kwargs)

        def fetchall(self):
            return self.cursor.fetchall()

    try:
        with psycopg.connect(theme_memory_dsn) as connection:
            with pytest.raises(RuntimeError, match="injected ledger insert failure"):
                with connection.transaction(), connection.cursor() as cursor:
                    apply_release_migrations(
                        FailingLedgerCursor(cursor), manifest, tmp_path,
                    )
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            assert admin.execute(
                "SELECT has_schema_privilege("
                "'stock_agent_release_reader_runtime','extensions','USAGE')"
            ).fetchone() == (True,)
            assert admin.execute(
                "SELECT count(*) FROM public.stock_agent_release_migration_ledger "
                "WHERE path=%s",
                (manifest[0]["path"],),
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute("REVOKE USAGE ON SCHEMA extensions FROM stock_agent_release_reader")


def test_actual_closure_lease_blocks_unrelated_recovery_until_finalized(
    theme_memory_dsn,
):
    from scripts.deploy_owner_dashboard_api import DurableMutationLease

    owner = "reader-closure-20261017-" + "a" * 64
    with DurableMutationLease(theme_memory_dsn, owner, "recovery"):
        pass

    with psycopg.connect(theme_memory_dsn, autocommit=True) as connection:
        assert connection.execute(
            "SELECT owner,state FROM public.stock_agent_release_mutation_lease "
            "WHERE singleton"
        ).fetchone() == (owner, "recovery_required")

    with pytest.raises(RuntimeError, match="remains unresolved"):
        with DurableMutationLease(
            theme_memory_dsn, "recovery-999999999-1", "recovery"
        ):
            pass

    with DurableMutationLease(theme_memory_dsn, owner, "recovery") as lease:
        lease.resolve()
    with psycopg.connect(theme_memory_dsn, autocommit=True) as connection:
        assert connection.execute(
            "SELECT owner,state FROM public.stock_agent_release_mutation_lease "
            "WHERE singleton"
        ).fetchone() == (owner, "resolved")


@pytest.mark.parametrize("sql", (
    "INSERT INTO migration_atomicity_probe VALUES (1); "
    "COMMIT/**/AND CHAIN; SELECT 1;",
    "INSERT INTO migration_atomicity_probe VALUES (1); -- hidden\rCOMMIT;",
    "INSERT INTO migration_atomicity_probe VALUES (1); "
    "SELECT 1 AS before$tag$; COMMIT; SELECT 1 AS after$tag$;",
))
def test_actual_postgres_adversarial_transaction_syntax_cannot_escape_rollback(
    theme_memory_dsn, sql,
):
    from scripts.deploy_owner_dashboard_api import migration_execution_statements

    with psycopg.connect(theme_memory_dsn, autocommit=True) as connection:
        connection.execute("CREATE TEMP TABLE migration_atomicity_probe (value INTEGER)")
        with pytest.raises(RuntimeError, match="transaction control"):
            statements = migration_execution_statements(sql)
            with connection.transaction():
                for statement in statements:
                    connection.execute(statement, prepare=True)
                raise RuntimeError("injected post-migration failure")
        assert connection.execute(
            "SELECT count(*) FROM migration_atomicity_probe"
        ).fetchone() == (0,)


def test_actual_postgres_multibyte_migration_statements_execute_prepared(
    theme_memory_dsn,
):
    from scripts.deploy_owner_dashboard_api import migration_execution_statements

    sql = "SELECT 'é' AS café; SELECT '東京' AS city;"
    with psycopg.connect(theme_memory_dsn, autocommit=True) as connection:
        values = [
            connection.execute(statement, prepare=True).fetchone()[0]
            for statement in migration_execution_statements(sql)
        ]
    assert values == ["é", "東京"]


def test_actual_postgres_reader_attestation_rejects_large_object_access(
    theme_memory_dsn,
):
    from scripts.protected_evidence import PostgresReadOnlySource

    def local_source():
        source = object.__new__(PostgresReadOnlySource)
        source._url = f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        source.project_ref = "local-release-reader"
        source.isolated_guard = False
        source.pre_migration_baseline = False
        source.connection = None
        source._read_tables = ()
        source._pre_migration_omissions = None
        original_query = source.query

        def query(statement, parameters=()):
            rows = original_query(statement, parameters)
            if "current_user AS role" in statement and rows:
                rows[0]["server"] = rows[0].get("server") or "local-socket"
            return rows

        source.query = query
        return source

    large_object_oid = None
    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
        large_object_oid = admin.execute("SELECT lo_create(0)").fetchone()[0]
        admin.execute(
            "SELECT lo_put(%s,0,convert_to('private-large-object','UTF8'))",
            (large_object_oid,),
        )
        admin.execute(
            psycopg.sql.SQL("GRANT SELECT ON LARGE OBJECT {} TO stock_agent_release_reader_runtime").format(
                psycopg.sql.SQL(str(large_object_oid))
            )
        )
    try:
        with psycopg.connect(
            f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        ) as reader:
            assert reader.execute(
                "SELECT convert_from(lo_get(%s),'UTF8')", (large_object_oid,)
            ).fetchone()[0] == "private-large-object"
        with pytest.raises(RuntimeError, match="large-object authority"):
            with local_source():
                pass
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            if large_object_oid is not None:
                admin.execute("SELECT lo_unlink(%s)", (large_object_oid,))
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def test_actual_postgres_reader_attestation_rejects_standalone_type_ownership(
    theme_memory_dsn,
):
    from scripts.protected_evidence import PostgresReadOnlySource

    def local_source():
        source = object.__new__(PostgresReadOnlySource)
        source._url = f"{theme_memory_dsn} user=stock_agent_release_reader_runtime"
        source.project_ref = "local-release-reader"
        source.isolated_guard = False
        source.pre_migration_baseline = False
        source.connection = None
        source._read_tables = ()
        source._pre_migration_omissions = None
        original_query = source.query

        def query(statement, parameters=()):
            rows = original_query(statement, parameters)
            if "current_user AS role" in statement and rows:
                rows[0]["server"] = rows[0].get("server") or "local-socket"
            return rows

        source.query = query
        return source

    with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
        admin.execute("ALTER ROLE stock_agent_release_reader_runtime LOGIN")
        admin.execute("CREATE TYPE public.release_reader_owned_enum AS ENUM ('unsafe')")
        admin.execute(
            "ALTER TYPE public.release_reader_owned_enum OWNER TO stock_agent_release_reader_runtime"
        )
    try:
        with pytest.raises(RuntimeError, match="own database objects"):
            with local_source():
                pass
    finally:
        with psycopg.connect(theme_memory_dsn, autocommit=True) as admin:
            admin.execute("DROP TYPE IF EXISTS public.release_reader_owned_enum")
            admin.execute("ALTER ROLE stock_agent_release_reader_runtime NOLOGIN")


def test_actual_postgres_dashboard_authority_verifier_rejects_inherited_insert(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        try:
            receipt = verify_dashboard_role(db)
            assert receipt["write_privileges"] == 0
            db.execute("GRANT INSERT ON public.holdings TO stock_agent_dashboard")
            with pytest.raises(RuntimeError, match="table privilege"):
                verify_dashboard_role(db)
        finally:
            db.execute("REVOKE INSERT ON public.holdings FROM stock_agent_dashboard")
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_dashboard_authority_verifier_rejects_auth_schema_access(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        db.execute("CREATE SCHEMA auth")
        db.execute("CREATE TABLE auth.secret_tokens(token text)")
        db.execute("GRANT USAGE ON SCHEMA auth TO stock_agent_dashboard")
        db.execute("GRANT SELECT, INSERT ON auth.secret_tokens TO stock_agent_dashboard")
        try:
            assert db.execute(
                "SELECT has_schema_privilege('stock_agent_dashboard_runtime','auth','USAGE'), "
                "has_table_privilege('stock_agent_dashboard_runtime','auth.secret_tokens','SELECT,INSERT')"
            ).fetchone() == (True, True)
            with pytest.raises(RuntimeError, match="outside public"):
                verify_dashboard_role(db)
        finally:
            db.execute("REVOKE SELECT, INSERT ON auth.secret_tokens FROM stock_agent_dashboard")
            db.execute("REVOKE USAGE ON SCHEMA auth FROM stock_agent_dashboard")
            db.execute("DROP TABLE auth.secret_tokens")
            db.execute("DROP SCHEMA auth")
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_dashboard_authority_verifier_rejects_foreign_table_access(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        db.execute("CREATE FOREIGN DATA WRAPPER dashboard_test_fdw")
        db.execute("CREATE SERVER dashboard_test_server FOREIGN DATA WRAPPER dashboard_test_fdw")
        db.execute(
            "CREATE FOREIGN TABLE public.remote_secrets(token text) "
            "SERVER dashboard_test_server"
        )
        db.execute("GRANT SELECT, INSERT ON public.remote_secrets TO stock_agent_dashboard")
        try:
            assert db.execute(
                "SELECT has_table_privilege("
                "'stock_agent_dashboard_runtime','public.remote_secrets','SELECT,INSERT')"
            ).fetchone() == (True,)
            with pytest.raises(RuntimeError, match="table privilege"):
                verify_dashboard_role(db)
        finally:
            db.execute("REVOKE SELECT, INSERT ON public.remote_secrets FROM stock_agent_dashboard")
            db.execute("DROP FOREIGN TABLE public.remote_secrets")
            db.execute("DROP SERVER dashboard_test_server")
            db.execute("DROP FOREIGN DATA WRAPPER dashboard_test_fdw")
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_dashboard_authority_verifier_rejects_database_create(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        database = db.info.dbname
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        db.execute(
            psycopg.sql.SQL("GRANT CREATE ON DATABASE {} TO stock_agent_dashboard").format(
                psycopg.sql.Identifier(database)
            )
        )
        try:
            assert db.execute(
                "SELECT has_database_privilege("
                "'stock_agent_dashboard_runtime',current_database(),'CREATE')"
            ).fetchone() == (True,)
            with pytest.raises(RuntimeError, match="database privilege"):
                verify_dashboard_role(db)
        finally:
            db.execute(
                psycopg.sql.SQL("REVOKE CREATE ON DATABASE {} FROM stock_agent_dashboard").format(
                    psycopg.sql.Identifier(database)
                )
            )
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_dashboard_authority_verifier_rejects_second_incoming_member(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("CREATE ROLE rogue_dashboard_reader LOGIN INHERIT")
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        db.execute("GRANT stock_agent_dashboard TO rogue_dashboard_reader")
        try:
            db.execute("SET ROLE rogue_dashboard_reader")
            db.execute("SELECT ticker FROM public.holdings LIMIT 1").fetchall()
            db.execute("RESET ROLE")
            with pytest.raises(RuntimeError, match="privilege role members"):
                verify_dashboard_role(db)
        finally:
            db.execute("RESET ROLE")
            db.execute("REVOKE stock_agent_dashboard FROM rogue_dashboard_reader")
            db.execute("REVOKE stock_agent_dashboard FROM stock_agent_dashboard_runtime")
            db.execute("DROP ROLE rogue_dashboard_reader")
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_dashboard_authority_verifier_rejects_runtime_role_member(theme_memory_dsn):
    from scripts.verify_owner_dashboard_role import verify_dashboard_role

    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        db.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN INHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        db.execute("CREATE ROLE rogue_dashboard_reader LOGIN INHERIT")
        db.execute("GRANT stock_agent_dashboard TO stock_agent_dashboard_runtime")
        db.execute("GRANT stock_agent_dashboard_runtime TO rogue_dashboard_reader")
        try:
            db.execute("SET ROLE rogue_dashboard_reader")
            db.execute("SELECT ticker FROM public.holdings LIMIT 1").fetchall()
            db.execute("RESET ROLE")
            with pytest.raises(RuntimeError, match="runtime role has incoming members"):
                verify_dashboard_role(db)
        finally:
            db.execute("RESET ROLE")
            db.execute("REVOKE stock_agent_dashboard_runtime FROM rogue_dashboard_reader")
            db.execute("REVOKE stock_agent_dashboard FROM stock_agent_dashboard_runtime")
            db.execute("DROP ROLE rogue_dashboard_reader")
            db.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_actual_postgres_freezes_unicode_bounded_memory_without_dropping_priority_surfaces(theme_memory_dsn):
    with psycopg.connect(theme_memory_dsn, autocommit=True) as db:
        source_run, evidence_ids = _seed_episode_run(db)
        target_run = str(uuid.uuid4())
        db.execute("SET session_replication_role=replica")
        db.execute(
            "UPDATE analysis_runs SET status='completed',finished_at='2026-09-07T12:20:00Z' WHERE id=%s",
            (source_run,),
        )
        db.execute(
            "INSERT INTO market_intelligence_run_events(id,run_id,status) VALUES(%s,%s,'completed')",
            (str(uuid.uuid4()), source_run),
        )
        db.execute(
            "INSERT INTO analysis_runs(id,kind,status) VALUES(%s,'market-intelligence','running')",
            (target_run,),
        )
        db.execute(
            "INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan) VALUES(%s,'on-demand','2026-09-08',90210,'{}')",
            (target_run,),
        )
        db.execute(
            "INSERT INTO market_events(id,run_id,event_type,title,summary,materiality,confidence,evidence_item_ids,content_hash,created_at) VALUES(%s,%s,'official_program','Urgent grid award','Current official program evidence',0.99,0.9,%s,%s,'2026-09-07T12:15:00Z')",
            (str(uuid.uuid4()), source_run, Jsonb(evidence_ids[:1]), "a" * 64),
        )
        db.execute(
            "INSERT INTO radar(ticker,added,last_seen,days_relevant,reason,promoted) VALUES('FROZEN','2026-09-07','2026-09-07',3,'Frozen radar row',false) ON CONFLICT(ticker) DO UPDATE SET reason=EXCLUDED.reason,last_seen=EXCLUDED.last_seen"
        )
        for index in range(25):
            db.execute(
                "INSERT INTO market_theme_episode_revisions_v2(revision_id,theme_id,episode_id,revision,anchor_hash,origin_run_id,theme_mechanism,subject_identity,jurisdiction,effective_period_start,source_membership,source_ids,supporting_source_ids,opposing_source_ids,added_source_ids,missing_questions,invalidation_conditions,first_seen,last_seen,next_review_at,expires_at,state,content_hash,created_at) VALUES(%s,%s,%s,1,%s,%s,%s,%s,'US','2026-09-01',%s,%s,%s,%s,%s,%s,%s,'2026-09-07T12:00:00Z','2026-09-07T12:10:00Z','2026-09-07T12:30:00Z','2026-09-27T12:00:00Z','open',%s,'2026-09-07T12:10:00Z')",
                (
                    str(uuid.uuid4()), f"theme_{index:02d}", str(uuid.uuid4()), f"{index + 100:064x}",
                    source_run, f"Grid mechanism {index}", f"US grid subject {index}",
                    Jsonb([{"evidence_id": evidence_ids[0], "story_identity": "one-story", "polarity": "supporting"}]),
                    Jsonb(evidence_ids[:1]), Jsonb(evidence_ids[:1]), Jsonb(evidence_ids[:1] if index == 0 else []),
                    Jsonb(evidence_ids[:1]), Jsonb(["⚠" * 2000]), Jsonb(["Recheck official status"]),
                    f"{index + 1000:064x}",
                ),
            )
        db.execute("SET session_replication_role=origin")
        db.execute("SET ROLE service_role")
        first = db.execute(
            "SELECT read_theme_memory_context(%s,'2026-09-08T12:00:00Z')", (target_run,)
        ).fetchone()[0]
        assert first["byte_truncated"] is True
        assert len(first["active_theme_heads"]) < 25
        assert len(first["urgent_events"]) == 1
        assert len(first["high_materiality_themes"]) == 10
        assert any(row["ticker"] == "FROZEN" for row in first["radar"])
        assert len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()) <= 65_536
        db.execute("RESET ROLE")
        db.execute("UPDATE radar SET reason='Changed after freeze' WHERE ticker='FROZEN'")
        db.execute("SET ROLE service_role")
        restarted = db.execute(
            "SELECT read_theme_memory_context(%s,'2026-09-09T12:00:00Z')", (target_run,)
        ).fetchone()[0]
        refreshed = db.execute("SELECT refresh_market_intelligence_context(%s)", (target_run,)).fetchone()[0]
        db.execute("RESET ROLE")
        binding = db.execute(
            "SELECT snapshot_hash,selected_revision_ids,selected_nomination_ids FROM market_intelligence_memory_context_bindings_v2 WHERE run_id=%s",
            (target_run,),
        ).fetchone()
        assert restarted == first
        assert refreshed["theme_memory"] == first
        assert refreshed["radar"] == first["radar"]
        assert refreshed["urgent_events"] == first["urgent_events"]
        assert refreshed["high_materiality_themes"] == first["high_materiality_themes"]
        assert refreshed["theme_memory_snapshot_hash"] == binding[0]
        assert binding[1] == [row["revision_id"] for row in first["active_theme_heads"]]
        assert binding[2] == [row["nomination_id"] for row in first["due_nominations"]]
