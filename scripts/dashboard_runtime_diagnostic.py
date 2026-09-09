#!/usr/bin/env python3
"""Emit bounded stage evidence for the protected dashboard runtime probe.

The diagnostic authenticates with the exact retained runtime URL used by the
release candidate. It never prints the URL, exception text, or projection rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
import re
import sys
from typing import Any, Final
from urllib.parse import unquote, urlparse

import psycopg
from psycopg.rows import dict_row

if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.managed_isolated_restore import canonical_json  # noqa: E402
from scripts.owner_intelligence_contract import validate_owner_intelligence_v2  # noqa: E402
from scripts.verify_owner_dashboard_role import (  # noqa: E402
    EXPECTED_COLUMNS,
    EXPECTED_FUNCTIONS,
    EXPECTED_PRIVILEGE_ATTRIBUTES,
    EXPECTED_RUNTIME_ATTRIBUTES,
    PRIVILEGE_ROLE,
    RUNTIME_ROLE,
    collect_dashboard_privileges,
    evaluate_dashboard_privileges,
)


FORMAT: Final = "stocks-dashboard-runtime-diagnostic-v1"
MAX_SAMPLE_ITEMS = 32
MAX_RECEIPT_BYTES = 32_768
_MAIN_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PROJECT_REF = re.compile(r"[a-z0-9]{20}\Z")

_AUTHORITY_ERROR_CODES: Final = {
    "dashboard runtime role is missing or cannot login": "runtime_role_unavailable",
    "dashboard runtime role may bypass RLS": "runtime_role_bypasses_rls",
    "dashboard runtime role has unsafe role authority": "runtime_role_authority_mismatch",
    "dashboard privilege role has unsafe role authority": "privilege_role_authority_mismatch",
    "dashboard runtime membership is not exact": "runtime_membership_mismatch",
    "dashboard privilege role membership is not exact": "privilege_membership_mismatch",
    "dashboard privilege role members are not exact": "privilege_members_mismatch",
    "dashboard runtime role has incoming members": "runtime_incoming_members",
    "unexpected dashboard database privilege": "unexpected_database_privilege",
    "unexpected dashboard schema privilege": "unexpected_schema_privilege",
    "unexpected dashboard schema privilege outside public": "unexpected_schema_privilege_outside_public",
    "unexpected dashboard table privilege": "unexpected_table_privilege",
    "unexpected dashboard sequence privilege": "unexpected_sequence_privilege",
    "dashboard column privileges differ from the allowlist": "column_privilege_mismatch",
    "dashboard executable function allowlist differs": "function_execute_mismatch",
    "dashboard role has object ownership": "owned_object_mismatch",
    "dashboard policy coverage is incomplete": "policy_coverage_mismatch",
}


def _sha256(items: list[str]) -> str:
    return hashlib.sha256("\0".join(items).encode()).hexdigest()


def _set_summary(actual: set[str], expected: set[str] | None = None) -> dict[str, object]:
    ordered = sorted(actual)
    result: dict[str, object] = {
        "count": len(ordered),
        "sha256": _sha256(ordered),
        "sample": ordered[:MAX_SAMPLE_ITEMS],
    }
    if expected is not None:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        result.update({
            "missing_count": len(missing),
            "missing_sample": missing[:MAX_SAMPLE_ITEMS],
            "unexpected_count": len(unexpected),
            "unexpected_sample": unexpected[:MAX_SAMPLE_ITEMS],
        })
    return result


def _flatten_privileges(value: object) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    return {
        f"{name}:{privilege}"
        for name, privileges in value.items()
        if isinstance(name, str) and isinstance(privileges, (set, list, tuple))
        for privilege in privileges
        if isinstance(privilege, str)
    }


def _flatten_columns(value: object) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    return {
        f"{table}.{column}"
        for table, columns in value.items()
        if isinstance(table, str) and isinstance(columns, (set, list, tuple))
        for column in columns
        if isinstance(column, str)
    }


def _policy_summary(value: object) -> dict[str, object]:
    policies = value if isinstance(value, Mapping) else {}
    actual_tables = {str(table) for table in policies}
    expected_tables = set(EXPECTED_COLUMNS)
    unsafe = {
        str(table)
        for table, policy in policies.items()
        if not isinstance(policy, Mapping)
        or policy.get("cmd") != "SELECT"
        or policy.get("roles") != [PRIVILEGE_ROLE]
    }
    summary = _set_summary(actual_tables, expected_tables)
    summary["unsafe_count"] = len(unsafe)
    summary["unsafe_sample"] = sorted(unsafe)[:MAX_SAMPLE_ITEMS]
    return summary


def _snapshot_summary(snapshot: Mapping[str, Any]) -> dict[str, object]:
    expected_columns = {
        f"{table}.{column}" for table, columns in EXPECTED_COLUMNS.items() for column in columns
    }
    actual_functions = {
        value for value in snapshot.get("application_function_execute", []) if isinstance(value, str)
    }
    owned = {value for value in snapshot.get("owned_objects", []) if isinstance(value, str)}
    memberships = {value for value in snapshot.get("memberships", []) if isinstance(value, str)}
    privilege_memberships = {
        value for value in snapshot.get("privilege_memberships", []) if isinstance(value, str)
    }
    runtime_members = {value for value in snapshot.get("runtime_members", []) if isinstance(value, str)}
    privilege_members = {
        canonical_json(value)
        for value in snapshot.get("privilege_members", [])
        if isinstance(value, Mapping)
    }
    return {
        "runtime_role_exact": snapshot.get("role") == EXPECTED_RUNTIME_ATTRIBUTES,
        "privilege_role_exact": snapshot.get("privilege_role_state") == EXPECTED_PRIVILEGE_ATTRIBUTES,
        "memberships": _set_summary(memberships, {PRIVILEGE_ROLE}),
        "privilege_memberships": _set_summary(privilege_memberships, set()),
        "privilege_members": _set_summary(privilege_members),
        "runtime_members": _set_summary(runtime_members, set()),
        "database_privileges": _set_summary(
            {str(value) for value in snapshot.get("database_privileges", set())},
            {"CONNECT", "TEMPORARY"},
        ),
        "public_schema_privileges": _set_summary(
            {str(value) for value in snapshot.get("schema_privileges", set())}, {"USAGE"}
        ),
        "other_schema_privileges": _set_summary(
            _flatten_privileges(snapshot.get("other_schema_privileges"))
        ),
        "table_privileges": _set_summary(_flatten_privileges(snapshot.get("table_privileges"))),
        "sequence_privileges": _set_summary(_flatten_privileges(snapshot.get("sequence_privileges"))),
        "column_privileges": _set_summary(
            _flatten_columns(snapshot.get("column_privileges")), expected_columns
        ),
        "function_execute": _set_summary(actual_functions, set(EXPECTED_FUNCTIONS)),
        "owned_objects": _set_summary(owned, set()),
        "policies": _policy_summary(snapshot.get("policies")),
    }


def _query_stage(statement: object) -> str:
    query = str(statement)
    if "has_function_privilege" in query:
        return "function_privileges"
    if "has_column_privilege" in query:
        return "column_privileges"
    if "has_sequence_privilege" in query:
        return "sequence_privileges"
    if "has_table_privilege" in query:
        return "table_privileges"
    if "has_schema_privilege" in query:
        return "schema_privileges"
    if "has_database_privilege" in query:
        return "database_privileges"
    if "pg_auth_members" in query:
        return "role_memberships"
    if "pg_policies" in query:
        return "policies"
    if "pg_proc" in query and "proowner" in query:
        return "function_ownership"
    if "pg_class" in query and "relowner" in query:
        return "relation_ownership"
    if "pg_database" in query and "datdba" in query:
        return "database_ownership"
    if "pg_namespace" in query and "nspowner" in query:
        return "schema_ownership"
    if "pg_roles" in query:
        return "role_attributes"
    return "authority_catalog"


class _TracingCursor:
    def __init__(self, cursor, connection):
        self._cursor = cursor
        self._connection = connection

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self._cursor.__exit__(*args)

    _PARAMETERS_OMITTED = object()

    def execute(self, statement, parameters=_PARAMETERS_OMITTED):
        self._connection.query_stage = _query_stage(statement)
        if parameters is self._PARAMETERS_OMITTED:
            return self._cursor.execute(statement)
        return self._cursor.execute(statement, parameters)

    def fetchall(self):
        return self._cursor.fetchall()


class _TracingConnection:
    def __init__(self, connection):
        self._connection = connection
        self.query_stage = "authority_catalog"

    def cursor(self, **kwargs):
        return _TracingCursor(self._connection.cursor(**kwargs), self)


def _database_error_code(error: Exception) -> str:
    if isinstance(error, psycopg.errors.QueryCanceled):
        return "query_timeout"
    if isinstance(error, psycopg.OperationalError):
        return "connection_error"
    if isinstance(error, psycopg.Error):
        return "database_error"
    return "diagnostic_error"


def _validated_runtime_url(environment: Mapping[str, str], project_ref: str) -> str:
    raw = environment.get("DASHBOARD_PRIOR_MANAGED_SECRETS_JSON", "")
    try:
        secrets = json.loads(raw)
        value = secrets["DASHBOARD_DATABASE_URL"]
        parsed = urlparse(value)
        port = parsed.port
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("credential_source_invalid") from error
    if (
        not isinstance(secrets, Mapping)
        or not isinstance(value, str)
        or parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.hostname.endswith(".pooler.supabase.com")
        or port != 5432
        or unquote(parsed.username or "") != f"{RUNTIME_ROLE}.{project_ref}"
        or len(unquote(parsed.password or "")) < 24
        or parsed.path != "/postgres"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("credential_url_invalid")
    return value


def _finalize(receipt: dict[str, object]) -> dict[str, object]:
    receipt["receipt_sha256"] = hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    if len(canonical_json(receipt).encode()) > MAX_RECEIPT_BYTES:
        raise RuntimeError("diagnostic receipt exceeds bound")
    return receipt


def diagnose_dashboard_runtime(
    environment: Mapping[str, str],
    project_ref: str,
    main_sha: str,
    *,
    connector: Callable[..., Any] = psycopg.connect,
    collector: Callable[[Any], dict[str, Any]] = collect_dashboard_privileges,
    evaluator: Callable[[dict[str, Any]], dict[str, object]] = evaluate_dashboard_privileges,
    projection_validator: Callable[[object], object] = validate_owner_intelligence_v2,
) -> dict[str, object]:
    if not _PROJECT_REF.fullmatch(project_ref) or not _MAIN_SHA.fullmatch(main_sha):
        raise ValueError("diagnostic binding is malformed")
    receipt: dict[str, object] = {
        "format": FORMAT,
        "main_sha": main_sha,
        "production_binding_sha256": hashlib.sha256((FORMAT + "\0" + project_ref).encode()).hexdigest(),
        "credential": {"status": "not_run"},
        "connection": {"status": "not_run"},
        "authority": {"status": "not_run"},
        "identity": {"status": "not_run"},
        "projection": {"status": "not_run"},
    }
    try:
        database_url = _validated_runtime_url(environment, project_ref)
    except ValueError as error:
        receipt["credential"] = {"status": "failed", "error_code": str(error)}
        return _finalize(receipt)
    receipt["credential"] = {"status": "valid"}

    try:
        context = connector(database_url, row_factory=dict_row, connect_timeout=15)
        with context as connection:
            receipt["connection"] = {"status": "connected"}
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute("SET LOCAL lock_timeout = '2s'")

            traced = _TracingConnection(connection)
            try:
                snapshot = collector(traced)
            except Exception as error:
                authority: dict[str, object] = {
                    "status": "failed",
                    "error_code": _database_error_code(error),
                    "query_stage": traced.query_stage,
                }
                receipt["authority"] = authority
                return _finalize(receipt)

            try:
                result = evaluator(snapshot)
            except RuntimeError as error:
                receipt["authority"] = {
                    "status": "failed",
                    "error_code": _AUTHORITY_ERROR_CODES.get(str(error), "authority_mismatch"),
                    "snapshot": _snapshot_summary(snapshot),
                }
            else:
                receipt["authority"] = {
                    "status": "verified" if result.get("status") == "verified" else "failed"
                }

            try:
                identity = connection.execute(
                    """SELECT current_user AS database_user,
                              current_setting('transaction_read_only') AS transaction_read_only"""
                ).fetchone()
                if (
                    not isinstance(identity, Mapping)
                    or identity.get("database_user") != RUNTIME_ROLE
                    or identity.get("transaction_read_only") != "on"
                ):
                    receipt["identity"] = {"status": "failed", "error_code": "identity_mismatch"}
                else:
                    receipt["identity"] = {"status": "verified"}
            except Exception as error:
                receipt["identity"] = {"status": "failed", "error_code": _database_error_code(error)}
                return _finalize(receipt)

            try:
                projection_row = connection.execute(
                    "SELECT public.read_owner_intelligence_v2(1) AS projection"
                ).fetchone()
                if not isinstance(projection_row, Mapping):
                    raise ValueError("projection row unavailable")
                projection_validator(projection_row.get("projection"))
            except psycopg.Error as error:
                receipt["projection"] = {"status": "failed", "error_code": _database_error_code(error)}
            except Exception:
                receipt["projection"] = {"status": "failed", "error_code": "contract_invalid"}
            else:
                receipt["projection"] = {"status": "verified"}
    except Exception as error:
        receipt["connection"] = {"status": "failed", "error_code": _database_error_code(error)}
    return _finalize(receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.name != "dashboard-runtime-diagnostic.json" or not output.parent.is_dir():
        raise RuntimeError("output must be dashboard-runtime-diagnostic.json in an existing directory")
    receipt = diagnose_dashboard_runtime(os.environ, args.project_ref, args.main_sha)
    output.write_text(canonical_json(receipt) + "\n")
    output.chmod(0o600)
    print(canonical_json({"receipt_sha256": receipt["receipt_sha256"], "status": "written"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
