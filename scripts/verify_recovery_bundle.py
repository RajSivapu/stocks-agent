#!/usr/bin/env python3
"""Query a guarded isolated restore and reconcile the encrypted production snapshot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Mapping, Protocol, runtime_checkable
from urllib.parse import unquote, urlparse

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_recovery_bundle import (
    RecoveryDataSource, _validated_records, decrypt_verified, exact_file, sha256, source_identity,
)


@runtime_checkable
class RecoveryRestoreTarget(Protocol):
    def identity(self) -> Mapping[str, object]: ...
    def restore_records(self, records: Mapping[str, list[dict[str, object]]]) -> None: ...


_RESTORE_TABLES = (
    ("release_migration_ledger", "stock_agent_release_migration_ledger", {}),
    ("policies", "market_policy_config", {}),
    ("holdings", "holdings", {"average_cost": "avg_cost"}),
    ("transactions", "transactions", {"quantity": "qty"}),
    ("commands", "portfolio_commands", {}),
    ("runs", "analysis_runs", {"phase": "kind"}),
    ("gateway_requests", "market_gateway_requests", {}),
    ("intelligence_runs", "market_intelligence_runs", {}),
    ("reference_chunk_receipts", "market_reference_chunk_receipts", {}),
    ("reference_manifests", "market_reference_manifests", {}),
    ("security_reference_revisions", "market_security_reference_revisions", {}),
    ("reference_finalization_seals", "market_reference_finalization_seals", {}),
    ("reference_snapshot_memberships", "market_reference_snapshot_memberships", {}),
    ("reference_run_bindings", "market_reference_run_bindings", {}),
    ("discovery_stage_tasks", "market_discovery_stage_tasks", {}),
    ("theme_episode_revisions", "market_theme_episode_revisions", {}),
    ("exposure_facts", "market_exposure_facts", {}),
    ("research_nominations", "market_research_nominations", {}),
    ("intelligence_run_events", "market_intelligence_run_events", {}),
    ("source_quota_reservations", "market_source_quota_reservations", {}),
    ("collection_checkpoints", "market_collection_checkpoints", {}),
    ("collection_checkpoint_history", "market_collection_checkpoint_history", {}),
    ("collection_completions", "market_intelligence_collection_completions", {}),
    ("packets", "market_evidence_packets", {}),
    ("decision_evaluations", "decision_evaluations", {}),
    ("policy_comparisons", "market_policy_comparisons", {}),
    ("reports", "market_reports", {}),
    ("report_origins", "market_report_request_origins", {}),
    ("publications", "market_report_publications", {}),
    ("evaluation_publications", "market_publications", {}),
    ("command_acknowledgements", "portfolio_command_acknowledgements", {}),
    ("cash_snapshots", "reconciled_cash_snapshots", {}),
    ("run_terminal_outcomes", "market_run_terminal_outcomes", {}),
)


def ordered_restore_rows(dataset: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return deterministic parent-first rows for self-referencing ledgers."""
    if dataset != "reference_finalization_seals":
        return rows
    pending = list(rows)
    ordered: list[dict[str, object]] = []
    restored: set[object] = set()
    while pending:
        ready = [
            row for row in pending
            if row.get("predecessor_manifest_id") is None
            or row.get("predecessor_manifest_id") in restored
        ]
        if not ready:
            raise ValueError("reference finalization predecessor chain is not restorable")
        ready.sort(key=lambda row: str(row.get("manifest_id")))
        for row in ready:
            pending.remove(row)
            ordered.append(row)
            restored.add(row.get("manifest_id"))
    return ordered


class PostgresIsolatedRestoreTarget:
    """Write a verified snapshot only to an explicitly different Supabase project."""

    def __init__(self, database_url: str, project_ref: str, production_project_ref: str):
        parsed = urlparse(database_url)
        direct = parsed.hostname == f"db.{project_ref}.supabase.co" and unquote(parsed.username or "") == "postgres"
        pooler = (bool(re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", parsed.hostname or ""))
                  and unquote(parsed.username or "") == f"postgres.{project_ref}")
        if (not re.fullmatch(r"[a-z0-9]{20}", project_ref)
                or not re.fullmatch(r"[a-z0-9]{20}", production_project_ref)
                or project_ref == production_project_ref or parsed.scheme not in {"postgres", "postgresql"}
                or not (direct or pooler) or parsed.port not in {None, 5432}
                or parsed.path != "/postgres" or not parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("restore writer must identify the exact guarded isolated project")
        self._url = database_url
        self.project_ref = project_ref
        self.connection = None

    def __enter__(self):
        try:
            self.connection = psycopg.connect(
                self._url, row_factory=dict_row, sslmode="verify-full", connect_timeout=15,
                autocommit=True,
            )
            row = self.connection.execute(
                "SELECT current_user AS role,current_database() AS database,"
                "inet_server_addr()::text AS server,inet_server_port() AS port,"
                "current_setting('transaction_read_only') AS read_only"
            ).fetchone()
            if (row["role"] != "postgres" or row["read_only"] != "off" or not row["server"] or not row["database"]):
                raise RuntimeError("isolated restore writer identity or authority is unavailable")
            self._identity = {
                "project_ref": self.project_ref,
                "connection_id": sha256(f"{row['server']}:{row['port']}/{row['database']}".encode()),
                "read_only": False,
                "restore_capable": True,
                "isolated_guard": True,
            }
            return self
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def __exit__(self, *_exc):
        if self.connection is not None:
            self.connection.close()

    def identity(self):
        if self.connection is None or self.connection.closed:
            raise RuntimeError("isolated restore writer is not connected")
        return dict(self._identity)

    def restore_records(self, records: Mapping[str, list[dict[str, object]]]) -> None:
        self.identity()
        restore_recovery_records(self.connection, records, isolated_guard=True)


def restore_recovery_records(connection, records: Mapping[str, object], *, isolated_guard: bool) -> None:
    """Insert one validated snapshot into an explicitly isolated, empty schema."""
    if isolated_guard is not True:
        raise RuntimeError("explicit isolated restore guard is required")
    normalized = _validated_records(records)
    tables = [table for _dataset, table, _renames in _RESTORE_TABLES]
    def scalar(row):
        return next(iter(row.values())) if isinstance(row, Mapping) else row[0]
    with connection.transaction():
        for table in tables:
            if scalar(connection.execute(f"SELECT count(*) FROM public.{table}").fetchone()):
                raise RuntimeError(f"isolated restore table {table} is not empty")
        if scalar(connection.execute("SELECT count(*) FROM supabase_migrations.schema_migrations").fetchone()):
            raise RuntimeError("isolated restore migration ledger is not empty")

        run_gateway_ids = {row["id"]: row["gateway_request_id"] for row in normalized["runs"]}
        for dataset, table, renames in _RESTORE_TABLES:
            for source_row in ordered_restore_rows(dataset, normalized[dataset]):
                row = {renames.get(key, key): value for key, value in source_row.items()}
                if dataset == "runs":
                    row["gateway_request_id"] = None
                connection.execute(
                    f"INSERT INTO public.{table} SELECT * FROM json_populate_record(NULL::public.{table}, %s::json)",
                    (json.dumps(row, ensure_ascii=False, separators=(",", ":")),),
                )
        for run_id, request_id in run_gateway_ids.items():
            if request_id is not None:
                connection.execute(
                    "UPDATE public.analysis_runs SET gateway_request_id=%s::uuid WHERE id=%s::uuid",
                    (request_id, run_id),
                )
        ledger = normalized["cash_ledger_state"][0]
        updated = connection.execute(
            "UPDATE public.portfolio_cash_ledger_state SET revision=%s::bigint,updated_at=%s::timestamptz WHERE singleton=true",
            (ledger["revision"], ledger["updated_at"]),
        ).rowcount
        if updated != 1:
            connection.execute(
                "INSERT INTO public.portfolio_cash_ledger_state(singleton,revision,updated_at) VALUES(true,%s::bigint,%s::timestamptz)",
                (ledger["revision"], ledger["updated_at"]),
            )
        for row in normalized["schema_version"]:
            connection.execute(
                "INSERT INTO supabase_migrations.schema_migrations(version,statements) VALUES(%s,%s)",
                (row["version"], row["statements"]),
            )
        sequence = scalar(connection.execute("SELECT pg_get_serial_sequence('public.transactions','id')").fetchone())
        if sequence and normalized["transactions"]:
            connection.execute(
                "SELECT setval(%s,(SELECT max(id) FROM public.transactions),true)", (sequence,),
            )


def verify_recovery_bundle(artifact: Path, restored_source: RecoveryDataSource, *, production_source: RecoveryDataSource,
                           decrypt_command: str | None = None,
                           restore_target: RecoveryRestoreTarget | None = None) -> dict[str, object]:
    if not decrypt_command:
        raise RuntimeError("decrypt verification command is required")
    production = source_identity(production_source)
    isolated = source_identity(restored_source)
    if (isolated.get("isolated_guard") is not True or isolated["project_ref"] == production["project_ref"]
            or isolated["connection_id"] == production["connection_id"]):
        raise RuntimeError("explicitly guarded isolated restored database identity is required")
    exact_file(artifact)
    receipt_path = artifact.with_suffix(artifact.suffix + ".receipt.json")
    with tempfile.TemporaryDirectory(prefix="stocks-recovery-verify-") as temporary:
        sidecar = json.loads(exact_file(receipt_path).read_text())
        if not isinstance(sidecar, dict) or set(sidecar) != {"format", "root_hash", "artifact_sha256"} or sidecar.get("artifact_sha256") != sha256(artifact.read_bytes()):
            raise RuntimeError("encrypted recovery artifact hash mismatch")
        manifest, exported = decrypt_verified(artifact, decrypt_command, Path(temporary).resolve())
        if manifest["root_hash"] != sidecar["root_hash"] or manifest["format"] != sidecar["format"]:
            raise RuntimeError("decrypted recovery root hash mismatch")
        if manifest["production_identity"] != production:
            raise RuntimeError("recovery production identity mismatch")
        restore_applied = False
        if restore_target is not None:
            target = dict(restore_target.identity())
            if (target.get("isolated_guard") is not True or target.get("restore_capable") is not True
                    or target.get("read_only") is not False
                    or target.get("project_ref") != isolated["project_ref"]
                    or target.get("connection_id") != isolated["connection_id"]):
                raise RuntimeError("restore target does not match the guarded isolated database")
            restore_target.restore_records(exported)
            restore_applied = True
            refresh = getattr(restored_source, "refresh_snapshot", None)
            if callable(refresh):
                refresh()
        restored = _validated_records(restored_source.read_records())
        if restored != exported or restored_source.counts() != manifest["counts"] or production_source.counts() != manifest["counts"]:
            raise RuntimeError("isolated restored records or production counts do not match the recovery snapshot")
    return {"status": "verified", "isolated": True, "restore_applied": restore_applied,
            "record_set_count": len(exported), "root_hash": manifest["root_hash"]}


def main() -> int:
    from scripts.protected_evidence import PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--restore-project-ref", required=True)
    parser.add_argument("--allow-isolated-restore", action="store_true", required=True)
    parser.add_argument("--decrypt-command", required=True)
    args = parser.parse_args()
    if args.production_project_ref == args.restore_project_ref:
        raise RuntimeError("restore project must differ from production")
    with PostgresReadOnlySource(os.environ.get("RECOVERY_PRODUCTION_DATABASE_URL", ""), args.production_project_ref) as production:
        with PostgresReadOnlySource(os.environ.get("RECOVERY_RESTORE_DATABASE_URL", ""), args.restore_project_ref,
                                   isolated_guard=args.allow_isolated_restore, production_project_ref=args.production_project_ref) as restored:
            with PostgresIsolatedRestoreTarget(
                os.environ.get("RECOVERY_RESTORE_WRITER_DATABASE_URL", ""),
                args.restore_project_ref, args.production_project_ref,
            ) as target:
                print(json.dumps(verify_recovery_bundle(
                    args.artifact, restored, production_source=production,
                    decrypt_command=args.decrypt_command, restore_target=target,
                ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
