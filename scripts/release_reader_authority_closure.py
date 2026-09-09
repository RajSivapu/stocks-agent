#!/usr/bin/env python3
"""One-time, fail-closed closure of legacy release-reader extension access."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Final

import psycopg

if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from lib.release_reader_closure_contract import (  # noqa: E402
    CLOSURE_READER_CONTRACT,
)
from scripts.deploy_owner_dashboard_api import (  # noqa: E402
    DurableMutationLease,
    MIGRATION_LEDGER,
    RECONCILIATION_BASELINE_PATH,
    RECONCILIATION_BASELINE_VERSION,
    ROOT,
    candidate_migration_manifest,
    legacy_migration_receipts,
    migration_execution_statements,
    migration_semantic_sha256,
    reconciliation_baseline_manifest,
    validate_candidate_migration_cutover,
    validate_release_admin_session_url,
)
from scripts.managed_isolated_restore import canonical_json  # noqa: E402
from scripts.protected_evidence import (  # noqa: E402
    LEGACY_EXTENSION_FUNCTIONS,
    PostgresReadOnlySource,
    READER,
    READER_AUTHORITY_CLOSURE_PATH,
    READER_PRIVILEGE_ROLE,
    ReleaseReaderFunctionAuthorityError,
)
from scripts.release_reader_diagnostic import (  # noqa: E402
    FORMAT as DIAGNOSTIC_FORMAT,
    MAX_RECEIPT_BYTES as MAX_DIAGNOSTIC_BYTES,
)
from scripts.verify_owner_dashboard_deployment import (  # noqa: E402
    validate_evidence_database_url,
)


FORMAT: Final = "stocks-release-reader-authority-closure-v1"
MAX_RECEIPT_BYTES = 16_384
ROLES: Final = (READER_PRIVILEGE_ROLE, READER)
CLOSURE_VERSION: Final = "20261017"
CLOSURE_RAW_SHA256: Final = (
    "0eb1b8245cde4b10a29c6a78c23610ccf705e9480f45aa80eedfdd1a7127f906"
)
CLOSURE_LEASE_OWNER: Final = (
    f"reader-closure-{CLOSURE_VERSION}-{CLOSURE_RAW_SHA256}"
)
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")

EXPECTED_FUNCTION_AUTHORITY_ISSUES: Final = tuple(
    {"function": function, "reason": "function_not_allowlisted"}
    for function in (
        "extensions.grant_pg_cron_access()",
        "extensions.grant_pg_graphql_access()",
        "extensions.grant_pg_net_access()",
        "extensions.pgrst_ddl_watch()",
        "extensions.pgrst_drop_watch()",
        "extensions.set_graphql_placeholder()",
        "extensions.uuid_generate_v1()",
        "extensions.uuid_generate_v1mc()",
        "extensions.uuid_generate_v3(uuid,text)",
        "extensions.uuid_generate_v4()",
        "extensions.uuid_generate_v5(uuid,text)",
        "extensions.uuid_nil()",
        "extensions.uuid_ns_dns()",
        "extensions.uuid_ns_oid()",
        "extensions.uuid_ns_url()",
        "extensions.uuid_ns_x500()",
    )
)
_EXPECTED_UNEXPECTED_FUNCTIONS: Final = {
    item["function"] for item in EXPECTED_FUNCTION_AUTHORITY_ISSUES
}

_SCHEMA_AUTHORITY_SQL: Final = """SELECT /* closure_schema_authority */
  role_name,
  pg_catalog.has_schema_privilege(role_name, namespace.oid, 'USAGE') AS schema_usage
FROM (VALUES ('stock_agent_release_reader'),
             ('stock_agent_release_reader_runtime')) AS roles(role_name)
CROSS JOIN pg_catalog.pg_namespace namespace
WHERE namespace.nspname='extensions'
ORDER BY role_name"""

_EXTENSION_INVENTORY_SQL: Final = """SELECT /* closure_extension_inventory */
  role_name AS role,
  pg_catalog.has_schema_privilege(role_name, namespace.oid, 'USAGE') AS schema_usage,
  namespace.nspname||'.'||procedure.proname||'('||
    replace(pg_catalog.oidvectortypes(procedure.proargtypes),', ',',')||')' AS function,
  extension.extname AS extension,
  language.lanname AS language,
  procedure.prosecdef AS security_definer,
  pg_catalog.has_function_privilege(
    role_name, procedure.oid, 'EXECUTE WITH GRANT OPTION') AS grantable,
  owner.rolname AS owner
FROM (VALUES ('stock_agent_release_reader'),
             ('stock_agent_release_reader_runtime')) AS roles(role_name)
CROSS JOIN pg_catalog.pg_proc procedure
JOIN pg_catalog.pg_namespace namespace ON namespace.oid=procedure.pronamespace
JOIN pg_catalog.pg_roles owner ON owner.oid=procedure.proowner
JOIN pg_catalog.pg_language language ON language.oid=procedure.prolang
LEFT JOIN pg_catalog.pg_depend dependency
  ON dependency.classid='pg_proc'::regclass
 AND dependency.objid=procedure.oid AND dependency.deptype='e'
LEFT JOIN pg_catalog.pg_extension extension ON extension.oid=dependency.refobjid
WHERE namespace.nspname='extensions'
  AND pg_catalog.has_schema_privilege(role_name,namespace.oid,'USAGE')
  AND pg_catalog.has_function_privilege(role_name,procedure.oid,'EXECUTE')
ORDER BY role_name,procedure.proname,
         pg_catalog.oidvectortypes(procedure.proargtypes)"""

_EXTENSION_COUNTS_SQL: Final = """SELECT /* closure_extension_counts */
  role_name,
  (SELECT count(*)
     FROM pg_catalog.pg_proc procedure
     JOIN pg_catalog.pg_namespace namespace ON namespace.oid=procedure.pronamespace
    WHERE namespace.nspname='extensions'
      AND pg_catalog.has_schema_privilege(role_name,namespace.oid,'USAGE')
      AND pg_catalog.has_function_privilege(role_name,procedure.oid,'EXECUTE'))
      AS function_count,
  (SELECT count(*)
     FROM pg_catalog.pg_class class
     JOIN pg_catalog.pg_namespace namespace ON namespace.oid=class.relnamespace
    WHERE namespace.nspname='extensions'
      AND class.relkind IN ('r','p','v','m','f','S')
      AND pg_catalog.has_schema_privilege(role_name,namespace.oid,'USAGE')
      AND (pg_catalog.has_table_privilege(role_name,class.oid,'SELECT')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'INSERT')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'UPDATE')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'DELETE')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'TRUNCATE')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'REFERENCES')
        OR pg_catalog.has_table_privilege(role_name,class.oid,'TRIGGER')))
      AS relation_count
FROM (VALUES ('stock_agent_release_reader'),
             ('stock_agent_release_reader_runtime')) AS roles(role_name)
ORDER BY role_name"""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validate_diagnostic_receipt(
    receipt: object, project_ref: str, main_sha: str,
) -> dict[str, object]:
    """Bind the bridge to the one reviewed, bounded diagnostic failure."""
    _require(
        isinstance(receipt, Mapping)
        and _PROJECT_REF.fullmatch(project_ref) is not None
        and _SHA40.fullmatch(main_sha) is not None,
        "release reader diagnostic receipt is malformed",
    )
    encoded = canonical_json(receipt).encode()
    _require(
        len(encoded) <= MAX_DIAGNOSTIC_BYTES
        and set(receipt) == {
            "format", "main_sha", "production_binding_sha256",
            "credential", "preflight", "receipt_sha256",
        },
        "release reader diagnostic receipt is malformed",
    )
    expected_binding = hashlib.sha256(
        (DIAGNOSTIC_FORMAT + "\0" + project_ref).encode()
    ).hexdigest()
    credential = receipt.get("credential")
    preflight = receipt.get("preflight")
    authority = preflight.get("function_authority") if isinstance(preflight, Mapping) else None
    issues = authority.get("issues") if isinstance(authority, Mapping) else None
    supplied_hash = receipt.get("receipt_sha256")
    core = dict(receipt)
    core.pop("receipt_sha256", None)
    expected_hash = hashlib.sha256(canonical_json(core).encode()).hexdigest()
    _require(
        receipt.get("format") == DIAGNOSTIC_FORMAT
        and receipt.get("main_sha") == main_sha
        and receipt.get("production_binding_sha256") == expected_binding
        and credential == {"status": "valid"}
        and isinstance(preflight, Mapping)
        and set(preflight) == {"status", "error_code", "function_authority"}
        and preflight.get("status") == "failed"
        and preflight.get("error_code") == "function_authority_mismatch"
        and isinstance(authority, Mapping)
        and set(authority) == {"issue_count", "issues", "truncated"}
        and authority.get("issue_count") == len(EXPECTED_FUNCTION_AUTHORITY_ISSUES)
        and authority.get("truncated") is False
        and issues == list(EXPECTED_FUNCTION_AUTHORITY_ISSUES)
        and isinstance(supplied_hash, str)
        and _SHA256.fullmatch(supplied_hash) is not None
        and supplied_hash == expected_hash,
        "release reader diagnostic receipt is not the exact reviewed failure",
    )
    return {
        "receipt_sha256": supplied_hash,
        "issue_count": len(EXPECTED_FUNCTION_AUTHORITY_ISSUES),
    }


def _valid_manifest(manifest: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    """Validate the full candidate and return its prefix through the closure."""
    candidate = [dict(item) for item in manifest]
    _require(candidate, "candidate migration manifest is empty")
    for item in candidate:
        path = item.get("path")
        version = item.get("version")
        digest = item.get("sha256")
        _require(
            set(item) == {"path", "version", "sha256"}
            and isinstance(path, str)
            and path == f"sql/migrations/{Path(path).name}"
            and isinstance(version, str)
            and Path(path).name.startswith(version + "_")
            and isinstance(digest, str)
            and _SHA256.fullmatch(digest) is not None,
            "candidate migration manifest is malformed",
        )
    paths = [item["path"] for item in candidate]
    _require(
        paths == sorted(paths)
        and len(paths) == len(set(paths))
        and len({item["version"] for item in candidate}) == len(candidate),
        "candidate migration manifest is unordered",
    )
    validate_candidate_migration_cutover(candidate)
    closure_indexes = [
        index for index, item in enumerate(candidate)
        if item["path"] == READER_AUTHORITY_CLOSURE_PATH
        and item["version"] == CLOSURE_VERSION
    ]
    _require(
        len(closure_indexes) == 1
        and candidate[closure_indexes[0]]["sha256"] == CLOSURE_RAW_SHA256,
        "release reader closure migration is not pinned in the candidate",
    )
    return candidate[:closure_indexes[0] + 1]


def closure_migration_manifest(
    manifest: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, str]:
    """Return the immutable closure entry after validating the full manifest."""
    prefix = _valid_manifest(
        candidate_migration_manifest() if manifest is None else manifest
    )
    return dict(prefix[-1])


def _private_migration_rows(
    rows: object,
    manifest: Sequence[Mapping[str, str]],
    legacy: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[str, str]]:
    _require(isinstance(rows, list), "private migration ledger is malformed")
    candidate_by_path = {item["path"]: item for item in manifest}
    known: dict[str, tuple[str, str]] = {}
    versions: set[str] = set()
    for row in rows:
        _require(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes))
            and len(row) == 3 and all(isinstance(value, str) for value in row),
            "private migration ledger is malformed",
        )
        path, version, digest = row
        is_baseline = (
            path == RECONCILIATION_BASELINE_PATH
            and version == RECONCILIATION_BASELINE_VERSION
        )
        item = candidate_by_path.get(path)
        allowed: set[str] = set()
        if item is not None and item["version"] == version:
            allowed.add(item["sha256"])
            if path in legacy:
                allowed.add(legacy[path]["legacy_sha256"])
        _require(
            path not in known and version not in versions
            and _SHA256.fullmatch(digest) is not None
            and (is_baseline or digest in allowed),
            "private migration ledger is malformed",
        )
        known[path] = (
            version,
            digest if is_baseline else candidate_by_path[path]["sha256"],
        )
        versions.add(version)
    return known


def _native_migration_rows(
    rows: object,
    manifest: Sequence[Mapping[str, str]],
    legacy: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[str, str]]:
    _require(isinstance(rows, list), "native migration ledger is malformed")
    by_version: dict[str, list[Mapping[str, str]]] = {}
    for item in manifest:
        by_version.setdefault(item["version"], []).append(item)
    native: dict[str, tuple[str, str]] = {}
    for row in rows:
        _require(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes))
            and len(row) == 2 and isinstance(row[0], str)
            and isinstance(row[1], Sequence) and not isinstance(row[1], (str, bytes))
            and bool(row[1]) and all(isinstance(part, str) for part in row[1]),
            "native migration ledger is malformed",
        )
        version, statements = row
        matches = [
            item for item in by_version.get(version, [])
            if item["path"] in legacy
            and item["sha256"] == legacy[item["path"]]["exact_sha256"]
            and migration_semantic_sha256(statements)
                == legacy[item["path"]]["semantic_sha256"]
        ]
        _require(
            len(matches) == 1 and matches[0]["path"] not in native,
            "native migration hash mismatch",
        )
        item = matches[0]
        native[item["path"]] = (item["version"], item["sha256"])
    return native


def classify_migration_state(
    private_rows: object,
    native_rows: object,
    manifest: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, object]:
    """Classify only sole-pending closure or exact already-closed state."""
    candidate = _valid_manifest(
        candidate_migration_manifest() if manifest is None else manifest
    )
    legacy = {
        item["path"]: item for item in legacy_migration_receipts()
    }
    known = _private_migration_rows(private_rows, candidate, legacy)
    has_baseline = (
        RECONCILIATION_BASELINE_PATH in known
        or any(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes))
            and len(row) > 0 and row[0] == RECONCILIATION_BASELINE_VERSION
            for row in native_rows if isinstance(native_rows, list)
        )
    )
    subsumed: set[str] = set()
    if has_baseline:
        baseline = reconciliation_baseline_manifest()
        _require(
            known.get(baseline["path"])
                == (baseline["version"], baseline["sha256"]),
            "reconciliation migration private baseline is invalid",
        )
        _require(
            isinstance(native_rows, list)
            and len(native_rows) == 1
            and isinstance(native_rows[0], Sequence)
            and len(native_rows[0]) == 2
            and native_rows[0][0] == baseline["version"]
            and isinstance(native_rows[0][1], Sequence)
            and not isinstance(native_rows[0][1], (str, bytes)),
            "reconciliation migration native baseline shape is invalid",
        )
        _require(
            migration_semantic_sha256(native_rows[0][1])
            == migration_semantic_sha256([
                (ROOT / RECONCILIATION_BASELINE_PATH).read_text(
                    encoding="utf-8"
                )
            ]),
            "reconciliation migration native baseline identity is invalid",
        )
        subsumed = {
            item["path"] for item in candidate
            if item["version"] < baseline["version"]
        }
        known = {
            path: value for path, value in known.items()
            if path != baseline["path"]
        }
    else:
        native = _native_migration_rows(native_rows, candidate, legacy)
        native_paths = [
            item["path"] for item in candidate if item["path"] in native
        ]
        _require(
            native_paths == [item["path"] for item in candidate[:len(native_paths)]],
            "native migration state is not an exact candidate prefix",
        )
        _require(
            all(known.get(path) == value for path, value in native.items()),
            "native and private migration ledgers diverge",
        )

    active = [item for item in candidate if item["path"] not in subsumed]
    private_paths = [item["path"] for item in active if item["path"] in known]
    _require(
        private_paths == [item["path"] for item in active[:len(private_paths)]]
        and len(known) == len(private_paths),
        "private migration state is not an exact candidate prefix",
    )
    if len(private_paths) == len(active) - 1:
        state = "pending"
        pending = [dict(active[-1])]
    elif len(private_paths) == len(active):
        state = "already_closed"
        pending = []
    else:
        raise RuntimeError(
            "release reader closure is not the sole pending migration"
        )
    return {
        "state": state,
        "closure": dict(candidate[-1]),
        "pending": pending,
        "candidate_count": len(candidate),
        "private_row_count": len(private_rows),
        "native_row_count": len(native_rows),
    }


def validate_extension_inventory(rows: object) -> dict[str, int]:
    """Require the exact unexpected reachability for each release-reader role."""
    _require(isinstance(rows, list), "release reader extension inventory is malformed")
    by_role = {role: set() for role in ROLES}
    for row in rows:
        _require(
            isinstance(row, Mapping)
            and set(row) == {
                "role", "schema_usage", "function", "extension", "language",
                "security_definer", "grantable", "owner",
            }
            and row.get("role") in ROLES
            and row.get("schema_usage") is True
            and isinstance(row.get("function"), str)
            and row.get("grantable") is False
            and row.get("owner") not in ROLES,
            "release reader extension inventory is unsafe",
        )
        role = str(row["role"])
        function = str(row["function"])
        _require(
            function not in by_role[role],
            "release reader extension inventory contains duplicates",
        )
        by_role[role].add(function)
        if function not in _EXPECTED_UNEXPECTED_FUNCTIONS:
            _require(
                function in LEGACY_EXTENSION_FUNCTIONS
                and row.get("extension") == LEGACY_EXTENSION_FUNCTIONS[function]
                and row.get("language") == "c"
                and row.get("security_definer") is False,
                "release reader extension inventory is unsafe",
            )
    _require(
        all(
            functions & _EXPECTED_UNEXPECTED_FUNCTIONS
                == _EXPECTED_UNEXPECTED_FUNCTIONS
            and functions <= (
                _EXPECTED_UNEXPECTED_FUNCTIONS | set(LEGACY_EXTENSION_FUNCTIONS)
            )
            for functions in by_role.values()
        ),
        "release reader extension inventory does not match the diagnostic",
    )
    return {
        "role_count": len(ROLES),
        "unexpected_function_count_per_role": len(
            EXPECTED_FUNCTION_AUTHORITY_ISSUES
        ),
    }


def verify_reader_state(
    database_url: str,
    project_ref: str,
    *,
    source_factory: Any = PostgresReadOnlySource,
) -> dict[str, object]:
    """Prove either the exact legacy exposure or the strict closed authority."""
    try:
        with source_factory(
            database_url, project_ref, pre_migration_baseline=True,
            reader_contract=CLOSURE_READER_CONTRACT,
        ) as source:
            identity = source.identity()
            authority = source.authority_receipt()
            scope = source.pre_migration_scope()
            _require(
                identity.get("read_only") is True
                and identity.get("isolated_guard") is False
                and authority.get("status") == "verified"
                and type(authority.get("read_table_count")) is int
                and authority["read_table_count"] > 0
                and scope.get("absent_tables") == []
                and scope.get("unreadable_tables") == [],
                "strict release reader post-closure evidence is incomplete",
            )
            return {
                "state": "strict_closed",
                "read_table_count": authority["read_table_count"],
                "authority_sha256": _canonical_sha256({
                    "identity": identity,
                    "authority": authority,
                    "scope": scope,
                }),
            }
    except ReleaseReaderFunctionAuthorityError as error:
        _require(
            error.issue_count == len(EXPECTED_FUNCTION_AUTHORITY_ISSUES)
            and error.truncated is False
            and error.issues == list(EXPECTED_FUNCTION_AUTHORITY_ISSUES),
            "release reader live function authority differs from the diagnostic",
        )
        return {
            "state": "legacy_open",
            "issue_count": len(EXPECTED_FUNCTION_AUTHORITY_ISSUES),
            "authority_sha256": _canonical_sha256({
                "issues": list(EXPECTED_FUNCTION_AUTHORITY_ISSUES),
                "truncated": False,
            }),
        }


def _schema_rows(rows: object) -> dict[str, bool]:
    _require(isinstance(rows, list), "release reader schema authority is malformed")
    normalized: dict[str, bool] = {}
    for row in rows:
        if isinstance(row, Mapping):
            role, usage = row.get("role_name", row.get("role")), row.get("schema_usage")
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) == 2:
            role, usage = row
        else:
            raise RuntimeError("release reader schema authority is malformed")
        _require(
            role in ROLES and type(usage) is bool and role not in normalized,
            "release reader schema authority is malformed",
        )
        normalized[str(role)] = usage
    _require(
        set(normalized) == set(ROLES),
        "release reader schema authority is incomplete",
    )
    return normalized


def _inventory_rows(rows: object) -> list[dict[str, object]]:
    _require(isinstance(rows, list), "release reader extension inventory is malformed")
    columns = (
        "role", "schema_usage", "function", "extension", "language",
        "security_definer", "grantable", "owner",
    )
    normalized = []
    for row in rows:
        if isinstance(row, Mapping):
            normalized.append(dict(row))
        elif (
            isinstance(row, Sequence) and not isinstance(row, (str, bytes))
            and len(row) == len(columns)
        ):
            normalized.append(dict(zip(columns, row, strict=True)))
        else:
            raise RuntimeError("release reader extension inventory is malformed")
    return normalized


def _closed_counts(rows: object) -> dict[str, tuple[int, int]]:
    _require(isinstance(rows, list), "post-closure extension counts are malformed")
    normalized = {}
    for row in rows:
        if isinstance(row, Mapping):
            role = row.get("role_name", row.get("role"))
            functions = row.get("function_count")
            relations = row.get("relation_count")
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) == 3:
            role, functions, relations = row
        else:
            raise RuntimeError("post-closure extension counts are malformed")
        _require(
            role in ROLES and type(functions) is int and type(relations) is int
            and role not in normalized,
            "post-closure extension counts are malformed",
        )
        normalized[str(role)] = (functions, relations)
    _require(
        set(normalized) == set(ROLES)
        and all(value == (0, 0) for value in normalized.values()),
        "release reader still reaches extension objects after closure",
    )
    return normalized


def _fetchall(cursor, statement: str, parameters: tuple = ()) -> list[Any]:
    cursor.execute(statement, parameters or None)
    rows = cursor.fetchall()
    _require(isinstance(rows, list), "database evidence rows are malformed")
    return rows


def apply_closure_transaction(
    cursor,
    *,
    manifest: Sequence[Mapping[str, str]] | None = None,
    migrations_directory: Path = ROOT / "sql/migrations",
    verify_only: bool = False,
) -> dict[str, object]:
    """Apply only the reviewed closure and its exact ledger row in one transaction."""
    candidate = _valid_manifest(
        candidate_migration_manifest() if manifest is None else manifest
    )
    closure = candidate[-1]
    cursor.execute("SET LOCAL statement_timeout='30s'")
    cursor.execute("SET LOCAL lock_timeout='10s'")
    cursor.execute("SET LOCAL idle_in_transaction_session_timeout='45s'")

    before_schema = _schema_rows(_fetchall(cursor, _SCHEMA_AUTHORITY_SQL))
    before_inventory = _inventory_rows(
        _fetchall(cursor, _EXTENSION_INVENTORY_SQL)
    )
    private = _fetchall(
        cursor,
        f"SELECT path,version,sha256 FROM {MIGRATION_LEDGER} FOR UPDATE",
    )
    native = _fetchall(
        cursor,
        "SELECT version,statements FROM supabase_migrations.schema_migrations "
        "ORDER BY version FOR UPDATE",
    )
    migration = classify_migration_state(private, native, candidate)

    if verify_only and migration["state"] != "already_closed":
        raise RuntimeError(
            "verify-only closure finalization found a pending migration"
        )

    if migration["state"] == "pending":
        _require(
            before_schema == {role: True for role in ROLES},
            "release reader legacy extension schema authority is not exact",
        )
        inventory = validate_extension_inventory(before_inventory)
        path = migrations_directory / Path(closure["path"]).name
        _require(
            path.is_file() and not path.is_symlink()
            and path.parent.is_dir() and not path.parent.is_symlink(),
            "release reader closure migration path is unsafe",
        )
        raw = path.read_bytes()
        _require(
            hashlib.sha256(raw).hexdigest() == closure["sha256"],
            "release reader closure migration hash mismatch",
        )
        try:
            statements = migration_execution_statements(raw.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise RuntimeError(
                "release reader closure migration is not UTF-8"
            ) from error
        _require(
            len(statements) == 1
            and statements[0].startswith("REVOKE USAGE ON SCHEMA extensions")
            and "GRANT" not in statements[0].upper(),
            "release reader closure migration body is not exact",
        )
        for statement in statements:
            cursor.execute(statement, prepare=True)
        cursor.execute(
            f"INSERT INTO {MIGRATION_LEDGER} (path,version,sha256) "
            "VALUES (%s,%s,%s)",
            (closure["path"], closure["version"], closure["sha256"]),
        )
        result_state = "applied"
    else:
        _require(
            before_schema == {role: False for role in ROLES}
            and before_inventory == [],
            "already-closed ledger does not match live reader authority",
        )
        inventory = {
            "role_count": len(ROLES),
            "unexpected_function_count_per_role": 0,
        }
        result_state = "already_closed"

    ledger_rows = _fetchall(
        cursor,
        f"SELECT /* closure_exact_ledger */ path,version,sha256 "
        f"FROM {MIGRATION_LEDGER} WHERE path=%s OR version=%s",
        (closure["path"], closure["version"]),
    )
    _require(
        ledger_rows == [
            (closure["path"], closure["version"], closure["sha256"])
        ] or ledger_rows == [closure],
        "release reader closure ledger row is not exact",
    )
    after_schema = _schema_rows(_fetchall(cursor, _SCHEMA_AUTHORITY_SQL))
    _require(
        after_schema == {role: False for role in ROLES},
        "release reader extension schema authority remains after closure",
    )
    counts = _closed_counts(_fetchall(cursor, _EXTENSION_COUNTS_SQL))
    return {
        "state": result_state,
        "closure": dict(closure),
        "pre_extension_inventory": inventory,
        "pre_extension_inventory_sha256": _canonical_sha256(
            before_inventory
        ),
        "post_extension_function_count": sum(value[0] for value in counts.values()),
        "post_extension_relation_count": sum(value[1] for value in counts.values()),
        "post_extension_authority_sha256": _canonical_sha256({
            "schema_usage": after_schema,
            "object_counts": counts,
        }),
        "transaction_state": "verified_before_commit",
    }


def _positive_id(value: str, name: str) -> str:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise ValueError(f"canonical {name} is required")
    return value


def _validate_cli_metadata(args: argparse.Namespace) -> None:
    if (
        _PROJECT_REF.fullmatch(args.project_ref) is None
        or _SHA40.fullmatch(args.main_sha) is None
        or _SHA40.fullmatch(args.reviewed_sha) is None
        or _SHA40.fullmatch(args.diagnostic_main_sha) is None
        or args.authorization_kind not in {"github_review", "owner_comment"}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)
    ):
        raise ValueError("closure authorization metadata is malformed")
    for name in (
        "workflow_run_id", "workflow_run_attempt", "pr_ci_workflow_run_id",
        "ci_workflow_run_id", "pull_request_number", "authorization_id",
        "diagnostic_workflow_run_id", "diagnostic_workflow_run_attempt",
        "diagnostic_artifact_id",
    ):
        _positive_id(str(getattr(args, name)), name)
    expected_name = (
        "production-release-reader-diagnostic-"
        f"{args.diagnostic_workflow_run_id}-"
        f"{args.diagnostic_workflow_run_attempt}"
    )
    if (
        args.diagnostic_artifact_name != expected_name
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.diagnostic_artifact_digest)
        or args.lease_owner != CLOSURE_LEASE_OWNER
    ):
        raise ValueError("diagnostic artifact or lease binding is malformed")


def _load_diagnostic(path: Path) -> object:
    if (
        path.name != "release-reader-diagnostic.json"
        or not path.is_file() or path.is_symlink()
        or path.stat().st_size > MAX_DIAGNOSTIC_BYTES
    ):
        raise RuntimeError("release reader diagnostic receipt path is unsafe")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("release reader diagnostic receipt is malformed") from error


def _finalize_receipt(receipt: dict[str, object]) -> dict[str, object]:
    receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode()
    ).hexdigest()
    _require(
        len(canonical_json(receipt).encode()) <= MAX_RECEIPT_BYTES,
        "release reader authority closure receipt exceeds bound",
    )
    return receipt


def _write_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(receipt) + "\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def close_release_reader_authority(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    *,
    connector: Any = psycopg.connect,
    source_factory: Any = PostgresReadOnlySource,
    lease_factory: Any = DurableMutationLease,
) -> dict[str, object]:
    """Perform the one-time closure or verify an exact already-closed retry."""
    _validate_cli_metadata(args)
    admin_url = environment.get("SUPAVISOR_SESSION_URL", "")
    reader_url = environment.get("RELEASE_READONLY_DATABASE_URL", "")
    if not admin_url or not reader_url:
        raise RuntimeError("protected closure database credentials are required")
    validate_release_admin_session_url(args.project_ref, admin_url)
    validate_evidence_database_url(
        reader_url,
        f"https://{args.project_ref}.supabase.co/functions/v1/owner-dashboard-api",
    )
    diagnostic = validate_diagnostic_receipt(
        _load_diagnostic(args.diagnostic), args.project_ref,
        args.diagnostic_main_sha,
    )
    started_at = datetime.now(timezone.utc).isoformat()
    with lease_factory(admin_url, args.lease_owner, "recovery") as lease:
        try:
            pre_reader = verify_reader_state(
                reader_url, args.project_ref, source_factory=source_factory,
            )
            _require(
                pre_reader.get("state") in {"legacy_open", "strict_closed"},
                "release reader precheck state is not canonical",
            )
            if pre_reader["state"] == "legacy_open":
                _require(
                    args.diagnostic_main_sha == args.main_sha,
                    "pending closure requires a current-main diagnostic",
                )
            lease.heartbeat()
            with connector(
                admin_url, sslmode="verify-full", connect_timeout=15,
            ) as connection:
                with connection.transaction(), connection.cursor() as cursor:
                    transaction = apply_closure_transaction(
                        cursor,
                        verify_only=pre_reader["state"] == "strict_closed",
                    )
            transaction["transaction_state"] = "committed"
            lease.heartbeat()
            post_reader = verify_reader_state(
                reader_url, args.project_ref, source_factory=source_factory,
            )
            _require(
                post_reader.get("state") == "strict_closed"
                and (
                    (
                        transaction["state"] == "applied"
                        and pre_reader["state"] == "legacy_open"
                        and args.diagnostic_main_sha == args.main_sha
                    )
                    or (
                        transaction["state"] == "already_closed"
                        and pre_reader["state"] == "strict_closed"
                    )
                ),
                "fresh strict release reader postcheck failed",
            )
            receipt = _finalize_receipt({
                "format": FORMAT,
                "repository": args.repository,
                "main_sha": args.main_sha,
                "reviewed_sha": args.reviewed_sha,
                "production_binding_sha256": hashlib.sha256(
                    (FORMAT + "\0" + args.project_ref).encode()
                ).hexdigest(),
                "workflow": {
                    "run_id": int(args.workflow_run_id),
                    "run_attempt": int(args.workflow_run_attempt),
                },
                "authorization": {
                    "kind": args.authorization_kind,
                    "id": int(args.authorization_id),
                    "pull_request_number": int(args.pull_request_number),
                    "pr_ci_workflow_run_id": int(args.pr_ci_workflow_run_id),
                    "ci_workflow_run_id": int(args.ci_workflow_run_id),
                },
                "diagnostic": {
                    **diagnostic,
                    "main_sha": args.diagnostic_main_sha,
                    "workflow_run_id": int(args.diagnostic_workflow_run_id),
                    "workflow_run_attempt": int(
                        args.diagnostic_workflow_run_attempt
                    ),
                    "artifact_id": int(args.diagnostic_artifact_id),
                    "artifact_name": args.diagnostic_artifact_name,
                    "artifact_digest": args.diagnostic_artifact_digest,
                },
                "pre_reader": pre_reader,
                "transaction": transaction,
                "post_reader": post_reader,
                "lease": {
                    "owner": args.lease_owner,
                    "kind": "recovery",
                    "state": "awaiting_artifact_finalization",
                },
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "status": "verified",
            })
            _write_receipt(args.output, receipt)
            return receipt
        except BaseException:
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--pr-ci-workflow-run-id", required=True)
    parser.add_argument("--ci-workflow-run-id", required=True)
    parser.add_argument("--pull-request-number", required=True)
    parser.add_argument("--authorization-kind", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--diagnostic-workflow-run-id", required=True)
    parser.add_argument("--diagnostic-workflow-run-attempt", required=True)
    parser.add_argument("--diagnostic-main-sha", required=True)
    parser.add_argument("--diagnostic-artifact-id", required=True)
    parser.add_argument("--diagnostic-artifact-name", required=True)
    parser.add_argument("--diagnostic-artifact-digest", required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lease-owner", required=True)
    args = parser.parse_args()
    args.diagnostic = args.diagnostic.resolve()
    args.output = args.output.resolve()
    if (
        args.output.name != "release-reader-authority-closure.json"
        or not args.output.parent.is_dir()
        or args.output.exists()
        or args.output.parent.is_symlink()
    ):
        raise RuntimeError("closure output path must be a new bounded receipt")
    receipt = close_release_reader_authority(args, os.environ)
    print(canonical_json({
        "receipt_sha256": receipt["receipt_sha256"],
        "status": receipt["status"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
