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
from typing import Mapping

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
PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID = re.compile(r"[1-9][0-9]*\Z")


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
