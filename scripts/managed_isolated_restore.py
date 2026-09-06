#!/usr/bin/env python3
"""Protected, management-API-only isolated recovery drill.

This module deliberately has no credentials in its command-line interface.  It
is invoked by the manual protected workflow, which supplies an access token and
the recovery encryption key through the runner environment.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import base64
import hashlib
import json
import re
import secrets
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.export_recovery_bundle import REQUIRED_RECOVERY_RECORDS, canonical_json
from scripts.protected_evidence import RECOVERY_SQL

PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")
MAX_MANAGEMENT_RESPONSE_BYTES = 32 * 1024 * 1024
ManagementRequest = Callable[[str, str, Mapping[str, object] | None], object]


def _project_ref(value: object, label: str) -> str:
    if not isinstance(value, str) or not PROJECT_REF.fullmatch(value):
        raise RuntimeError(f"exact {label} project identity is required")
    return value


class SupabaseManagementApi:
    """Small injectable client for the documented Management API.

    Exceptions intentionally contain no response body because Management API
    errors can echo credential-bearing connection material.
    """

    def __init__(self, access_token: str, *, request: ManagementRequest | None = None,
                 base_url: str = "https://api.supabase.com"):
        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("Supabase access token is required")
        self._token = access_token
        self._request = request
        self._base_url = base_url.rstrip("/")

    def __call__(self, method: str, path: str, payload: Mapping[str, object] | None = None) -> object:
        if self._request is not None:
            return self._request(method, path, payload)
        if method not in {"GET", "POST", "DELETE"} or not path.startswith("/v1/") or ".." in path:
            raise RuntimeError("unsafe Management API request")
        data = None if payload is None else canonical_json(payload).encode()
        request = Request(self._base_url + path, data=data, method=method,
                          headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(MAX_MANAGEMENT_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError) as error:
            raise RuntimeError("Supabase Management API request failed") from error
        if len(raw) > MAX_MANAGEMENT_RESPONSE_BYTES:
            raise RuntimeError("Supabase Management API response exceeds limit")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("Supabase Management API returned invalid JSON") from error


def _snapshot_sql() -> str:
    # One fixed query obtains all allowlisted sets atomically; ORDER BY gives a
    # deterministic snapshot before the existing exporter validates it.
    parts = []
    for name in REQUIRED_RECOVERY_RECORDS:
        sql = RECOVERY_SQL[name].strip().rstrip(";")
        parts.append("%s,COALESCE((SELECT jsonb_agg(to_jsonb(records) ORDER BY to_jsonb(records)::text) "
                     "FROM (%s) AS records),'[]'::jsonb)" % (json.dumps(name), sql))
    return "SELECT jsonb_build_object('datasets',jsonb_build_object(" + ",".join(parts) + ")) AS snapshot"


class ManagedReadOnlyRecoverySource:
    """RecoveryDataSource backed only by the Management read-only SQL route."""

    def __init__(self, request: ManagementRequest, production_project_ref: str, *, isolated_guard: bool = False):
        self._request = request
        self.project_ref = _project_ref(production_project_ref, "production")
        self.isolated_guard = isolated_guard
        self._identity: dict[str, object] | None = None
        self._records: dict[str, list[dict[str, object]]] | None = None

    @property
    def _path(self) -> str:
        return f"/v1/projects/{self.project_ref}/database/query/read-only"

    def _query(self, query: str) -> list[dict[str, object]]:
        response = self._request("POST", self._path, {"query": query})
        if not isinstance(response, list) or not all(isinstance(row, Mapping) for row in response):
            raise RuntimeError("read-only SQL endpoint returned malformed rows")
        return [dict(row) for row in response]

    def identity(self) -> dict[str, object]:
        if self._identity is None:
            rows = self._query("SELECT current_user AS role,current_setting('transaction_read_only') AS transaction_read_only,current_database() AS database")
            if len(rows) != 1 or rows[0].get("role") != "supabase_read_only_user" or rows[0].get("transaction_read_only") != "on" or not isinstance(rows[0].get("database"), str):
                raise RuntimeError("Management read-only identity is unavailable or unsafe")
            self._identity = {
                "project_ref": self.project_ref,
                "connection_id": hashlib.sha256(f"management:{self.project_ref}:{rows[0]['database']}".encode()).hexdigest(),
                "read_only": True,
                "isolated_guard": self.isolated_guard,
                "management_role": "supabase_read_only_user",
            }
        return dict(self._identity)

    def _load(self) -> dict[str, list[dict[str, object]]]:
        self.identity()
        if self._records is None:
            rows = self._query(_snapshot_sql())
            snapshot = rows[0].get("snapshot") if len(rows) == 1 else None
            datasets = snapshot.get("datasets") if isinstance(snapshot, Mapping) else None
            if not isinstance(datasets, Mapping) or set(datasets) != set(REQUIRED_RECOVERY_RECORDS):
                raise RuntimeError("read-only recovery snapshot is incomplete")
            if not all(isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows) for rows in datasets.values()):
                raise RuntimeError("read-only recovery snapshot is malformed")
            self._records = {name: [dict(row) for row in datasets[name]] for name in REQUIRED_RECOVERY_RECORDS}
        return self._records

    def read_records(self) -> dict[str, list[dict[str, object]]]:
        return json.loads(canonical_json(self._load()))

    def counts(self) -> dict[str, int]:
        # Counts are derived from the same cached canonical snapshot: no race.
        return {name: len(rows) for name, rows in self._load().items()}


class ManagedRestoreTarget:
    """The only writer: exact project created by this drill invocation."""

    def __init__(self, request: ManagementRequest, restore_project_ref: str, production_project_ref: str,
                 *, created_project_ref: str | None):
        self._request = request
        self.project_ref = _project_ref(restore_project_ref, "restore")
        production = _project_ref(production_project_ref, "production")
        if self.project_ref == production:
            raise RuntimeError("restore project must differ from production")
        if created_project_ref != self.project_ref:
            raise RuntimeError("restore target must be created by this run")
        self._identity = {
            "project_ref": self.project_ref,
            "connection_id": hashlib.sha256(f"management:{self.project_ref}:postgres".encode()).hexdigest(),
            "read_only": False, "restore_capable": True, "isolated_guard": True,
        }

    @property
    def _path(self) -> str:
        return f"/v1/projects/{self.project_ref}/database/query"

    def identity(self) -> dict[str, object]:
        return dict(self._identity)

    def execute(self, query: str) -> list[dict[str, object]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 8 * 1024 * 1024:
            raise RuntimeError("restore SQL is invalid")
        response = self._request("POST", self._path, {"query": query})
        if not isinstance(response, list):
            raise RuntimeError("restore SQL endpoint returned malformed rows")
        return [dict(row) for row in response if isinstance(row, Mapping)]

    def preflight_empty(self) -> None:
        from scripts.verify_recovery_bundle import _RESTORE_TABLES
        tables = tuple(dict.fromkeys(table for _dataset, table, _renames in _RESTORE_TABLES)) + ("portfolio_cash_ledger_state",)
        checks = " AND ".join(f"(SELECT count(*) = 0 FROM public.{table})" for table in tables)
        query = ("SELECT jsonb_build_object('tables_empty',(" + checks + "),"
                 "'native_migrations_empty',(to_regclass('supabase_migrations.schema_migrations') IS NULL OR "
                 "(SELECT count(*) = 0 FROM supabase_migrations.schema_migrations)),"
                 "'private_ledger_empty',(to_regclass('public.stock_agent_release_migration_ledger') IS NULL OR "
                 "(SELECT count(*) = 0 FROM public.stock_agent_release_migration_ledger))) AS restore_preflight")
        rows = self.execute(query)
        result = rows[0].get("restore_preflight") if len(rows) == 1 else None
        if not isinstance(result, Mapping) or result != {"tables_empty": True, "native_migrations_empty": True, "private_ledger_empty": True}:
            raise RuntimeError("isolated restore preflight requires empty restore tables and migration ledgers")

    def apply_schema(self, schema: Path) -> None:
        resolved = Path(schema).resolve()
        if resolved.name != "schema.sql" or not resolved.is_file() or len(resolved.read_bytes()) > 16 * 1024 * 1024:
            raise RuntimeError("exact bounded schema.sql is required")
        # schema.sql is applied directly; no migration runner or ledger write is used.
        self.execute(resolved.read_text())

    def reproduce_role_shapes(self, roles: object) -> None:
        if not isinstance(roles, list):
            raise RuntimeError("recovery role shapes are unavailable")
        expected = {"stock_agent_dashboard", "stock_agent_dashboard_runtime"}
        by_name = {row.get("role"): row for row in roles if isinstance(row, Mapping)}
        if set(by_name) != expected:
            raise RuntimeError("recovery must contain exactly the two expected role shapes")
        statements: list[str] = []
        for role in sorted(expected):
            row = by_name[role]
            if row.get("login") is not False or row.get("superuser") is not False or row.get("bypass_rls") is not False:
                raise RuntimeError("recovery role shape has unsafe authority")
            statements.append("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') THEN CREATE ROLE %s NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$" % (role, role))
            statements.append(f"ALTER ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            memberships = row.get("memberships")
            grants = row.get("grants")
            if not isinstance(memberships, list) or not isinstance(grants, list):
                raise RuntimeError("recovery role shape is malformed")
            for parent in memberships:
                if not isinstance(parent, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", parent):
                    raise RuntimeError("recovery role membership is invalid")
                statements.append(f"GRANT {parent} TO {role}")
            for grant in grants:
                if not isinstance(grant, str):
                    raise RuntimeError("recovery role grant is invalid")
                # The recovery query renders only a PostgreSQL privilege and a
                # fully qualified object.  Re-parse it before direct SQL.
                match = re.fullmatch(r"([A-Z]+):([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?:grantable=(true|false)", grant)
                if match is None or match.group(1) not in {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "EXECUTE", "USAGE"}:
                    raise RuntimeError("recovery role grant is invalid")
                privilege, object_name, column, grantable = match.groups()
                suffix = " WITH GRANT OPTION" if grantable == "true" else ""
                if column is not None:
                    if privilege not in {"SELECT", "INSERT", "UPDATE", "REFERENCES"}:
                        raise RuntimeError("recovery column grant is invalid")
                    statements.append(f"GRANT {privilege} ({column}) ON {object_name} TO {role}{suffix}")
                else:
                    statements.append(f"GRANT {privilege} ON {object_name} TO {role}{suffix}")
        self.execute("BEGIN;" + ";".join(statements) + ";COMMIT")

    def restore_records(self, records: Mapping[str, list[dict[str, object]]]) -> None:
        from scripts.export_recovery_bundle import _validated_records
        from scripts.verify_recovery_bundle import _RESTORE_TABLES
        normalized = _validated_records(records)
        self.preflight_empty()
        statements = ["BEGIN"]
        run_gateway_ids = {row["id"]: row["gateway_request_id"] for row in normalized["runs"]}
        for dataset, table, renames in _RESTORE_TABLES:
            for source_row in normalized[dataset]:
                row = {renames.get(key, key): value for key, value in source_row.items()}
                if dataset == "runs":
                    row["gateway_request_id"] = None
                encoded = canonical_json(row).replace("'", "''")
                statements.append(f"INSERT INTO public.{table} SELECT * FROM json_populate_record(NULL::public.{table},'{encoded}'::json)")
        for run_id, request_id in run_gateway_ids.items():
            if request_id is not None:
                statements.append("UPDATE public.analysis_runs SET gateway_request_id='%s'::uuid WHERE id='%s'::uuid" % (request_id, run_id))
        ledger = normalized["cash_ledger_state"][0]
        statements.append("UPDATE public.portfolio_cash_ledger_state SET revision='%s'::bigint,updated_at='%s'::timestamptz WHERE singleton=true" % (ledger["revision"], ledger["updated_at"]))
        for row in normalized["schema_version"]:
            version = row["version"].replace("'", "''")
            statement_array = "ARRAY[" + ",".join("'%s'" % item.replace("'", "''") for item in row["statements"]) + "]"
            statements.append("INSERT INTO supabase_migrations.schema_migrations(version,statements) VALUES('%s',%s)" % (version, statement_array))
        statements.append("COMMIT")
        self.execute(";".join(statements))

    def verify_migration_noop(self, schema_versions: object) -> dict[str, list[str]]:
        """Read-only equivalence proof for the migration retry (no runner call)."""
        if not isinstance(schema_versions, list) or not schema_versions:
            raise RuntimeError("restored native migration history is unavailable")
        expected: list[tuple[str, str]] = []
        for row in schema_versions:
            if not isinstance(row, Mapping) or not isinstance(row.get("version"), str) or not re.fullmatch(r"[0-9]{8,12}", row["version"]):
                raise RuntimeError("restored native migration version is invalid")
            statements = row.get("statements")
            if not isinstance(statements, list) or not all(isinstance(item, str) for item in statements):
                raise RuntimeError("restored native migration statements are invalid")
            literal = "ARRAY[" + ",".join("'%s'" % item.replace("'", "''") for item in statements) + "]"
            expected.append((row["version"], literal))
        values = ",".join("('%s',%s)" % (version, statements) for version, statements in expected)
        query = ("WITH expected(version,statements) AS (VALUES " + values + "), stats AS (SELECT "
                 "(SELECT count(*) FROM expected e LEFT JOIN supabase_migrations.schema_migrations m USING(version) WHERE m.version IS NULL) AS missing,"
                 "(SELECT count(*) FROM supabase_migrations.schema_migrations m LEFT JOIN expected e USING(version) WHERE e.version IS NULL) AS extra,"
                 "(SELECT count(*) FROM expected e JOIN supabase_migrations.schema_migrations m USING(version) WHERE m.statements IS DISTINCT FROM e.statements) AS mismatch) "
                 "SELECT jsonb_build_object('missing',missing,'extra',extra,'mismatch',mismatch) AS migration_noop FROM stats")
        rows = self.execute(query)
        result = rows[0].get("migration_noop") if len(rows) == 1 else None
        if result != {"missing": 0, "extra": 0, "mismatch": 0}:
            raise RuntimeError("native migration retry is not a no-op")
        return {"applied": [], "skipped": [version for version, _statements in expected]}


class ManagedProjectProvisioner:
    """Creates a single disposable project only after the free-slot proof."""

    _HEALTHY = {"ACTIVE_HEALTHY", "HEALTHY"}
    _ACTIVE = _HEALTHY | {"ACTIVE", "CREATING", "RESTORING", "UPGRADING", "PAUSING", "PAUSED"}

    def __init__(self, request: ManagementRequest, production_project_ref: str, *,
                 random_bytes: Callable[[int], bytes] = secrets.token_bytes,
                 sleep: Callable[[float], None] | None = None, max_health_checks: int = 12):
        self._request = request
        self.production_ref = _project_ref(production_project_ref, "production")
        self._random_bytes = random_bytes
        self._sleep = sleep or __import__("time").sleep
        if not isinstance(max_health_checks, int) or not 1 <= max_health_checks <= 30:
            raise RuntimeError("bounded health check count is required")
        self._max_health_checks = max_health_checks
        self.created_project_ref: str | None = None

    def _production(self) -> dict[str, object]:
        value = self._request("GET", f"/v1/projects/{self.production_ref}", None)
        if not isinstance(value, Mapping) or value.get("ref") != self.production_ref:
            raise RuntimeError("production project metadata is unavailable")
        if not isinstance(value.get("organization_id"), str) or not isinstance(value.get("region"), str):
            raise RuntimeError("production organization or region is unavailable")
        return dict(value)

    def create_and_wait(self) -> str:
        production = self._production()
        projects = self._request("GET", "/v1/projects", None)
        if not isinstance(projects, list) or not all(isinstance(project, Mapping) for project in projects):
            raise RuntimeError("project inventory is unavailable")
        active = [project for project in projects if project.get("status") in self._ACTIVE]
        if len(active) != 1 or active[0].get("ref") != self.production_ref:
            raise RuntimeError("exactly one active project is required for a free restore slot")
        # This password exists only in the create request.  It is deliberately
        # not retained on the instance, in receipts, or in raised errors.
        password = base64.urlsafe_b64encode(self._random_bytes(36)).decode().rstrip("=")
        name_suffix = base64.b32encode(self._random_bytes(8)).decode().lower().rstrip("=")
        created = self._request("POST", "/v1/projects", {
            "name": f"stocks-recovery-{name_suffix}",
            "organization_id": production["organization_id"], "region": production["region"], "db_pass": password,
        })
        if not isinstance(created, Mapping):
            raise RuntimeError("temporary project creation returned invalid metadata")
        restore_ref = _project_ref(created.get("ref"), "created restore")
        if (restore_ref == self.production_ref or created.get("organization_id") != production["organization_id"]
                or created.get("region") != production["region"]):
            raise RuntimeError("temporary project does not match the production organization and region")
        self.created_project_ref = restore_ref
        for attempt in range(self._max_health_checks):
            status = self._request("GET", f"/v1/projects/{restore_ref}", None)
            if (isinstance(status, Mapping) and status.get("ref") == restore_ref
                    and status.get("organization_id") == production["organization_id"]
                    and status.get("region") == production["region"] and status.get("status") in self._HEALTHY):
                return restore_ref
            if attempt + 1 < self._max_health_checks:
                self._sleep(min(5 * (attempt + 1), 30))
        raise RuntimeError("temporary restore project did not become healthy within the bounded wait")

    def cleanup(self) -> dict[str, object]:
        if self.created_project_ref is None:
            return {"attempted": False, "deleted": False, "retained_project_ref": None}
        ref = self.created_project_ref
        try:
            self._request("DELETE", f"/v1/projects/{ref}", None)
        except Exception as error:
            # No API error text is surfaced; it may contain sensitive data.
            return {"attempted": True, "deleted": False, "retained_project_ref": ref,
                    "error": type(error).__name__}
        return {"attempted": True, "deleted": True, "retained_project_ref": None}


def _digest(value: object, label: str, *, length: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise RuntimeError(f"{label} is invalid")
    return value


def write_restore_receipt(destination: Path, values: Mapping[str, object]) -> Path:
    """Write a compact allowlisted receipt; no input rows or secrets cross it."""
    destination = Path(destination)
    if ".." in destination.parts or destination.name != destination.name.strip() or not destination.name.endswith(".json"):
        raise RuntimeError("receipt path is unsafe")
    restore = values.get("restore")
    cleanup = values.get("cleanup")
    migration = values.get("migration_retry")
    workflow = values.get("workflow")
    artifacts = values.get("artifacts")
    if (not isinstance(restore, Mapping) or not isinstance(cleanup, Mapping) or not isinstance(migration, Mapping)
            or not isinstance(workflow, Mapping) or not isinstance(artifacts, Mapping)):
        raise RuntimeError("restore receipt fields are incomplete")
    production = _project_ref(values.get("production_project_ref"), "production")
    temporary = _project_ref(values.get("restore_project_ref"), "restore")
    if temporary == production:
        raise RuntimeError("restore receipt identities are not isolated")
    if restore.get("status") != "verified" or restore.get("isolated") is not True or restore.get("restore_applied") is not True or restore.get("record_set_count") != 26:
        raise RuntimeError("restore receipt result is incomplete")
    if not isinstance(cleanup.get("attempted"), bool) or not isinstance(cleanup.get("deleted"), bool):
        raise RuntimeError("cleanup receipt is incomplete")
    retained = cleanup.get("retained_project_ref")
    if cleanup["deleted"] is False and retained != temporary:
        raise RuntimeError("failed cleanup must retain the exact temporary identity")
    if cleanup["deleted"] is True and retained is not None:
        raise RuntimeError("successful cleanup must not retain a project identity")
    clean_artifacts: dict[str, dict[str, str]] = {}
    for name, item in artifacts.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", name)
                or not isinstance(item, Mapping) or set(item) - {"path", "uploaded_name", "sha256"}
                or not isinstance(item.get("sha256"), str)):
            raise RuntimeError("artifact receipt is invalid")
        location = item.get("uploaded_name", item.get("path"))
        if not isinstance(location, str) or not location or len(location) > 256 or ".." in Path(location).parts:
            raise RuntimeError("artifact receipt location is invalid")
        clean_artifacts[name] = {"location": location, "sha256": _digest(item["sha256"], "artifact hash")}
    receipt = {
        "format": "stocks-managed-isolated-restore-v1",
        "main_sha": _digest(values.get("main_sha"), "main SHA", length=40),
        "main_tree": _digest(values.get("main_tree"), "main tree", length=40),
        "started_at": values.get("started_at"), "completed_at": values.get("completed_at"),
        "production_project_ref": production, "restore_project_ref": temporary,
        "before_root_hash": _digest(values.get("before_root_hash"), "before root hash"),
        "after_root_hash": _digest(values.get("after_root_hash"), "after root hash"),
        "restore": {"status": "verified", "isolated": True, "restore_applied": True, "record_set_count": 26},
        "migration_retry": {"applied": list(migration.get("applied", [])), "skipped": list(migration.get("skipped", []))},
        "artifacts": clean_artifacts,
        "workflow": {"run_id": str(workflow.get("run_id", "")), "attempt": str(workflow.get("attempt", ""))},
        "cleanup": {"attempted": cleanup["attempted"], "deleted": cleanup["deleted"], "retained_project_ref": retained},
    }
    if receipt["before_root_hash"] != receipt["after_root_hash"]:
        raise RuntimeError("before and after production recovery hashes differ")
    if not all(isinstance(receipt[key], str) and receipt[key].endswith("Z") for key in ("started_at", "completed_at")):
        raise RuntimeError("receipt timestamps must be UTC")
    raw = canonical_json(receipt).encode() + b"\n"
    if len(raw) > 16 * 1024:
        raise RuntimeError("restore receipt exceeds its bounded size")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(raw)
    destination.chmod(0o600)
    return destination


def write_failed_restore_receipt(destination: Path, *, main_sha: str, main_tree: str, started_at: str,
                                 completed_at: str, production_ref: str, restore_ref: str | None,
                                 cleanup: Mapping[str, object], error: BaseException) -> Path:
    """Failure receipts retain only the safe project identity needed for cleanup."""
    production = _project_ref(production_ref, "production")
    temporary = None if restore_ref is None else _project_ref(restore_ref, "restore")
    retained = cleanup.get("retained_project_ref")
    if retained is not None and retained != temporary:
        raise RuntimeError("failure cleanup identity is invalid")
    receipt = {
        "format": "stocks-managed-isolated-restore-v1", "status": "failed",
        "main_sha": _digest(main_sha, "main SHA", length=40), "main_tree": _digest(main_tree, "main tree", length=40),
        "started_at": started_at, "completed_at": completed_at, "production_project_ref": production,
        "restore_project_ref": temporary,
        "failure_class": type(error).__name__,
        "cleanup": {"attempted": cleanup.get("attempted") is True, "deleted": cleanup.get("deleted") is True,
                    "retained_project_ref": retained},
    }
    raw = canonical_json(receipt).encode() + b"\n"
    if len(raw) > 8 * 1024:
        raise RuntimeError("failed restore receipt exceeds its bounded size")
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(raw); destination.chmod(0o600)
    return destination


def _git_value(repository: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repository, check=False, capture_output=True, text=True, timeout=30)
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise RuntimeError("exact checked-out main Git identity is unavailable")
    return value


class ManagedIsolatedRestoreDrill:
    """Runs the protected drill.  All database transport stays in Management API."""

    def __init__(self, request: ManagementRequest, production_project_ref: str, *, output_dir: Path,
                 repository: Path, workflow_run_id: str, workflow_attempt: str):
        self.request = request
        self.production_ref = _project_ref(production_project_ref, "production")
        self.output_dir = Path(output_dir)
        self.repository = Path(repository).resolve()
        self.workflow_run_id, self.workflow_attempt = str(workflow_run_id), str(workflow_attempt)

    def _crypt_command(self, operation: str) -> str:
        script = self.repository / "scripts" / "recovery_crypt.py"
        if not script.is_file():
            raise RuntimeError("recovery cipher script is unavailable")
        return f"{json.dumps(sys.executable)} {json.dumps(str(script))} {operation} {{input}} {{output}}"

    def run(self) -> dict[str, object]:
        from scripts.export_recovery_bundle import export_recovery_bundle, sha256
        from scripts.verify_recovery_bundle import verify_recovery_bundle

        main_sha = _git_value(self.repository, "rev-parse", "HEAD")
        main_tree = _git_value(self.repository, "rev-parse", "HEAD^{tree}")
        # Workflow binds main before calling this; repeat the local invariant.
        if _git_value(self.repository, "rev-parse", "main") != main_sha:
            raise RuntimeError("checkout is not current main")
        started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        first = self.output_dir / "production-before.enc"
        second = self.output_dir / "production-after.enc"
        receipt_path = self.output_dir / "managed-isolated-restore-receipt.json"
        provisioner = ManagedProjectProvisioner(self.request, self.production_ref)
        failure: Exception | None = None
        cleanup: dict[str, object] = {"attempted": False, "deleted": False, "retained_project_ref": None}
        values: dict[str, object] | None = None
        try:
            restore_ref = provisioner.create_and_wait()
            production = ManagedReadOnlyRecoverySource(self.request, self.production_ref)
            # This first export pins the production count comparison to one
            # canonical management read-only snapshot.
            export_recovery_bundle(production, first, encrypt_command=self._crypt_command("encrypt"),
                                   decrypt_command=self._crypt_command("decrypt"))
            records = production.read_records()
            target = ManagedRestoreTarget(self.request, restore_ref, self.production_ref,
                                          created_project_ref=provisioner.created_project_ref)
            target.apply_schema(self.repository / "sql" / "schema.sql")
            target.preflight_empty()
            target.reproduce_role_shapes(records["roles"])
            restored = ManagedReadOnlyRecoverySource(self.request, restore_ref, isolated_guard=True)
            verification = verify_recovery_bundle(first, restored, production_source=production,
                                                  decrypt_command=self._crypt_command("decrypt"), restore_target=target)
            migration_retry = target.verify_migration_noop(records["schema_version"])
            # A second independently fetched production bundle closes the
            # source-count race: its root must equal the pre-restore export.
            after_source = ManagedReadOnlyRecoverySource(self.request, self.production_ref)
            export_recovery_bundle(after_source, second, encrypt_command=self._crypt_command("encrypt"),
                                   decrypt_command=self._crypt_command("decrypt"))
            first_sidecar = json.loads(first.with_suffix(".enc.receipt.json").read_text())
            second_sidecar = json.loads(second.with_suffix(".enc.receipt.json").read_text())
            if first_sidecar["root_hash"] != second_sidecar["root_hash"]:
                raise RuntimeError("production recovery root changed during isolated restore drill")
            values = {
                "main_sha": main_sha, "main_tree": main_tree, "started_at": started,
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "production_project_ref": self.production_ref, "restore_project_ref": restore_ref,
                "before_root_hash": first_sidecar["root_hash"], "after_root_hash": second_sidecar["root_hash"],
                "restore": verification, "migration_retry": migration_retry,
                "artifacts": {
                    "production_before": {"path": first.name, "sha256": sha256(first.read_bytes())},
                    "production_after": {"path": second.name, "sha256": sha256(second.read_bytes())},
                    "production_before_sidecar": {"path": first.with_suffix(".enc.receipt.json").name, "sha256": sha256(first.with_suffix(".enc.receipt.json").read_bytes())},
                    "production_after_sidecar": {"path": second.with_suffix(".enc.receipt.json").name, "sha256": sha256(second.with_suffix(".enc.receipt.json").read_bytes())},
                },
                "workflow": {"run_id": self.workflow_run_id, "attempt": self.workflow_attempt},
            }
        except Exception as error:
            failure = error
        finally:
            cleanup = provisioner.cleanup()
            if values is not None:
                values["cleanup"] = cleanup
                write_restore_receipt(receipt_path, values)
            else:
                write_failed_restore_receipt(
                    receipt_path, main_sha=main_sha, main_tree=main_tree, started_at=started,
                    completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    production_ref=self.production_ref, restore_ref=provisioner.created_project_ref,
                    cleanup=cleanup, error=failure or RuntimeError("unknown restore failure"),
                )
        if failure is not None:
            raise RuntimeError("managed isolated restore drill failed") from failure
        if cleanup["deleted"] is not True:
            raise RuntimeError("temporary restore project cleanup failed; retained identity is in the receipt")
        return {"receipt": str(receipt_path), "status": "verified"}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True)
    args = parser.parse_args()
    # Both values remain process environment only; neither is accepted by argv.
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if not os.environ.get("RELEASE_RECOVERY_KEY"):
        raise RuntimeError("recovery encryption key is required")
    api = SupabaseManagementApi(token)
    result = ManagedIsolatedRestoreDrill(api, args.production_project_ref, output_dir=args.output_dir,
                                         repository=Path(__file__).resolve().parents[1],
                                         workflow_run_id=args.workflow_run_id,
                                         workflow_attempt=args.workflow_attempt).run()
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
