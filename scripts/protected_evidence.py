"""Read-only production evidence adapters. No JSON file can impersonate these sources.

GitHub deployment records reference an immutable Actions artifact by numeric ID.
The artifact must belong to a successful protected release workflow on main. DB
reads use one repeatable-read, read-only transaction and a restricted login.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
from typing import Mapping
from urllib.parse import unquote, urlparse
import zipfile

import psycopg
from psycopg.rows import dict_row

from lib.intelligence.canonical import EVENT_CANONICAL_SQL, RANKING_CANONICAL_SQL
from scripts.export_recovery_bundle import MAX_PAYLOAD_BYTES
from scripts.verify_personal_stock_agent_v1 import path_is_safe, require

READER = "stock_agent_release_reader_runtime"
RECOVERY_SQL = {
    "decision_evaluations": "SELECT id::text,request_id::text,run_id::text,candidate_id::text,policy_version,input_digest,raw_action,final_action,policy_status,reason_codes,explanations,normalized,evidence,analyst,checker,created_at::text FROM public.decision_evaluations",
    "policy_comparisons": "SELECT id::text,run_id::text,packet_id::text,evaluation_id::text,comparison,created_at::text FROM public.market_policy_comparisons",
    "holdings": "SELECT ticker,shares::text AS shares,avg_cost::text AS average_cost,bucket,opened_at::text AS opened_at,notes,stop::text AS stop,target::text AS target,high_water_price::text AS high_water_price,hold_override_until::text AS hold_override_until,stop_alert_active,stop_near_alert_active,target_near_alert_active,target_alert_active FROM public.holdings",
    "transactions": "SELECT id::text AS id,ticker,qty::text AS quantity,price::text AS price,ts::text AS ts,side,source,executed_on::text AS executed_on FROM public.transactions",
    "commands": """SELECT id::text AS id,status,telegram_update_id::text AS telegram_update_id,chat_id::text AS chat_id,user_id::text AS user_id,
        operation,ticker,qty::text AS qty,price::text AS price,executed_on::text AS executed_on,bucket,expected_shares::text AS expected_shares,
        stop::text AS stop,amount::text AS amount,cadence,next_due_on::text AS next_due_on,
        expected_plan_updated_at::text AS expected_plan_updated_at,preview,
        confirmation_message_id::text AS confirmation_message_id,expires_at::text AS expires_at,
        applied_at::text AS applied_at,realized_pnl::text AS realized_pnl,result,error,created_at::text AS created_at,updated_at::text AS updated_at FROM public.portfolio_commands""",
    "command_acknowledgements": """SELECT command_id::text AS command_id,telegram_update_id::text AS telegram_update_id,status,result,error,
        lease_token::text AS lease_token,lease_expires_at::text AS lease_expires_at,attempt_count,
        created_at::text AS created_at,updated_at::text AS updated_at FROM public.portfolio_command_acknowledgements""",
    "runs": """SELECT id::text AS id,status,kind AS phase,started_at::text AS started_at,finished_at::text AS finished_at,
        data_as_of::text AS data_as_of,source_status,symbols,write_counts,telegram_message_ids,summary,error,
        scheduled_phase,scheduled_market_date::text AS scheduled_market_date,gateway_request_id::text AS gateway_request_id
        FROM public.analysis_runs""",
    "gateway_requests": """SELECT request_id::text AS request_id,operation,run_id::text AS run_id,status,lease_token::text AS lease_token,
        attempt_count,response,response_digest,created_at::text AS created_at,claimed_at::text AS claimed_at,finished_at::text AS finished_at
        FROM public.market_gateway_requests""",
    "policies": "SELECT version,config,active,created_at::text AS created_at,activated_at::text AS activated_at FROM public.market_policy_config",
    "intelligence_runs": """SELECT id::text AS id,phase,market_date::text AS market_date,policy_version,reservation_plan,request_window,
        created_at::text AS created_at FROM public.market_intelligence_runs""",
    "intelligence_run_events": """SELECT id::text AS id,run_id::text AS run_id,status,detail,created_at::text AS created_at
        FROM public.market_intelligence_run_events""",
    "source_quota_reservations": """SELECT id::text AS id,run_id::text AS run_id,provider,market_date::text AS market_date,
        phase,reserved_requests,cache_keys,created_at::text AS created_at FROM public.market_source_quota_reservations""",
    "collection_checkpoints": """SELECT run_id::text AS run_id,cache_key,request_window,source_receipt_id::text AS source_receipt_id,
        payload,created_at::text AS created_at FROM public.market_collection_checkpoints""",
    "collection_checkpoint_history": """SELECT run_id::text AS run_id,cache_key,source_receipt_id::text AS source_receipt_id,
        payload,replaced_at::text AS replaced_at FROM public.market_collection_checkpoint_history""",
    "collection_completions": """SELECT completion_id::text AS completion_id,run_id::text AS run_id,payload,receipt,
        created_at::text AS created_at FROM public.market_intelligence_collection_completions""",
    "packets": """SELECT id::text AS id,run_id::text AS run_id,policy_version,status,candidate_count,evidence_count,
        packet_hash,packet,created_at::text AS created_at FROM public.market_evidence_packets""",
    "reports": """SELECT id::text AS id,run_id::text AS run_id,packet_id::text AS packet_id,idempotency_key,market_date::text AS market_date,kind,
        report_hash,rendered_hash,report,rendered_text,created_at::text AS created_at FROM public.market_reports""",
    "report_origins": """SELECT request_id::text AS request_id,run_id::text AS run_id,scheduled_phase,market_date::text AS market_date,
        requested_kind,requested_report_id::text AS requested_report_id,requested_packet_id::text AS requested_packet_id,
        requested_idempotency_key,requested_report_hash,created_at::text AS created_at FROM public.market_report_request_origins""",
    "publications": """SELECT report_id::text AS report_id,idempotency_key,status,telegram_message_ids,
        telegram_accepted_at::text AS telegram_accepted_at,suppression_reason,attempt_count,lease_token::text AS lease_token,
        lease_expires_at::text AS lease_expires_at,error,created_at::text AS created_at,updated_at::text AS updated_at
        FROM public.market_report_publications""",
    "evaluation_publications": """SELECT id::text AS id,idempotency_key::text AS idempotency_key,run_id::text AS run_id,
        market_date::text AS market_date,phase,kind,template_version,rendered_body,rendered_hash,status,telegram_message_ids,
        attempt_count,lease_token::text AS lease_token,sending_started_at::text AS sending_started_at,delivered_at::text AS delivered_at,
        telegram_accepted_at::text AS telegram_accepted_at,error,created_at::text AS created_at,updated_at::text AS updated_at
        FROM public.market_publications""",
    "cash_ledger_state": "SELECT singleton,revision::text AS revision,updated_at::text AS updated_at FROM public.portfolio_cash_ledger_state",
    "cash_snapshots": """SELECT id::text AS id,as_of::text AS as_of,fresh_through::text AS fresh_through,
        ledger_watermark::text AS ledger_watermark,core_available::text AS core_available,growth_available::text AS growth_available,
        speculative_available::text AS speculative_available,created_at::text AS created_at FROM public.reconciled_cash_snapshots""",
    "run_terminal_outcomes": """SELECT run_id::text AS run_id,evaluation_request_id::text AS evaluation_request_id,outcome,
        created_at::text AS created_at FROM public.market_run_terminal_outcomes""",
    "roles": """SELECT r.rolname AS role,r.rolcanlogin AS login,r.rolsuper AS superuser,r.rolbypassrls AS bypass_rls,
        COALESCE((SELECT jsonb_agg(parent.rolname ORDER BY parent.rolname) FROM pg_catalog.pg_auth_members m
                  JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid WHERE m.member=r.oid),'[]'::jsonb) AS memberships,
        COALESCE((SELECT jsonb_agg(g.privilege ORDER BY g.privilege) FROM (
            SELECT a.privilege_type||':'||n.nspname||'.'||c.relname||':grantable='||a.is_grantable::text AS privilege
              FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE a.grantee=r.oid
            UNION ALL
            SELECT a.privilege_type||':'||n.nspname||'.'||c.relname||'.'||col.attname||':grantable='||a.is_grantable::text
              FROM pg_catalog.pg_attribute col JOIN pg_catalog.pg_class c ON c.oid=col.attrelid
              JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN LATERAL aclexplode(col.attacl) a WHERE a.grantee=r.oid
        ) g),'[]'::jsonb) AS grants
        FROM pg_catalog.pg_roles r WHERE r.rolname IN ('stock_agent_dashboard','stock_agent_dashboard_runtime')""",
    "schema_version": """SELECT version,statements,encode(extensions.digest(convert_to(array_to_string(statements,E'\\n'),'UTF8'),'sha256'),'hex') AS sha256
                         FROM supabase_migrations.schema_migrations""",
}
READ_TABLES = (
    "holdings", "transactions", "portfolio_commands", "portfolio_command_acknowledgements", "analysis_runs", "market_policy_config", "market_evidence_packets", "market_reports",
    "market_report_publications", "market_intelligence_runs", "market_intelligence_collection_completions", "market_intelligence_run_events",
    "market_collection_checkpoints", "market_collection_checkpoint_history", "market_events", "market_candidate_rankings", "market_gateway_requests",
    "market_report_request_origins", "market_publications", "market_source_quota_reservations", "market_source_receipts",
    "market_alert_drafts", "market_alert_events", "market_alert_actions", "portfolio_cash_ledger_state",
    "reconciled_cash_snapshots", "market_run_terminal_outcomes",
)


class PostgresReadOnlySource:
    def __init__(self, database_url: str, project_ref: str, *, isolated_guard: bool = False, production_project_ref: str | None = None):
        parsed = urlparse(database_url)
        require(bool(re.fullmatch(r"[a-z0-9]{20}", project_ref)), "exact database project identity is required")
        user = unquote(parsed.username or "")
        direct = parsed.hostname == f"db.{project_ref}.supabase.co" and user == READER
        pooler = bool(re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", parsed.hostname or "")) and user == f"{READER}.{project_ref}"
        require(parsed.scheme in {"postgres", "postgresql"} and (direct or pooler) and parsed.port in {None, 5432}
                and parsed.path == "/postgres" and parsed.password and not parsed.query and not parsed.fragment,
                "database URL must identify the exact project and read-only login")
        if isolated_guard:
            require(bool(re.fullmatch(r"[a-z0-9]{20}", production_project_ref or "")) and project_ref != production_project_ref,
                    "guarded restore project must differ from production")
        self._url = database_url
        self.project_ref = project_ref
        self.isolated_guard = isolated_guard
        self.connection = None

    def __enter__(self):
        try:
            self.connection = psycopg.connect(self._url, row_factory=dict_row, sslmode="verify-full", connect_timeout=15)
            self.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.connection.execute("SET LOCAL statement_timeout='30s'")
            row = self.query("""SELECT current_user AS role,current_database() AS database,
                inet_server_addr()::text AS server,inet_server_port() AS port,
                current_setting('transaction_read_only') AS read_only,rolsuper,rolbypassrls
                FROM pg_catalog.pg_roles WHERE rolname=current_user""")[0]
            require(row["role"] == READER and row["read_only"] == "on" and not row["rolsuper"] and not row["rolbypassrls"]
                    and row["server"] and row["database"], "queried database identity is not a restricted read-only source")
            for table in READ_TABLES:
                privileges = self.query("SELECT has_table_privilege(current_user,%s,'SELECT') AS readable,has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS writable", (f"public.{table}", f"public.{table}"))[0]
                require(privileges["readable"] is True and privileges["writable"] is False, "read-only database source lacks SELECT or has write authority")
            self._identity = {"project_ref": self.project_ref, "connection_id": hashlib.sha256(f"{row['server']}:{row['port']}/{row['database']}".encode()).hexdigest(),
                              "read_only": True, "isolated_guard": self.isolated_guard}
            return self
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def __exit__(self, *_exc):
        if self.connection is not None:
            self.connection.rollback()
            self.connection.close()

    def identity(self):
        require(self.connection is not None and not self.connection.closed, "read-only source is not connected")
        return dict(self._identity)

    def query(self, sql: str, parameters: tuple = ()) -> list[dict]:
        require(self.connection is not None and sql.lstrip().upper().startswith("SELECT "), "only fixed SELECT evidence queries are permitted")
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, parameters)
            return [dict(row) for row in cursor.fetchall()]

    def read_records(self):
        self.identity()
        return {name: self.query(sql) for name, sql in RECOVERY_SQL.items()}

    def counts(self):
        self.identity()
        return {name: self.query(f"SELECT count(*) AS count FROM ({sql}) AS records")[0]["count"] for name, sql in RECOVERY_SQL.items()}

    def refresh_snapshot(self):
        """Begin a fresh read-only snapshot after an isolated writer commits restore data."""
        self.identity()
        self.connection.rollback()
        self.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.connection.execute("SET LOCAL statement_timeout='30s'")

    def dry_run_snapshot(self) -> dict:
        """Hash canonical full rows across every release write surface."""
        self.identity()
        tables = {}
        for name in READ_TABLES:
            rows = self.query(f"SELECT to_jsonb(t) AS row FROM public.{name} AS t ORDER BY to_jsonb(t)::text")
            canonical_rows = [json.dumps(row["row"], sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in rows]
            require(all(isinstance(value, str) for value in canonical_rows), "dry-run rows are malformed")
            tables[name] = {
                "count": len(canonical_rows),
                "rows_sha256": hashlib.sha256("\n".join(canonical_rows).encode()).hexdigest(),
            }
        return {"source": self.identity(), "tables": tables}

    def release_rows(self, run_id: str) -> dict:
        require(bool(re.fullmatch(r"[0-9a-f-]{36}", run_id)), "run UUID is required")
        parameter = (run_id,)
        queries = {
            "run": "SELECT id::text AS id,kind,scheduled_phase,scheduled_market_date::text AS scheduled_market_date,status,started_at::text AS started_at,finished_at::text AS finished_at,gateway_request_id::text AS gateway_request_id,telegram_message_ids FROM public.analysis_runs WHERE id=%s::uuid",
            "intelligence_runs": "SELECT id::text AS id,phase,market_date::text AS market_date FROM public.market_intelligence_runs WHERE id=%s::uuid",
            "completions": "SELECT completion_id::text AS completion_id,run_id::text AS run_id,receipt FROM public.market_intelligence_collection_completions WHERE run_id=%s::uuid",
            "run_events": "SELECT id::text AS id,run_id::text AS run_id,status FROM public.market_intelligence_run_events WHERE run_id=%s::uuid",
            "checkpoints": "SELECT run_id::text AS run_id,cache_key FROM public.market_collection_checkpoints WHERE run_id=%s::uuid",
            "packets": RECOVERY_SQL["packets"] + " WHERE run_id=%s::uuid",
            "reports": "SELECT id::text AS id,run_id::text AS run_id,packet_id::text AS packet_id,report_hash,rendered_hash,report,rendered_text,idempotency_key,market_date::text AS market_date,kind FROM public.market_reports WHERE run_id=%s::uuid",
            "publications": RECOVERY_SQL["publications"] + " WHERE report_id IN (SELECT id FROM public.market_reports WHERE run_id=%s::uuid)",
            "events": f"""SELECT id::text AS id,run_id::text AS run_id,content_hash,{EVENT_CANONICAL_SQL} AS canonical FROM public.market_events WHERE run_id=%s::uuid""",
            "rankings": f"""SELECT id::text AS id,run_id::text AS run_id,event_id::text AS event_id,content_hash,{RANKING_CANONICAL_SQL} AS canonical FROM public.market_candidate_rankings WHERE run_id=%s::uuid""",
            "evaluation_publications": "SELECT id::text AS id,run_id::text AS run_id,status,phase,market_date::text AS market_date FROM public.market_publications WHERE run_id=%s::uuid",
            "origins": "SELECT request_id::text AS request_id,run_id::text AS run_id,requested_packet_id::text AS requested_packet_id,scheduled_phase,market_date::text AS market_date,requested_kind,requested_report_id::text AS requested_report_id,requested_idempotency_key,requested_report_hash FROM public.market_report_request_origins WHERE run_id=%s::uuid",
            "quota": "SELECT q.id::text AS id,q.run_id::text AS run_id,q.provider,q.reserved_requests,COALESCE((SELECT sum(r.request_cost) FROM public.market_source_receipts r WHERE r.reservation_id=q.id),0)::int AS actual_requests FROM public.market_source_quota_reservations q WHERE q.run_id=%s::uuid",
        }
        result = {name: self.query(sql, parameter) for name, sql in queries.items()}
        result["requests"] = self.query("""SELECT request_id::text AS request_id,run_id::text AS run_id,operation,status,response FROM public.market_gateway_requests
            WHERE run_id=%s::uuid OR request_id IN (SELECT request_id FROM public.market_report_request_origins WHERE run_id=%s::uuid)""", (run_id, run_id))
        return result


class GitHubProductionDataSource:
    def __init__(self, repository: str, project_ref: str, database: PostgresReadOnlySource):
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)), "GitHub repository must be exact owner/name")
        require(bool(re.fullmatch(r"[a-z0-9]{20}", project_ref)), "production project identity is required")
        self.prefix = f"repos/{repository}"
        self.project_ref, self.database = project_ref, database
        self.candidate = None

    def _get(self, path: str, *, binary: bool = False):
        require(path.startswith(self.prefix + "/") and ".." not in path and not path.startswith("-"), "unsafe protected record path")
        result = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, check=False, timeout=60)
        require(result.returncode == 0 and len(result.stdout) <= MAX_PAYLOAD_BYTES, "protected GitHub evidence is unavailable")
        return result.stdout if binary else json.loads(result.stdout)

    def deployment(self, deployment_id: int) -> Mapping:
        require(type(deployment_id) is int and deployment_id > 0, "numeric deployment ID required")
        deployment = self._get(f"{self.prefix}/deployments/{deployment_id}")
        require(deployment.get("environment") == "production" and deployment.get("production_environment") is True,
                "protected production deployment record is required")
        statuses = self._get(f"{self.prefix}/deployments/{deployment_id}/statuses")
        require(statuses and statuses[0].get("state") == "success", "latest production deployment status is not successful")
        self.candidate = deployment["sha"]
        match = re.fullmatch(r"release-artifact:([1-9][0-9]*)", str(statuses[0].get("description", "")))
        require(match is not None, "protected deployment status lacks immutable release artifact identity")
        artifact_id = int(match.group(1))
        files = self.artifact(artifact_id)
        require(set(files) == {"release-record.json"}, "release artifact has unexpected files")
        record = json.loads(files["release-record.json"])
        require(record["candidate_sha"] == self.candidate and record["project_ref"] == self.project_ref
                and record["deployment_id"] == deployment["id"], "protected deployment candidate/project mismatch")
        return {**record, "id": deployment["id"], "sha": deployment["sha"], "environment": deployment["environment"], "deployed_at": statuses[0]["created_at"]}

    def artifact(self, artifact_id: int) -> dict[str, bytes]:
        require(type(artifact_id) is int and artifact_id > 0, "numeric protected artifact ID required")
        metadata = self._get(f"{self.prefix}/actions/artifacts/{artifact_id}")
        run = self._get(f"{self.prefix}/actions/runs/{metadata['workflow_run']['id']}")
        require(not metadata["expired"] and metadata["workflow_run"]["head_sha"] == self.candidate
                and run["head_sha"] == self.candidate and run["head_branch"] == "main" and run["conclusion"] == "success"
                and run["path"] == ".github/workflows/owner-dashboard-release.yml", "artifact did not originate in the protected candidate release workflow")
        raw = self._get(f"{self.prefix}/actions/artifacts/{artifact_id}/zip", binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(members and len({member.filename for member in members}) == len(members)
                    and all(path_is_safe(member.filename) and not member.is_dir() and (member.external_attr >> 16) & 0o170000 != 0o120000 for member in members)
                    and sum(member.file_size for member in members) <= MAX_PAYLOAD_BYTES, "protected artifact has unsafe paths or members")
            return {member.filename: archive.read(member) for member in members}

    def ci(self, workflow_run_id: int):
        require(type(workflow_run_id) is int and workflow_run_id > 0, "numeric CI run ID required")
        return self._get(f"{self.prefix}/actions/runs/{workflow_run_id}")

    def merge(self, number: int):
        require(type(number) is int and number > 0, "numeric pull request ID required")
        return self._get(f"{self.prefix}/pulls/{number}")

    def reviews(self, number: int):
        rows = self._get(f"{self.prefix}/pulls/{number}/reviews?per_page=100")
        require(len(rows) < 100, "review evidence exceeds bounded page; cannot infer completeness")
        latest = {}
        for row in rows:
            if row["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[row["user"]["id"]] = row
        return list(latest.values())

    def release_rows(self, run_id: str):
        require(self.database.identity()["project_ref"] == self.project_ref, "queried production database identity mismatch")
        return self.database.release_rows(run_id)

    def scheduled_run(self, deployed_at: str) -> str:
        require(self.database.identity()["project_ref"] == self.project_ref, "queried production database identity mismatch")
        rows = self.database.query("""SELECT id::text AS id FROM public.analysis_runs
            WHERE started_at>%s::timestamptz AND scheduled_phase IN ('pre-market','intraday','post-market')
            ORDER BY started_at,id LIMIT 1""", (deployed_at,))
        require(len(rows) == 1, "next existing scheduled production run is unavailable")
        return rows[0]["id"]
