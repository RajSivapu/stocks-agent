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

from scripts.export_recovery_bundle import MAX_PAYLOAD_BYTES
from scripts.verify_personal_stock_agent_v1 import path_is_safe, require

READER = "stock_agent_release_reader_runtime"
RECOVERY_SQL = {
    "holdings": "SELECT ticker,shares::text AS shares,avg_cost::text AS average_cost,bucket,opened_at::text AS opened_at,notes,stop::text AS stop,target::text AS target,high_water_price::text AS high_water_price,hold_override_until::text AS hold_override_until,stop_alert_active,stop_near_alert_active,target_near_alert_active,target_alert_active FROM public.holdings",
    "transactions": "SELECT id::text AS id,ticker,qty::text AS quantity,price::text AS price,ts::text AS ts,side,source,executed_on::text AS executed_on FROM public.transactions",
    "commands": """SELECT id::text AS id,status,telegram_update_id::text AS telegram_update_id,chat_id::text AS chat_id,user_id::text AS user_id,
        operation,ticker,qty::text AS qty,price::text AS price,executed_on::text AS executed_on,bucket,expected_shares::text AS expected_shares,
        stop::text AS stop,preview,confirmation_message_id::text AS confirmation_message_id,expires_at::text AS expires_at,
        applied_at::text AS applied_at,realized_pnl::text AS realized_pnl,result,error,created_at::text AS created_at,updated_at::text AS updated_at FROM public.portfolio_commands""",
    "runs": "SELECT id::text AS id,status,kind AS phase,started_at::text AS started_at,finished_at::text AS finished_at,scheduled_phase,scheduled_market_date::text AS scheduled_market_date,gateway_request_id::text AS gateway_request_id,telegram_message_ids FROM public.analysis_runs",
    "packets": "SELECT id::text AS id,run_id::text AS run_id,packet_hash,packet FROM public.market_evidence_packets",
    "reports": "SELECT id::text AS id,run_id::text AS run_id,packet_id::text AS packet_id,report_hash,rendered_hash,report,rendered_text FROM public.market_reports",
    "publications": "SELECT report_id::text AS report_id,idempotency_key,status,telegram_message_ids,telegram_accepted_at::text AS telegram_accepted_at,suppression_reason FROM public.market_report_publications",
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
    "schema_version": """SELECT version,encode(extensions.digest(convert_to(array_to_string(statements,E'\\n'),'UTF8'),'sha256'),'hex') AS sha256
                         FROM supabase_migrations.schema_migrations""",
}
READ_TABLES = (
    "holdings", "transactions", "portfolio_commands", "analysis_runs", "market_evidence_packets", "market_reports",
    "market_report_publications", "market_intelligence_runs", "market_intelligence_collection_completions", "market_intelligence_run_events",
    "market_collection_checkpoints", "market_events", "market_candidate_rankings", "market_gateway_requests",
    "market_report_request_origins", "market_publications", "market_source_quota_reservations", "market_source_receipts",
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

    def dry_run_snapshot(self) -> dict:
        """Bound, fixed read-only proof surrounding a safe dry-run command."""
        self.identity()
        queries = {
            "scheduled_runs": "SELECT id::text AS id FROM public.analysis_runs WHERE scheduled_phase IS NOT NULL ORDER BY id",
            "transactions": "SELECT id::text AS id FROM public.transactions ORDER BY id",
            "market_publications": "SELECT id::text AS id FROM public.market_publications ORDER BY id",
            "telegram_publications": "SELECT report_id::text AS id FROM public.market_report_publications WHERE telegram_accepted_at IS NOT NULL ORDER BY report_id",
        }
        tables = {}
        for name, sql in queries.items():
            rows = self.query(sql)
            ids = [row["id"] for row in rows]
            require(all(isinstance(value, str) for value in ids), "dry-run queried IDs are malformed")
            tables[name] = {
                "count": len(ids), "ids": ids,
                "sha256": hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest(),
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
            "events": """SELECT id::text AS id,run_id::text AS run_id,content_hash,jsonb_build_object('event_type',event_type,'title',title,'summary',summary,'occurred_at',occurred_at,'effective_at',effective_at,'materiality',materiality,'confidence',confidence,'evidence_item_ids',evidence_item_ids) AS canonical FROM public.market_events WHERE run_id=%s::uuid""",
            "rankings": """SELECT id::text AS id,run_id::text AS run_id,event_id::text AS event_id,content_hash,jsonb_build_object('event_id',event_id,'candidate_key',candidate_key,'ticker',ticker,'rank',rank,'component_scores',component_scores,'total_score',total_score,'qualified',qualified,'veto_reasons',veto_reasons,'exposure_item_ids',exposure_item_ids) AS canonical FROM public.market_candidate_rankings WHERE run_id=%s::uuid""",
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
