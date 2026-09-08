"""Execute the reconciliation against a disposable, populated legacy database."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from scripts.inspect_production_schema_baseline import _catalog_query


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "sql/migrations"
RECONCILIATION = ROOT / "sql/reconciliation/20261004_production_schema_reconciliation.sql"
ACL_CLOSURE = MIGRATIONS / "20261003_release_ledger_acl_closure.sql"
DISCOVERY = MIGRATIONS / "20261005_market_wide_discovery.sql"
REFERENCE_TRANSFER = MIGRATIONS / "20261006_reference_snapshot_transfer.sql"
CURSOR_CONTEXT = MIGRATIONS / "20261007_discovery_cursor_context.sql"
OFFICIAL_COMPLETION = MIGRATIONS / "20261008_official_source_completion_contract.sql"
ISSUER_NAMES = MIGRATIONS / "20261009_reference_issuer_names.sql"
ENRICHMENT = MIGRATIONS / "20261010_bounded_adaptive_enrichment.sql"
RESEARCH_SUITABILITY = MIGRATIONS / "20261011_research_suitability_packet_contract.sql"
THEME_MEMORY = MIGRATIONS / "20261012_theme_memory_research_nominations.sql"
RUNTIME_COMPLETION = MIGRATIONS / "20261013_v2_runtime_completion.sql"

# The original pre-20260901 tables. Historical migrations run only in this
# disposable fixture, before representative legacy facts are inserted.
LEGACY_BASE = """
CREATE TABLE holdings(ticker TEXT PRIMARY KEY,shares NUMERIC NOT NULL,avg_cost NUMERIC NOT NULL,
 bucket TEXT,opened_at DATE,notes TEXT,stop NUMERIC,target NUMERIC,high_water_price NUMERIC,
 stop_alert_active BOOLEAN DEFAULT false,hold_override_until DATE,stop_near_alert_active BOOLEAN DEFAULT false,
 target_near_alert_active BOOLEAN DEFAULT false,target_alert_active BOOLEAN DEFAULT false);
CREATE TABLE transactions(id BIGSERIAL PRIMARY KEY,ts TIMESTAMPTZ NOT NULL DEFAULT now(),ticker TEXT NOT NULL,
 side TEXT NOT NULL CHECK(side IN ('buy','sell')),qty NUMERIC NOT NULL,price NUMERIC NOT NULL,source TEXT DEFAULT 'owner');
CREATE TABLE suggestions(id BIGSERIAL PRIMARY KEY,ts TIMESTAMPTZ NOT NULL DEFAULT now(),date DATE NOT NULL,
 ticker TEXT NOT NULL,action TEXT NOT NULL,bucket TEXT,depth TEXT,entry_zone_low NUMERIC,entry_zone_high NUMERIC,
 valid_until DATE,stop NUMERIC,target NUMERIC,confidence TEXT,bull TEXT,bear TEXT,decisive_factor TEXT,
 risk_verdict TEXT,invalidation_level TEXT,reason TEXT,score INT,score_growth INT,score_health INT,score_valuation INT,
 risk_band TEXT,score_inputs TEXT,score_partial BOOLEAN DEFAULT false,price_at_suggestion NUMERIC);
CREATE TABLE suggestion_grades(id BIGSERIAL PRIMARY KEY,suggestion_id BIGINT REFERENCES suggestions(id),
 graded_at TIMESTAMPTZ DEFAULT now(),result TEXT,price_then NUMERIC,price_later NUMERIC,horizon_days INT,note TEXT);
CREATE TABLE stock_observations(id BIGSERIAL PRIMARY KEY,ticker TEXT NOT NULL,obs_date DATE NOT NULL,
 event_type TEXT,summary TEXT,price_reaction TEXT,confidence TEXT,source TEXT,created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX idx_obs_ticker ON stock_observations(ticker);
CREATE TABLE daily_snapshots(id BIGSERIAL PRIMARY KEY,snap_date DATE NOT NULL,ticker TEXT NOT NULL,
 close NUMERIC,day_move_pct NUMERIC,rsi14 NUMERIC,sma50 NUMERIC,sma200 NUMERIC,macd_hist NUMERIC,UNIQUE(snap_date,ticker));
CREATE TABLE dry_powder(month TEXT PRIMARY KEY,growth_available NUMERIC DEFAULT 0,spec_available NUMERIC DEFAULT 0,
 rolled_months INT DEFAULT 0);
CREATE TABLE radar(ticker TEXT PRIMARY KEY,added DATE,last_seen DATE,days_relevant INT,reason TEXT,bucket_guess TEXT,
 promoted BOOLEAN DEFAULT false,promoted_on DATE);
CREATE TABLE lessons(id BIGSERIAL PRIMARY KEY,entry_date DATE NOT NULL,category TEXT NOT NULL DEFAULT 'regime',
 content TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX idx_lessons_date ON lessons(entry_date DESC,id DESC);
CREATE TABLE paper_watches(id BIGSERIAL PRIMARY KEY,ticker TEXT NOT NULL,created DATE NOT NULL,entry_ref_price NUMERIC,
 target_price NUMERIC,hypothetical_amount NUMERIC,thesis TEXT,horizon TEXT,status TEXT NOT NULL DEFAULT 'active',
 closed_date DATE,close_price NUMERIC,agent_view_at_open TEXT,agent_score_at_open INT,created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX idx_paper_status ON paper_watches(status);
DO $$ DECLARE name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['holdings','transactions','suggestions','suggestion_grades','stock_observations',
 'daily_snapshots','dry_powder','radar','lessons','paper_watches'] LOOP
 EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',name); END LOOP;
END $$;
"""

SEED = """
INSERT INTO holdings(ticker,shares,avg_cost,bucket,opened_at,notes) VALUES('TEST',12,99.125,'core','2020-01-01','preserve');
INSERT INTO transactions(ticker,side,qty,price,source,executed_on) VALUES('TEST','buy',12,99.125,'telegram','2026-09-01');
INSERT INTO dry_powder(month,growth_available,spec_available) VALUES('2026-09',432.10,87.65);
INSERT INTO analysis_runs(id,kind,status,summary) VALUES('00000000-0000-0000-0000-000000000001','pre-market','completed','legacy');
INSERT INTO portfolio_commands(id,telegram_update_id,chat_id,user_id,operation,ticker,qty,price,bucket,expected_shares,status)
 VALUES('00000000-0000-0000-0000-000000000002',42,5,5,'buy','TEST',12,99.125,'core',0,'applied');
INSERT INTO market_policy_config(version,config) VALUES(99,'{"fixture":"unchanged"}');
INSERT INTO decision_evaluations(id,candidate_id,input_digest,raw_action,policy_status)
 VALUES('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000004',repeat('a',64),'hold','legacy_unverified');
INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan)
 VALUES('00000000-0000-0000-0000-000000000001','pre-market','2026-09-01',99,'{}');
"""


def test_exact_legacy_reconciliation_preserves_facts_matches_fresh_and_refuses_retry():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local server binaries and a non-root user")
    assert RECONCILIATION.is_file()
    assert ACL_CLOSURE.is_file()

    def run(name, *args, sql=None, check=True):
        result = subprocess.run([binaries[name], *args], input=sql, text=True,
                                capture_output=True, timeout=60)
        if check:
            assert result.returncode == 0, result.stderr
        return result

    with tempfile.TemporaryDirectory(prefix="reconcile-", dir="/tmp") as directory:
        data = str(Path(directory) / "data")
        run("initdb", "-D", data, "-U", "postgres", "--auth=trust", "--no-locale", "--encoding=UTF8")
        run("pg_ctl", "-D", data, "-l", str(Path(directory) / "server.log"),
            "-o", f"-F -k {directory} -c listen_addresses=''", "-w", "start")
        try:
            def execute(database, sql, *, check=True):
                return run("psql", "-X", "-h", directory, "-p", "5432", "-U", "postgres", "-d", database,
                           "-v", "ON_ERROR_STOP=1", "-qAt", sql=sql, check=check)

            def scalar(database, sql):
                return json.loads(execute(database, sql).stdout)

            execute("postgres", "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;"
                    "CREATE DATABASE legacy; CREATE DATABASE fresh;")
            for database in ("legacy", "fresh"):
                execute(database, "CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions;"
                        "CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid';"
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon,authenticated,service_role;"
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon,authenticated,service_role;")
            execute("legacy", LEGACY_BASE)
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                if migration.name[:8] <= "20260909":
                    execute("legacy", migration.read_text())

            def catalog(database):
                return scalar(database, "SELECT row_to_json(result) FROM (" + _catalog_query() + ") result;")["catalog"]

            before_catalog = catalog("legacy")
            before_tables = {row["name"] for row in before_catalog["relations"] if row["kind"] in {"r", "p"}}
            assert len(before_tables) == 35
            execute("legacy", SEED)

            def projected_rows(database):
                result = {}
                for table in sorted(before_tables):
                    columns = sorted((c for c in before_catalog["columns"] if c["relation"] == table), key=lambda c: c["position"])
                    fields = ",".join('"' + c["name"] + '"' for c in columns)
                    result[table] = scalar(database, "SELECT COALESCE(jsonb_agg(to_jsonb(row) ORDER BY to_jsonb(row)::text),'[]')"
                                           f' FROM (SELECT {fields} FROM public."{table}") row;')
                return result

            before = projected_rows("legacy")
            reconciliation = RECONCILIATION.read_text()
            execute("legacy", "BEGIN;" + reconciliation + "COMMIT;")
            assert projected_rows("legacy") == before
            execute("legacy", "BEGIN;" + DISCOVERY.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + REFERENCE_TRANSFER.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + CURSOR_CONTEXT.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + OFFICIAL_COMPLETION.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + ISSUER_NAMES.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + ENRICHMENT.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + RESEARCH_SUITABILITY.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + THEME_MEMORY.read_text() + "COMMIT;")
            execute("legacy", "BEGIN;" + RUNTIME_COMPLETION.read_text() + "COMMIT;")
            assert projected_rows("legacy") == before
            after_catalog = catalog("legacy")
            after_tables = {row["name"] for row in after_catalog["relations"] if row["kind"] in {"r", "p"}}
            assert len(after_tables - before_tables) == 38
            assert scalar("legacy", "SELECT count(*) FROM supabase_migrations.schema_migrations;") == 0
            assert scalar("legacy", "SELECT count(*) FROM public.stock_agent_release_migration_ledger;") == 0
            assert scalar("legacy", "SELECT jsonb_agg(jsonb_build_object('singleton',singleton,'revision',revision)) FROM portfolio_cash_ledger_state;") == [{"singleton": True, "revision": 0}]
            assert scalar("legacy", "SELECT count(*) FROM market_scheduled_phase_deadlines WHERE effective_on=timezone('America/Chicago',statement_timestamp())::date;") == 3
            assert scalar("legacy", "SELECT bool_or(has_table_privilege('service_role',"
                          "'public.stock_agent_release_migration_ledger',privilege))::text FROM unnest("
                          "ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) privilege;") is False

            execute("fresh", (ROOT / "sql/schema.sql").read_text() + ACL_CLOSURE.read_text())

            def structural(value):
                # Attribute positions reflect historical ADD COLUMN order, not
                # column identity. The preservation check above retains order.
                value = json.loads(json.dumps(value))
                for column in value["columns"]:
                    column.pop("position")
                return {category: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
                        for category, rows in value.items()}

            assert structural(after_catalog) == structural(catalog("fresh"))
            after_rows = projected_rows("legacy")

            def new_rows():
                return {table: scalar("legacy", "SELECT COALESCE(jsonb_agg(to_jsonb(row) ORDER BY to_jsonb(row)::text),'[]')"
                                      f' FROM public."{table}" row;')
                        for table in sorted(after_tables - before_tables)}

            after_new_rows = new_rows()
            assert {table for table, rows in after_new_rows.items() if rows} == {
                "portfolio_cash_ledger_state", "market_scheduled_phase_deadlines",
            }
            retry = execute("legacy", "BEGIN;" + reconciliation + "COMMIT;", check=False)
            assert retry.returncode != 0 and "reconciliation baseline" in retry.stderr
            assert projected_rows("legacy") == after_rows
            assert new_rows() == after_new_rows
            assert catalog("legacy") == after_catalog
            assert scalar("legacy", "SELECT count(*) FROM public.stock_agent_release_migration_ledger;") == 0
            assert scalar("legacy", "SELECT count(*) FROM supabase_migrations.schema_migrations;") == 0
        finally:
            run("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")
