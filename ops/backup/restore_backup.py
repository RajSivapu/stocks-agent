#!/usr/bin/env python3
"""Decrypt and restore a verified stock-agent archive into an empty staging project."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from ops.backup.export_backup import BackupFailed, collect_archive
from ops.backup.verify_archive import (
    DATASET_SPECS,
    SCHEMA_VERSION,
    ArchiveValidationError,
    build_archive,
    canonical_json_bytes,
    verify_archive,
)
from scripts.invite_user import _request, canonical_project_url


PROJECT_REF_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
MAX_CIPHERTEXT_BYTES = 513 * 1024 * 1024


class RestoreRejected(RuntimeError):
    """The requested restore is unsafe, incomplete, or unverifiable."""


def assert_staging_restore(
    *,
    environment: str,
    production_triggers_paused: bool,
    project_ref: str,
    confirmation: str,
) -> None:
    if environment != "staging" or not production_triggers_paused:
        raise RestoreRejected("restore requires staging with production triggers paused")
    if (
        not PROJECT_REF_RE.fullmatch(project_ref)
        or "prod" in project_ref.lower()
        or "production" in project_ref.lower()
        or confirmation != f"RESTORE STAGING {project_ref}"
    ):
        raise RestoreRejected("restore confirmation or staging project reference is invalid")


def decrypt_archive(
    ciphertext: Path,
    *,
    identity: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if (
        not ciphertext.is_file()
        or not identity.is_file()
        or ciphertext.stat().st_size <= 24
        or ciphertext.stat().st_size > MAX_CIPHERTEXT_BYTES
    ):
        raise RestoreRejected("restore input is missing or oversized")
    if stat_mode(identity) & 0o077:
        raise RestoreRejected("age identity file must not be group/world accessible")
    with tempfile.TemporaryDirectory(prefix="stock-agent-restore-") as directory_name:
        directory = Path(directory_name)
        os.chmod(directory, 0o700)
        plaintext = directory / "archive.json"
        try:
            result = runner(
                [
                    "age",
                    "--decrypt",
                    "--identity",
                    str(identity.resolve()),
                    "--output",
                    str(plaintext),
                    str(ciphertext.resolve()),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not plaintext.is_file():
                raise RestoreRejected("age decryption failed")
            os.chmod(plaintext, 0o600)
            if plaintext.stat().st_size > 512 * 1024 * 1024:
                raise RestoreRejected("decrypted archive exceeds the restore limit")
            try:
                value = json.loads(plaintext.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RestoreRejected("decrypted archive is malformed") from error
            return verify_archive(value)
        finally:
            _wipe_file(plaintext)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _wipe_file(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            size = path.stat().st_size
            with path.open("r+b", buffering=0) as output:
                output.write(b"\0" * size)
                output.flush()
                os.fsync(output.fileno())
    finally:
        path.unlink(missing_ok=True)


def build_identity_remap(
    archive: Mapping[str, Any],
    identity_creator: Callable[[str], str | UUID],
) -> dict[str, str]:
    verified = verify_archive(archive)
    mapping: dict[str, str] = {}
    created: set[str] = set()
    for row in verified["identity_recovery"]:
        old_owner = str(row["owner_id"])
        try:
            new_owner = str(UUID(str(identity_creator(str(row["email"])))))
        except (TypeError, ValueError) as error:
            raise RestoreRejected("identity provider returned an invalid owner UUID") from error
        if new_owner in created:
            raise RestoreRejected("identity provider returned a duplicate owner UUID")
        created.add(new_owner)
        mapping[old_owner] = new_owner
    profile_owners = {str(row["id"]) for row in verified["datasets"]["profiles"]["rows"]}
    if set(mapping) != profile_owners:
        raise RestoreRejected("identity recovery map does not cover every profile")
    return mapping


def remap_archive_owners(archive: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    remapped = copy.deepcopy(dict(archive))
    for row in remapped["identity_recovery"]:
        row["owner_id"] = mapping[str(row["owner_id"])]
    for dataset, entry in remapped["datasets"].items():
        for row in entry["rows"]:
            if dataset != "deletion_tombstones" and row.get("owner_id") is not None:
                row["owner_id"] = mapping[str(row["owner_id"])]
            if dataset == "profiles":
                row["id"] = mapping[str(row["id"])]
            if dataset == "app_admins":
                row["user_id"] = mapping[str(row["user_id"])]
            if dataset == "transactions" and row.get("actor_id") is not None:
                row["actor_id"] = mapping[str(row["actor_id"])]
    return remapped


def harden_restored_rows(
    datasets: Mapping[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    hardened = copy.deepcopy(dict(datasets))
    for row in hardened.get("agent_connections", []):
        row["status"] = "disabled"
        row["last_handshake_at"] = None
        for key in ("inbound_token_digest", "outbound_trigger_secret_id", "trigger_url"):
            row.pop(key, None)
    for row in hardened.get("portfolio_commands", []):
        if row.get("status") in {"submitted", "previewed", "confirmed"}:
            row["status"] = "cancelled"
            row["preview_digest"] = row.get("preview_digest") or row.get("input_digest")
            row["error_code"] = "RESTORE_CANCELLED"
    for row in hardened.get("market_gateway_requests", []):
        if row.get("status") == "claimed":
            row["status"] = "failed"
            row["finished_at"] = row.get("finished_at") or row.get("claimed_at") or row.get("created_at")
    for row in hardened.get("analysis_runs", []):
        if row.get("status") == "running":
            row["status"] = "failed"
            row["finished_at"] = row.get("finished_at") or row.get("started_at")
            row["error"] = "RESTORE_INTERRUPTED"
    for row in hardened.get("operational_events", []):
        if row.get("status") in {"open", "notified"}:
            row["status"] = "resolved"
    for dataset in ("market_publications", "operational_alerts"):
        for row in hardened.get(dataset, []):
            if row.get("status") in {"ready", "sending"}:
                row["status"] = "suppressed"
                row["lease_token"] = None
                row["sending_started_at"] = None
                if dataset == "market_publications":
                    row["error"] = "RESTORE_SUPPRESSED"
                else:
                    row["error_code"] = None
    return hardened


class SupabaseIdentityAdmin:
    def __init__(self, project_url: str, service_role_key: str, *, opener: Callable[..., Any]):
        self.project_url = canonical_project_url(project_url)
        if len(service_role_key) < 16 or len(service_role_key) > 16_384:
            raise RestoreRejected("staging Auth credential is malformed")
        self.service_role_key = service_role_key
        self.opener = opener

    def create(self, email: str) -> str:
        result = _request(
            f"{self.project_url}/auth/v1/admin/users",
            self.service_role_key,
            method="POST",
            body={"email": email, "email_confirm": True, "user_metadata": {"recovery_restore": True}},
            opener=self.opener,
        )
        candidate = result.get("id")
        if candidate is None and isinstance(result.get("user"), dict):
            candidate = result["user"].get("id")
        try:
            return str(UUID(str(candidate)))
        except (TypeError, ValueError) as error:
            raise RestoreRejected("staging Auth did not return a user UUID") from error

    def delete(self, owner_id: str) -> None:
        _request(
            f"{self.project_url}/auth/v1/admin/users/{UUID(owner_id)}",
            self.service_role_key,
            method="DELETE",
            body=None,
            opener=self.opener,
            parse_response=False,
        )


def _target_catalog(connection: psycopg.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT machine.backup_export_catalog(%s::jsonb)",
        (json.dumps({"schema_version": SCHEMA_VERSION}),),
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RestoreRejected("staging schema does not expose the recovery contract")
    included = {
        item["name"]
        for item in row[0].get("tables", [])
        if item.get("disposition") == "include"
    }
    if included != set(DATASET_SPECS):
        raise RestoreRejected("staging schema version does not match the archive contract")
    return row[0]


def _truncate_staging(connection: psycopg.Connection, catalog: Mapping[str, Any]) -> None:
    tables = sorted(item["name"] for item in catalog["tables"])
    if not tables:
        raise RestoreRejected("staging schema catalog is empty")
    statement = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier("app", name) for name in tables)
    )
    connection.execute(statement)


def _insert_dataset(
    connection: psycopg.Connection,
    dataset: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    columns = sorted({key for row in rows for key in row})
    if any(set(row) != set(columns) for row in rows):
        raise RestoreRejected(f"{dataset} rows do not share one column shape")
    target = sql.Identifier("app", dataset)
    column_list = sql.SQL(", ").join(map(sql.Identifier, columns))
    source_list = sql.SQL(", ").join(sql.Identifier("restored", name) for name in columns)
    override = sql.SQL(" OVERRIDING SYSTEM VALUE") if dataset == "app_api_audit_events" else sql.SQL("")
    query = sql.SQL(
        "INSERT INTO {} ({}){} SELECT {} FROM jsonb_populate_recordset(NULL::{}, %s::jsonb) AS restored"
    ).format(target, column_list, override, source_list, target)
    connection.execute(query, (json.dumps(rows, separators=(",", ":")),))


def restore_database(
    connection: psycopg.Connection,
    *,
    remapped_archive: Mapping[str, Any],
) -> dict[str, Any]:
    catalog = _target_catalog(connection)
    rows = {
        name: copy.deepcopy(remapped_archive["datasets"][name]["rows"])
        for name in DATASET_SPECS
    }
    rows = harden_restored_rows(rows)
    identities = copy.deepcopy(remapped_archive["identity_recovery"])
    expected = build_archive(
        datasets=rows,
        identity_rows=identities,
        exported_at=datetime.now(timezone.utc),
    )
    connection.execute("SET LOCAL session_replication_role = replica")
    _truncate_staging(connection, catalog)
    _insert_dataset(connection, "deletion_tombstones", rows["deletion_tombstones"])
    for dataset in DATASET_SPECS:
        if dataset != "deletion_tombstones":
            _insert_dataset(connection, dataset, rows[dataset])
    actual = collect_archive(connection, exported_at=datetime.now(timezone.utc))
    for digest in ("identity_digest", "relationship_digest", "projection_digest"):
        if actual[digest] != expected[digest]:
            raise RestoreRejected(f"staging restore {digest} verification failed")
    expected_counts = {name: entry["count"] for name, entry in expected["datasets"].items()}
    actual_counts = {name: entry["count"] for name, entry in actual["datasets"].items()}
    if actual_counts != expected_counts:
        raise RestoreRejected("staging restore row-count verification failed")
    connection.execute(
        """
        INSERT INTO app.backup_restore_receipts(
          archive_digest, source_exported_at, row_counts,
          relationship_digest, projection_digest, status
        ) VALUES (%s, %s, %s::jsonb, %s, %s, 'verified')
        """,
        (
            remapped_archive["archive_digest"],
            remapped_archive["exported_at"],
            json.dumps(actual_counts, sort_keys=True),
            actual["relationship_digest"],
            actual["projection_digest"],
        ),
    )
    return {
        "status": "verified",
        "archive_digest": remapped_archive["archive_digest"],
        "row_counts": actual_counts,
        "requires_provider_reconnect": True,
        "requires_runtime_secret_rotation": True,
        "requires_telegram_webhook_registration": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciphertext", type=Path, required=True)
    parser.add_argument("--age-identity", type=Path, required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--production-triggers-paused", action="store_true")
    parser.add_argument("--database-url-env", default="STAGING_POSTGRES_URL")
    parser.add_argument("--supabase-url-env", default="STAGING_SUPABASE_URL")
    parser.add_argument("--service-role-key-env", default="STAGING_SUPABASE_SERVICE_ROLE_KEY")
    return parser


def main(argv: list[str] | None = None) -> int:
    from urllib.request import urlopen

    args = _parser().parse_args(argv)
    created: list[str] = []
    admin: SupabaseIdentityAdmin | None = None
    try:
        assert_staging_restore(
            environment=args.environment,
            production_triggers_paused=args.production_triggers_paused,
            project_ref=args.project_ref,
            confirmation=args.confirm,
        )
        archive = decrypt_archive(args.ciphertext, identity=args.age_identity)
        admin = SupabaseIdentityAdmin(
            os.environ.get(args.supabase_url_env, ""),
            os.environ.get(args.service_role_key_env, ""),
            opener=urlopen,
        )

        def create_identity(email: str) -> str:
            owner_id = admin.create(email)
            created.append(owner_id)
            return owner_id

        mapping = build_identity_remap(archive, create_identity)
        remapped = remap_archive_owners(archive, mapping)
        database_url = os.environ.get(args.database_url_env, "")
        if not database_url:
            raise RestoreRejected("staging database URL is not configured")
        with psycopg.connect(database_url, autocommit=False) as connection:
            connection.execute("SET LOCAL statement_timeout = '180s'")
            receipt = restore_database(connection, remapped_archive=remapped)
            connection.commit()
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ArchiveValidationError, BackupFailed, OSError, psycopg.Error, RestoreRejected, RuntimeError) as error:
        if admin is not None:
            for owner_id in reversed(created):
                try:
                    admin.delete(owner_id)
                except Exception:
                    pass
        print(f"restore rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
