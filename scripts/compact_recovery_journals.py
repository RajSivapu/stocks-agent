#!/usr/bin/env python3
"""Compact authenticated terminal release journals and reclaim their TOAST space."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping, Sequence

from cryptography.fernet import Fernet, InvalidToken
import psycopg
from psycopg.rows import dict_row

if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.release_components import (  # noqa: E402
    BACKEND_COMPONENTS,
    canonical,
    validate_snapshot,
)


JOURNALS = "public.stock_agent_component_recovery_journals"
LEASE = "public.stock_agent_release_mutation_lease"
JOURNAL_IDENTITY_INDEX = "stock_agent_component_recovery_journals_run_identity"
MAX_JOURNAL_ROWS = 10_000
MAX_RECEIPT_BYTES = 64_000
MAX_BACKUP_BYTES = 192 * 1024 * 1024
MAX_MANIFEST_BYTES = 64_000
BACKUP_FORMAT = "stocks-recovery-journal-backup-v1"
BACKUP_MANIFEST_FORMAT = "stocks-recovery-journal-backup-manifest-v1"
PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID = re.compile(r"[1-9][0-9]*\Z")
ARTIFACT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
ARTIFACT_NAME = re.compile(r"recovery-journal-backup-([1-9][0-9]*)-([1-9][0-9]*)\Z")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_component_entries(journal: Mapping[str, object], *, rolled_back: bool) -> None:
    components = journal.get("components")
    _require(isinstance(components, Mapping), "terminal recovery journal components are malformed")
    if rolled_back and not components:
        return
    _require(
        set(components) == set(BACKEND_COMPONENTS),
        "terminal recovery journal component set is incomplete",
    )
    for name in BACKEND_COMPONENTS:
        entry = components[name]
        _require(isinstance(entry, Mapping), "terminal recovery journal component is malformed")
        changed = entry.get("changed")
        _require(type(changed) is bool, "terminal recovery journal mutation boundary is unknown")
        prior = validate_snapshot(name, entry.get("prior"))
        _require(
            entry.get("prior_sha256") == hashlib.sha256(canonical(prior)).hexdigest(),
            "terminal recovery journal prior digest is invalid",
        )
        _require("recovery_failure" not in entry, "terminal recovery journal retains a recovery failure")
        if rolled_back:
            _require(changed is False, "rolled-back recovery journal still requires recovery")
        elif changed:
            validate_snapshot(name, entry.get("candidate"), candidate=True)
            validate_snapshot(name, entry.get("deployed"))


def _authenticate_terminal_journal(
    row: Mapping[str, object], cipher: Fernet, project_ref: str
) -> tuple[str, str]:
    try:
        encrypted = bytes(row["ciphertext"])
        journal = json.loads(cipher.decrypt(encrypted))
    except (InvalidToken, ValueError, TypeError, KeyError) as error:
        raise RuntimeError("latest recovery journal authentication failed") from error
    _require(isinstance(journal, Mapping), "latest recovery journal is malformed")
    context = journal.get("release_context")
    _require(isinstance(context, Mapping), "latest recovery journal identity is incomplete")
    expected = {
        "project_ref": row["project_ref"],
        "candidate_sha": row["candidate_sha"],
        "release_run_id": row["run_id"],
        "release_run_attempt": row["run_attempt"],
    }
    _require(
        context.get("project_ref") == project_ref
        and all(str(context.get(key)) == str(value) for key, value in expected.items()),
        "latest recovery journal identity mismatch",
    )
    _require(journal.get("format") == 1, "latest recovery journal format is unsupported")
    status = journal.get("status")
    _require(status in {"rolled_back", "verified"}, "latest recovery journal is not terminal")
    _validate_component_entries(journal, rolled_back=status == "rolled_back")
    return str(status), hashlib.sha256(encrypted).hexdigest()


def _relation_size(connection: psycopg.Connection) -> int:
    return int(
        connection.execute(
            "SELECT pg_total_relation_size(%s::regclass) AS bytes", (JOURNALS,)
        ).fetchone()["bytes"]
    )


def _database_size(connection: psycopg.Connection) -> int:
    return int(
        connection.execute(
            "SELECT pg_database_size(current_database()) AS bytes"
        ).fetchone()["bytes"]
    )


def _inventory(connection: psycopg.Connection) -> tuple[int, int]:
    row = connection.execute(
        f"SELECT count(*)::bigint AS rows, count(DISTINCT "
        f"(project_ref,candidate_sha,run_id,run_attempt))::bigint AS groups FROM {JOURNALS}"
    ).fetchone()
    return int(row["rows"]), int(row["groups"])


def _unique_identity_is_exact(connection: psycopg.Connection) -> bool:
    row = connection.execute(
        "SELECT i.indisunique, array_agg(a.attname ORDER BY keys.ordinality) AS columns "
        "FROM pg_catalog.pg_class idx "
        "JOIN pg_catalog.pg_namespace ns ON ns.oid=idx.relnamespace "
        "JOIN pg_catalog.pg_index i ON i.indexrelid=idx.oid "
        "JOIN unnest(i.indkey) WITH ORDINALITY keys(attnum,ordinality) ON true "
        "JOIN pg_catalog.pg_class tab ON tab.oid=i.indrelid "
        "JOIN pg_catalog.pg_namespace tabns ON tabns.oid=tab.relnamespace "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=tab.oid AND a.attnum=keys.attnum "
        "WHERE ns.nspname='public' AND idx.relname=%s "
        "AND tabns.nspname='public' AND tab.relname='stock_agent_component_recovery_journals' "
        "GROUP BY i.indisunique",
        (JOURNAL_IDENTITY_INDEX,),
    ).fetchall()
    return row == [{
        "indisunique": True,
        "columns": ["project_ref", "candidate_sha", "run_id", "run_attempt"],
    }]


def _validate_scope(
    *,
    project_ref: str,
    main_sha: str,
    expected_rows: int | None,
    expected_groups: int | None,
) -> None:
    _require(bool(PROJECT_REF.fullmatch(project_ref)), "exact project reference is required")
    _require(bool(SHA.fullmatch(main_sha)), "exact main SHA is required")
    _require(
        (expected_rows is None and expected_groups is None)
        or (
            type(expected_rows) is int
            and type(expected_groups) is int
            and 0 < expected_groups <= expected_rows <= MAX_JOURNAL_ROWS
        ),
        "approved recovery journal inventory is invalid",
    )


def _cipher(recovery_key: bytes) -> Fernet:
    try:
        return Fernet(recovery_key)
    except (TypeError, ValueError) as error:
        raise RuntimeError("valid release recovery key is required") from error


def _inventory_is_approved(
    rows: int,
    groups: int,
    expected_rows: int | None,
    expected_groups: int | None,
) -> bool:
    if expected_rows is None or expected_groups is None:
        return True
    return (rows, groups) in {
        (expected_rows, expected_groups),
        (expected_groups, expected_groups),
    }


def _resolved_lease(connection: psycopg.Connection) -> Mapping[str, object]:
    leases = connection.execute(
        f"SELECT owner,kind,state,expires_at>statement_timestamp() AS live "
        f"FROM {LEASE} WHERE singleton"
    ).fetchall()
    _require(len(leases) == 1, "protected release lease receipt is unavailable")
    _require(
        leases[0]["state"] == "resolved" and leases[0]["live"] is False,
        "protected release or recovery lease remains unresolved",
    )
    return leases[0]


def _write_bytes(path: Path, content: bytes, *, maximum: int, label: str) -> None:
    _require(0 < len(content) <= maximum, f"{label} exceeds its bound")
    path = path.resolve()
    _require(path.parent.is_dir() and not path.parent.is_symlink(), f"{label} parent is unavailable")
    _require(not path.exists() and not path.is_symlink(), f"{label} path already exists")
    temporary = path.with_suffix(path.suffix + ".pending")
    _require(not temporary.exists(), f"temporary {label} path already exists")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_canonical_json(path: Path, *, maximum: int, label: str) -> tuple[dict[str, object], bytes]:
    _require(path.exists() and path.is_file() and not path.is_symlink(), f"{label} is unavailable")
    size = path.stat().st_size
    _require(0 < size <= maximum, f"{label} exceeds its bound")
    content = path.read_bytes()
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is malformed") from error
    _require(isinstance(value, dict), f"{label} is malformed")
    _require(content == _canonical_json(value).encode() + b"\n", f"{label} is not canonical")
    return value, content


def _retained_identity(
    records: Sequence[Mapping[str, object]],
    cipher: Fernet,
    project_ref: str,
) -> tuple[list[dict[str, object]], Counter[str], int]:
    retained: list[dict[str, object]] = []
    statuses: Counter[str] = Counter()
    ciphertext_bytes = 0
    identities: set[tuple[str, str, str, str]] = set()
    for record in records:
        _require(
            set(record)
            == {
                "sequence",
                "project_ref",
                "candidate_sha",
                "run_id",
                "run_attempt",
                "ciphertext",
                "captured_at",
            },
            "recovery journal backup record is malformed",
        )
        _require(
            type(record["sequence"]) is int and int(record["sequence"]) > 0,
            "recovery journal backup sequence is malformed",
        )
        _require(
            isinstance(record["project_ref"], str)
            and record["project_ref"] == project_ref
            and isinstance(record["candidate_sha"], str)
            and bool(SHA.fullmatch(record["candidate_sha"]))
            and isinstance(record["run_id"], str)
            and bool(RUN_ID.fullmatch(record["run_id"]))
            and isinstance(record["run_attempt"], str)
            and bool(RUN_ID.fullmatch(record["run_attempt"])),
            "recovery journal row identity is malformed",
        )
        identity = (
            record["project_ref"],
            record["candidate_sha"],
            record["run_id"],
            record["run_attempt"],
        )
        _require(identity not in identities, "recovery journal backup identity is duplicated")
        identities.add(identity)
        ciphertext = record["ciphertext"]
        _require(isinstance(ciphertext, str), "recovery journal backup ciphertext is malformed")
        try:
            encrypted = ciphertext.encode("ascii")
        except UnicodeEncodeError as error:
            raise RuntimeError("recovery journal backup ciphertext is malformed") from error
        captured_at = record["captured_at"]
        _require(isinstance(captured_at, str), "recovery journal backup timestamp is malformed")
        try:
            parsed_captured_at = datetime.fromisoformat(captured_at)
        except ValueError as error:
            raise RuntimeError("recovery journal backup timestamp is malformed") from error
        _require(parsed_captured_at.tzinfo is not None, "recovery journal backup timestamp is malformed")
        row = {
            "sequence": record["sequence"],
            "project_ref": record["project_ref"],
            "candidate_sha": record["candidate_sha"],
            "run_id": record["run_id"],
            "run_attempt": record["run_attempt"],
            "ciphertext": encrypted,
        }
        status, ciphertext_sha256 = _authenticate_terminal_journal(row, cipher, project_ref)
        statuses[status] += 1
        ciphertext_bytes += len(encrypted)
        retained.append(
            {
                "sequence": record["sequence"],
                "project_ref": record["project_ref"],
                "candidate_sha": record["candidate_sha"],
                "run_id": record["run_id"],
                "run_attempt": record["run_attempt"],
                "captured_at": captured_at,
                "status": status,
                "ciphertext_sha256": ciphertext_sha256,
            }
        )
    return retained, statuses, ciphertext_bytes


def verify_recovery_journal_backup(
    *,
    backup_path: Path,
    manifest_path: Path,
    project_ref: str,
    main_sha: str,
    recovery_key: bytes,
    expected_rows: int | None = None,
    expected_groups: int | None = None,
) -> dict[str, object]:
    """Authenticate a deterministic encrypted backup without opening the database."""
    _validate_scope(
        project_ref=project_ref,
        main_sha=main_sha,
        expected_rows=expected_rows,
        expected_groups=expected_groups,
    )
    cipher = _cipher(recovery_key)
    manifest, _ = _read_canonical_json(
        manifest_path, maximum=MAX_MANIFEST_BYTES, label="recovery journal backup manifest"
    )
    backup, backup_bytes = _read_canonical_json(
        backup_path, maximum=MAX_BACKUP_BYTES, label="recovery journal backup"
    )
    _require(
        set(manifest)
        == {"format", "created_at", "project_ref", "main_sha", "bundle", "source", "retained"}
        and manifest["format"] == BACKUP_MANIFEST_FORMAT
        and manifest["project_ref"] == project_ref
        and manifest["main_sha"] == main_sha,
        "recovery journal backup manifest is malformed",
    )
    bundle_binding = manifest["bundle"]
    _require(
        isinstance(bundle_binding, Mapping)
        and set(bundle_binding) == {"format", "bytes", "sha256"}
        and bundle_binding["format"] == BACKUP_FORMAT,
        "recovery journal backup manifest is malformed",
    )
    _require(
        bundle_binding["bytes"] == len(backup_bytes)
        and bundle_binding["sha256"] == hashlib.sha256(backup_bytes).hexdigest(),
        "recovery journal backup digest mismatch",
    )
    _require(
        set(backup) == {"format", "project_ref", "main_sha", "records"}
        and backup["format"] == BACKUP_FORMAT
        and backup["project_ref"] == project_ref
        and backup["main_sha"] == main_sha
        and isinstance(backup["records"], list)
        and 0 < len(backup["records"]) <= MAX_JOURNAL_ROWS,
        "recovery journal backup is malformed",
    )
    source = manifest["source"]
    retained_manifest = manifest["retained"]
    _require(
        isinstance(source, Mapping)
        and set(source) == {"rows", "groups", "relation_bytes", "database_bytes"}
        and type(source["rows"]) is int
        and type(source["groups"]) is int
        and 0 < source["groups"] <= source["rows"] <= MAX_JOURNAL_ROWS
        and type(source["relation_bytes"]) is int
        and source["relation_bytes"] > 0
        and type(source["database_bytes"]) is int
        and source["database_bytes"] > 0,
        "recovery journal backup source inventory is malformed",
    )
    _require(
        _inventory_is_approved(
            source["rows"], source["groups"], expected_rows, expected_groups
        ),
        "approved inventory changed before recovery journal compaction",
    )
    _require(
        isinstance(retained_manifest, Mapping)
        and set(retained_manifest)
        == {"rows", "identity_sha256", "ciphertext_bytes", "terminal_statuses"},
        "recovery journal backup retained inventory is malformed",
    )
    retained, statuses, ciphertext_bytes = _retained_identity(
        backup["records"], cipher, project_ref
    )
    _require(
        retained_manifest["rows"] == source["groups"] == len(retained)
        and retained_manifest["identity_sha256"]
        == hashlib.sha256(canonical(retained)).hexdigest()
        and retained_manifest["ciphertext_bytes"] == ciphertext_bytes
        and retained_manifest["terminal_statuses"] == dict(sorted(statuses.items())),
        "recovery journal backup retained inventory mismatch",
    )
    return manifest


def prepare_recovery_journal_backup(
    *,
    admin_url: str,
    project_ref: str,
    main_sha: str,
    recovery_key: bytes,
    backup_path: Path,
    manifest_path: Path,
    expected_rows: int | None = None,
    expected_groups: int | None = None,
) -> dict[str, object]:
    """Export and authenticate the exact retained journal set without mutating it."""
    _validate_scope(
        project_ref=project_ref,
        main_sha=main_sha,
        expected_rows=expected_rows,
        expected_groups=expected_groups,
    )
    cipher = _cipher(recovery_key)
    with psycopg.connect(admin_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("SET TIME ZONE 'UTC'")
        connection.execute("SET statement_timeout='300s'")
        connection.execute("SET lock_timeout='15s'")
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('stock_agent_protected_release',0)) AS locked"
        ).fetchone()["locked"]
        _require(locked is True, "another protected release or recovery holds the mutation lock")
        try:
            lease = _resolved_lease(connection)
            connection.execute("BEGIN")
            try:
                connection.execute(f"LOCK TABLE {JOURNALS} IN SHARE MODE")
                source_rows, source_groups = _inventory(connection)
                _require(
                    source_rows <= MAX_JOURNAL_ROWS,
                    "recovery journal inventory exceeds the bounded maintenance limit",
                )
                _require(
                    _inventory_is_approved(
                        source_rows, source_groups, expected_rows, expected_groups
                    ),
                    "approved inventory changed before recovery journal compaction",
                )
                relation_bytes = _relation_size(connection)
                database_bytes = _database_size(connection)
                latest = connection.execute(
                    f"SELECT DISTINCT ON (project_ref,candidate_sha,run_id,run_attempt) "
                    f"sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at "
                    f"FROM {JOURNALS} ORDER BY project_ref,candidate_sha,run_id,run_attempt,sequence DESC"
                ).fetchall()
                _require(
                    len(latest) == source_groups,
                    "latest recovery journal inventory is incomplete",
                )
                records = []
                for row in latest:
                    encrypted = bytes(row["ciphertext"])
                    try:
                        ciphertext = encrypted.decode("ascii")
                    except UnicodeDecodeError as error:
                        raise RuntimeError("recovery journal backup ciphertext is malformed") from error
                    _require(
                        isinstance(row["captured_at"], datetime)
                        and row["captured_at"].tzinfo is not None,
                        "recovery journal backup timestamp is malformed",
                    )
                    records.append(
                        {
                            "sequence": int(row["sequence"]),
                            "project_ref": row["project_ref"],
                            "candidate_sha": row["candidate_sha"],
                            "run_id": row["run_id"],
                            "run_attempt": row["run_attempt"],
                            "ciphertext": ciphertext,
                            "captured_at": row["captured_at"].isoformat(),
                        }
                    )
                records.sort(key=lambda record: int(record["sequence"]))
                retained, statuses, ciphertext_bytes = _retained_identity(
                    records, cipher, project_ref
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended('stock_agent_protected_release',0))"
            )
    bundle = {
        "format": BACKUP_FORMAT,
        "project_ref": project_ref,
        "main_sha": main_sha,
        "records": records,
    }
    bundle_bytes = _canonical_json(bundle).encode() + b"\n"
    manifest = {
        "format": BACKUP_MANIFEST_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_ref": project_ref,
        "main_sha": main_sha,
        "bundle": {
            "format": BACKUP_FORMAT,
            "bytes": len(bundle_bytes),
            "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
        },
        "source": {
            "rows": source_rows,
            "groups": source_groups,
            "relation_bytes": relation_bytes,
            "database_bytes": database_bytes,
        },
        "retained": {
            "rows": len(retained),
            "identity_sha256": hashlib.sha256(canonical(retained)).hexdigest(),
            "ciphertext_bytes": ciphertext_bytes,
            "terminal_statuses": dict(sorted(statuses.items())),
        },
    }
    _write_bytes(
        backup_path,
        bundle_bytes,
        maximum=MAX_BACKUP_BYTES,
        label="recovery journal backup",
    )
    _write_bytes(
        manifest_path,
        _canonical_json(manifest).encode() + b"\n",
        maximum=MAX_MANIFEST_BYTES,
        label="recovery journal backup manifest",
    )
    _require(lease["state"] == "resolved", "protected release lease receipt is unavailable")
    return verify_recovery_journal_backup(
        backup_path=backup_path,
        manifest_path=manifest_path,
        project_ref=project_ref,
        main_sha=main_sha,
        recovery_key=recovery_key,
        expected_rows=expected_rows,
        expected_groups=expected_groups,
    )


def _validate_backup_artifact(binding: Mapping[str, object]) -> dict[str, object]:
    _require(
        isinstance(binding, Mapping)
        and set(binding) == {"id", "name", "digest", "workflow_run_id"}
        and type(binding["id"]) is int
        and binding["id"] > 0
        and isinstance(binding["name"], str)
        and isinstance(binding["digest"], str)
        and bool(ARTIFACT_DIGEST.fullmatch(binding["digest"]))
        and type(binding["workflow_run_id"]) is int
        and binding["workflow_run_id"] > 0,
        "backup artifact binding is malformed",
    )
    match = ARTIFACT_NAME.fullmatch(binding["name"])
    _require(
        match is not None and int(match.group(1)) == binding["workflow_run_id"],
        "backup artifact binding is malformed",
    )
    return {
        "id": binding["id"],
        "name": binding["name"],
        "digest": binding["digest"],
        "workflow_run_id": binding["workflow_run_id"],
    }


def _record_from_row(row: Mapping[str, object]) -> dict[str, object]:
    encrypted = bytes(row["ciphertext"])
    try:
        ciphertext = encrypted.decode("ascii")
    except UnicodeDecodeError as error:
        raise RuntimeError("recovery journal backup ciphertext is malformed") from error
    captured_at = row["captured_at"]
    _require(
        isinstance(captured_at, datetime) and captured_at.tzinfo is not None,
        "recovery journal backup timestamp is malformed",
    )
    return {
        "sequence": int(row["sequence"]),
        "project_ref": row["project_ref"],
        "candidate_sha": row["candidate_sha"],
        "run_id": row["run_id"],
        "run_attempt": row["run_attempt"],
        "ciphertext": ciphertext,
        "captured_at": captured_at.isoformat(),
    }


def _all_records(connection: psycopg.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        f"SELECT sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at "
        f"FROM {JOURNALS} ORDER BY sequence"
    ).fetchall()
    return [_record_from_row(row) for row in rows]


def _latest_records(connection: psycopg.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        f"SELECT DISTINCT ON (project_ref,candidate_sha,run_id,run_attempt) "
        f"sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at "
        f"FROM {JOURNALS} ORDER BY project_ref,candidate_sha,run_id,run_attempt,sequence DESC"
    ).fetchall()
    records = [_record_from_row(row) for row in rows]
    records.sort(key=lambda record: int(record["sequence"]))
    return records


def _record_identity(record: Mapping[str, object]) -> tuple[object, object, object, object]:
    return (
        record["project_ref"],
        record["candidate_sha"],
        record["run_id"],
        record["run_attempt"],
    )


def apply_recovery_journal_backup(
    *,
    admin_url: str,
    project_ref: str,
    main_sha: str,
    recovery_key: bytes,
    backup_path: Path,
    manifest_path: Path,
    backup_artifact: Mapping[str, object],
    expected_rows: int | None = None,
    expected_groups: int | None = None,
) -> dict[str, object]:
    """Apply an artifact-bound backup using resumable WAL-light table replacement."""
    artifact = _validate_backup_artifact(backup_artifact)
    manifest = verify_recovery_journal_backup(
        backup_path=backup_path,
        manifest_path=manifest_path,
        project_ref=project_ref,
        main_sha=main_sha,
        recovery_key=recovery_key,
        expected_rows=expected_rows,
        expected_groups=expected_groups,
    )
    backup, _ = _read_canonical_json(
        backup_path, maximum=MAX_BACKUP_BYTES, label="recovery journal backup"
    )
    records = backup["records"]
    _require(isinstance(records, list), "recovery journal backup is malformed")
    source = manifest["source"]
    retained_manifest = manifest["retained"]
    _require(
        isinstance(source, Mapping) and isinstance(retained_manifest, Mapping),
        "recovery journal backup manifest is malformed",
    )
    retained_by_identity = {_record_identity(record): record for record in records}
    started_at = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(admin_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("SET TIME ZONE 'UTC'")
        connection.execute("SET statement_timeout='300s'")
        connection.execute("SET lock_timeout='15s'")
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('stock_agent_protected_release',0)) AS locked"
        ).fetchone()["locked"]
        _require(locked is True, "another protected release or recovery holds the mutation lock")
        try:
            lease = _resolved_lease(connection)
            before_relation_bytes = _relation_size(connection)
            before_database_bytes = _database_size(connection)
            connection.execute("BEGIN")
            try:
                connection.execute(f"LOCK TABLE {JOURNALS} IN ACCESS EXCLUSIVE MODE")
                before_rows, before_groups = _inventory(connection)
                current_records = _all_records(connection)
                latest_records = _latest_records(connection)
                source_matches = (
                    (before_rows, before_groups) == (source["rows"], source["groups"])
                    and latest_records == records
                )
                complete_matches = (
                    before_rows == before_groups == len(records)
                    and current_records == records
                )
                current_by_identity = {
                    _record_identity(record): record for record in current_records
                }
                subset_matches = (
                    before_rows == before_groups
                    and before_rows < len(records)
                    and len(current_by_identity) == before_rows
                    and all(
                        retained_by_identity.get(identity) == record
                        for identity, record in current_by_identity.items()
                    )
                )
                _require(
                    source_matches or complete_matches or subset_matches,
                    "recovery journal state does not match the verified backup",
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

            truncated = bool(source_matches and before_rows > len(records))
            resumed = bool(subset_matches)
            if truncated:
                connection.execute(f"TRUNCATE TABLE {JOURNALS} CONTINUE IDENTITY")
                current_by_identity = {}

            connection.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {JOURNAL_IDENTITY_INDEX} "
                f"ON {JOURNALS}(project_ref,candidate_sha,run_id,run_attempt)"
            )
            _require(
                _unique_identity_is_exact(connection),
                "recovery journal unique identity is unavailable",
            )
            for record in records:
                if _record_identity(record) in current_by_identity:
                    continue
                connection.execute(
                    f"INSERT INTO {JOURNALS} AS journal("
                    f"sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at) "
                    f"OVERRIDING SYSTEM VALUE VALUES(%s,%s,%s,%s,%s,%s,%s) "
                    f"ON CONFLICT (project_ref,candidate_sha,run_id,run_attempt) DO UPDATE SET "
                    f"ciphertext=EXCLUDED.ciphertext,captured_at=EXCLUDED.captured_at",
                    (
                        record["sequence"],
                        record["project_ref"],
                        record["candidate_sha"],
                        record["run_id"],
                        record["run_attempt"],
                        record["ciphertext"].encode("ascii"),
                        record["captured_at"],
                    ),
                )
            maximum_sequence = max(int(record["sequence"]) for record in records)
            connection.execute(
                "SELECT setval(pg_get_serial_sequence(%s,'sequence'),%s,true)",
                (JOURNALS, maximum_sequence),
            )
            connection.execute(f"ANALYZE {JOURNALS}")
            after_rows, after_groups = _inventory(connection)
            _require(
                (after_rows, after_groups) == (len(records), len(records))
                and _all_records(connection) == records,
                "recovery journal restore readback is inconsistent",
            )
            _require(
                _unique_identity_is_exact(connection),
                "recovery journal unique identity readback failed",
            )
            receipt = {
                "format": "stocks-recovery-journal-compaction-v2",
                "project_ref": project_ref,
                "main_sha": main_sha,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "method": "truncate_restore",
                "lease": {
                    "owner": lease["owner"],
                    "kind": lease["kind"],
                    "state": "resolved",
                    "live": False,
                },
                "source": dict(source),
                "before": {"groups": before_groups, "rows": before_rows},
                "after": {"groups": after_groups, "rows": after_rows},
                "approved_inventory": (
                    None
                    if expected_rows is None
                    else {"groups": expected_groups, "rows": expected_rows}
                ),
                "truncated": truncated,
                "resumed": resumed,
                "removed_redundant_rows": int(source["rows"]) - len(records),
                "terminal_statuses": retained_manifest["terminal_statuses"],
                "retained_identity_sha256": retained_manifest["identity_sha256"],
                "backup_artifact": artifact,
                "relation_bytes_source": source["relation_bytes"],
                "relation_bytes_before": before_relation_bytes,
                "relation_bytes_after": _relation_size(connection),
                "database_bytes_source": source["database_bytes"],
                "database_bytes_before": before_database_bytes,
                "database_bytes_after": _database_size(connection),
                "unique_identity": True,
                "vacuum_full": False,
            }
            _require(
                len(_canonical_json(receipt).encode()) <= MAX_RECEIPT_BYTES,
                "recovery journal compaction receipt exceeds its bound",
            )
            return receipt
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended('stock_agent_protected_release',0))"
            )


def compact_recovery_journals(
    *,
    admin_url: str,
    project_ref: str,
    main_sha: str,
    recovery_key: bytes,
    expected_rows: int | None = None,
    expected_groups: int | None = None,
) -> dict[str, object]:
    """Keep one authenticated terminal checkpoint per run under the release lock."""
    _require(bool(PROJECT_REF.fullmatch(project_ref)), "exact project reference is required")
    _require(bool(SHA.fullmatch(main_sha)), "exact main SHA is required")
    _require(
        (expected_rows is None and expected_groups is None)
        or (
            type(expected_rows) is int
            and type(expected_groups) is int
            and 0 < expected_groups <= expected_rows <= MAX_JOURNAL_ROWS
        ),
        "approved recovery journal inventory is invalid",
    )
    try:
        cipher = Fernet(recovery_key)
    except (TypeError, ValueError) as error:
        raise RuntimeError("valid release recovery key is required") from error

    started_at = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(admin_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("SET statement_timeout='300s'")
        connection.execute("SET lock_timeout='15s'")
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('stock_agent_protected_release',0)) AS locked"
        ).fetchone()["locked"]
        _require(locked is True, "another protected release or recovery holds the mutation lock")
        try:
            leases = connection.execute(
                f"SELECT owner,kind,state,expires_at>statement_timestamp() AS live "
                f"FROM {LEASE} WHERE singleton"
            ).fetchall()
            _require(len(leases) == 1, "protected release lease receipt is unavailable")
            _require(
                leases[0]["state"] == "resolved" and leases[0]["live"] is False,
                "protected release or recovery lease remains unresolved",
            )
            before_relation_bytes = _relation_size(connection)
            before_database_bytes = _database_size(connection)
            before_rows, before_groups = _inventory(connection)
            _require(
                before_rows <= MAX_JOURNAL_ROWS,
                "recovery journal inventory exceeds the bounded maintenance limit",
            )
            if expected_rows is not None and expected_groups is not None:
                _require(
                    (before_rows, before_groups) == (expected_rows, expected_groups)
                    or (before_rows, before_groups) == (expected_groups, expected_groups),
                    "approved inventory changed before recovery journal compaction",
                )

            connection.execute("BEGIN")
            try:
                connection.execute(f"LOCK TABLE {JOURNALS} IN ACCESS EXCLUSIVE MODE")
                latest = connection.execute(
                    f"SELECT DISTINCT ON (project_ref,candidate_sha,run_id,run_attempt) "
                    f"sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext "
                    f"FROM {JOURNALS} ORDER BY project_ref,candidate_sha,run_id,run_attempt,sequence DESC"
                ).fetchall()
                _require(len(latest) == before_groups, "latest recovery journal inventory is incomplete")
                retained = []
                statuses: Counter[str] = Counter()
                for row in latest:
                    _require(
                        isinstance(row["project_ref"], str)
                        and row["project_ref"] == project_ref
                        and isinstance(row["candidate_sha"], str)
                        and bool(SHA.fullmatch(row["candidate_sha"]))
                        and isinstance(row["run_id"], str)
                        and bool(RUN_ID.fullmatch(row["run_id"]))
                        and isinstance(row["run_attempt"], str)
                        and bool(RUN_ID.fullmatch(row["run_attempt"])),
                        "recovery journal row identity is malformed",
                    )
                    status, ciphertext_sha256 = _authenticate_terminal_journal(
                        row, cipher, project_ref
                    )
                    statuses[status] += 1
                    retained.append(
                        {
                            "sequence": int(row["sequence"]),
                            "candidate_sha": row["candidate_sha"],
                            "run_id": int(row["run_id"]),
                            "run_attempt": int(row["run_attempt"]),
                            "status": status,
                            "ciphertext_sha256": ciphertext_sha256,
                        }
                    )
                deleted = connection.execute(
                    f"DELETE FROM {JOURNALS} WHERE sequence NOT IN "
                    f"(SELECT max(sequence) FROM {JOURNALS} "
                    f"GROUP BY project_ref,candidate_sha,run_id,run_attempt)"
                ).rowcount
                _require(
                    deleted == before_rows - before_groups,
                    "recovery journal deletion count is inconsistent",
                )
                connection.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {JOURNAL_IDENTITY_INDEX} "
                    f"ON {JOURNALS}(project_ref,candidate_sha,run_id,run_attempt)"
                )
                _require(
                    _unique_identity_is_exact(connection),
                    "recovery journal unique identity is unavailable",
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

            connection.execute(f"VACUUM (FULL,ANALYZE) {JOURNALS}")
            after_rows, after_groups = _inventory(connection)
            _require(
                (after_rows, after_groups) == (before_groups, before_groups),
                "recovery journal compaction readback is inconsistent",
            )
            _require(
                _unique_identity_is_exact(connection),
                "recovery journal unique identity readback failed",
            )
            receipt = {
                "format": "stocks-recovery-journal-compaction-v1",
                "project_ref": project_ref,
                "main_sha": main_sha,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "lease": {
                    "owner": leases[0]["owner"],
                    "kind": leases[0]["kind"],
                    "state": "resolved",
                    "live": False,
                },
                "before": {"groups": before_groups, "rows": before_rows},
                "after": {"groups": after_groups, "rows": after_rows},
                "approved_inventory": (
                    None
                    if expected_rows is None
                    else {"groups": expected_groups, "rows": expected_rows}
                ),
                "deleted_rows": deleted,
                "terminal_statuses": dict(sorted(statuses.items())),
                "retained_identity_sha256": hashlib.sha256(canonical(retained)).hexdigest(),
                "relation_bytes_before": before_relation_bytes,
                "relation_bytes_after": _relation_size(connection),
                "database_bytes_before": before_database_bytes,
                "database_bytes_after": _database_size(connection),
                "unique_identity": True,
                "vacuum_full": True,
            }
            _require(
                len(_canonical_json(receipt).encode()) <= MAX_RECEIPT_BYTES,
                "recovery journal compaction receipt exceeds its bound",
            )
            return receipt
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended('stock_agent_protected_release',0))"
            )


def _write_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    path = path.resolve()
    _require(path.parent.is_dir() and not path.parent.is_symlink(), "receipt parent is unavailable")
    _require(not path.exists() and not path.is_symlink(), "receipt path already exists")
    temporary = path.with_suffix(path.suffix + ".pending")
    _require(not temporary.exists(), "temporary receipt path already exists")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_canonical_json(receipt) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--expected-rows", required=True, type=int)
    parser.add_argument("--expected-groups", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    from scripts.deploy_owner_dashboard_api import validate_release_admin_session_url

    admin_url = validate_release_admin_session_url(arguments.project_ref, arguments.admin_url)
    key = os.environ.get("RELEASE_RECOVERY_KEY", "").encode()
    receipt = compact_recovery_journals(
        admin_url=admin_url,
        project_ref=arguments.project_ref,
        main_sha=arguments.main_sha,
        recovery_key=key,
        expected_rows=arguments.expected_rows,
        expected_groups=arguments.expected_groups,
    )
    _write_receipt(arguments.output, receipt)
    print(_canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
