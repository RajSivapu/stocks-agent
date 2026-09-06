#!/usr/bin/env python3
"""Reconcile the reviewed production schema through the Supabase Management API.

The runner is deliberately fail closed.  It accepts only an exact prior
read-only inventory, creates and verifies an encrypted full-row snapshot before
opening the write route, and sends the mutation as one database transaction.
No credential or application row is written to stdout or to a receipt.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Final
import xml.etree.ElementTree as ElementTree

from cryptography.fernet import Fernet, InvalidToken

# Direct execution starts with scripts/ on sys.path.
if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.deploy_owner_dashboard_api import (  # noqa: E402
    migration_statements_sha256,
    normalize_migration_statements,
)
from scripts.inspect_production_schema_baseline import (  # noqa: E402
    MAIN_SHA,
    MAX_RECEIPT_BYTES,
    MAX_ROOT_RELATIONS,
    MAX_ROOT_ROWS_PER_RELATION,
    RELATION_PRESENCE,
    _IDENTIFIER,
    _catalog_query,
    _root_relation_names,
    _validate_catalog,
    _validate_presence,
    _validate_roots,
    inspect_production_schema,
)
from scripts.managed_isolated_restore import (  # noqa: E402
    MAX_MANAGEMENT_RESPONSE_BYTES,
    PROJECT_REF,
    SupabaseManagementApi,
    canonical_json,
)


ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION_RELATIVE_PATH = Path(
    "sql/reconciliation/20261004_production_schema_reconciliation.sql"
)
RECONCILIATION_VERSION = "20261004"
AUTHORIZED_PRIOR_INVENTORY_RUN_ID = "34029103876"
AUTHORIZED_PRIOR_INVENTORY_HEAD_SHA = "849763522216394576ce54969693dd561b44af80"
AUTHORIZED_PRIOR_INVENTORY_RECEIPT_SHA256 = (
    "f1f08d635d59bb2e429c57deb1a2a9948d1ce631faa746bc8a19ea51372afb46"
)
RECEIPT_FORMAT = "stocks-production-schema-reconciliation-v1"
SNAPSHOT_FORMAT = "stocks-production-legacy-snapshot-v1"
MAX_RECONCILIATION_SQL_BYTES = 4 * 1024 * 1024
MAX_RECONCILIATION_RECEIPT_BYTES = 16 * 1024
MAX_SNAPSHOT_SIDECAR_BYTES = 16 * 1024
MAX_DB_STATEMENT_TIMEOUT_MS = 120_000
DB_LOCK_TIMEOUT_SECONDS = 10
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[1-9][0-9]*\Z")

ManagementRequest = Callable[[str, str, Mapping[str, object] | None], object]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_project_ref(project_ref: object) -> str:
    if not isinstance(project_ref, str) or PROJECT_REF.fullmatch(project_ref) is None:
        raise RuntimeError("exact production project identity is required")
    return project_ref


def _validate_main_sha(value: object, label: str = "main") -> str:
    if not isinstance(value, str) or MAIN_SHA.fullmatch(value) is None:
        raise RuntimeError(f"exact lowercase {label} SHA is required")
    return value


def _validate_run_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise RuntimeError(f"numeric {label} is required")
    return value


def validate_authorized_prior_binding(
    run_id: object, head_sha: object, receipt_sha256: object
) -> None:
    """Accept only the reviewed legacy inventory used to author the SQL."""
    if (
        run_id != AUTHORIZED_PRIOR_INVENTORY_RUN_ID
        or head_sha != AUTHORIZED_PRIOR_INVENTORY_HEAD_SHA
        or receipt_sha256 != AUTHORIZED_PRIOR_INVENTORY_RECEIPT_SHA256
    ):
        raise RuntimeError("authorized prior inventory binding is required")


def _canonical_bytes(value: object, label: str, maximum: int) -> bytes:
    try:
        raw = canonical_json(value).encode()
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} is malformed") from error
    if len(raw) > maximum:
        raise RuntimeError(f"{label} exceeds the size limit")
    return raw


def _read_bounded_json(path: Path, label: str, maximum: int) -> object:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
            raise RuntimeError(f"{label} is unavailable or exceeds the size limit")
        raw = path.read_bytes()
        if not raw or len(raw) > maximum:
            raise RuntimeError(f"{label} is unavailable or exceeds the size limit")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is malformed") from error


def validate_prior_inventory(
    path: Path,
    project_ref: str,
    prior_inventory_run_id: str,
    prior_head_sha: str,
) -> dict[str, object]:
    """Validate the downloaded receipt including its run head and project bind."""
    project_ref = _validate_project_ref(project_ref)
    prior_inventory_run_id = _validate_run_id(
        prior_inventory_run_id, "prior inventory run ID"
    )
    prior_head_sha = _validate_main_sha(prior_head_sha, "prior inventory head")
    if (
        prior_inventory_run_id != AUTHORIZED_PRIOR_INVENTORY_RUN_ID
        or prior_head_sha != AUTHORIZED_PRIOR_INVENTORY_HEAD_SHA
    ):
        raise RuntimeError("authorized prior inventory binding is required")
    value = _read_bounded_json(Path(path), "prior inventory receipt", MAX_RECEIPT_BYTES)
    expected_fields = {
        "format",
        "main_sha",
        "production_binding_sha256",
        "relation_presence",
        "catalog",
        "root_algorithm",
        "protected_roots",
        "receipt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise RuntimeError("prior inventory receipt is malformed")
    receipt = dict(value)
    digest = receipt.get("receipt_sha256")
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if (
        not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or digest != _sha256(_canonical_bytes(unsigned, "prior inventory receipt", MAX_RECEIPT_BYTES))
    ):
        raise RuntimeError("prior inventory receipt digest is invalid")
    if receipt.get("format") != "stocks-production-schema-inventory-v1":
        raise RuntimeError("prior inventory receipt format is invalid")
    if receipt.get("main_sha") != prior_head_sha:
        raise RuntimeError("prior inventory receipt run binding is invalid")
    validate_authorized_prior_binding(
        prior_inventory_run_id, prior_head_sha, digest
    )
    expected_binding = _sha256(
        ("stocks-production-schema-inventory-v1\\0" + project_ref).encode()
    )
    if receipt.get("production_binding_sha256") != expected_binding:
        raise RuntimeError("prior inventory receipt project binding is invalid")
    if receipt.get("root_algorithm") != "sha256-sorted-row-hashes-v1":
        raise RuntimeError("prior inventory root algorithm is invalid")

    catalog = _validate_catalog(receipt.get("catalog"))
    presence = _validate_presence(receipt.get("relation_presence"), catalog["relations"])
    relation_names = _root_relation_names(catalog["relations"])
    roots = _validate_roots(receipt.get("protected_roots"), relation_names)
    checked: dict[str, object] = {
        "format": receipt["format"],
        "main_sha": receipt["main_sha"],
        "production_binding_sha256": receipt["production_binding_sha256"],
        "relation_presence": presence,
        "catalog": catalog,
        "root_algorithm": receipt["root_algorithm"],
        "protected_roots": roots,
        "receipt_sha256": digest,
    }
    # The inspector emits sorted arrays and maps.  Accepting an alternative
    # representation would make the downloaded digest less meaningful.
    if canonical_json(checked) != canonical_json(receipt):
        raise RuntimeError("prior inventory receipt is noncanonical")
    return checked


def inventories_match(prior: Mapping[str, object], fresh: Mapping[str, object]) -> bool:
    """Compare complete inventory state while ignoring run-specific bindings."""
    ignored = {"main_sha", "receipt_sha256"}
    return canonical_json({k: v for k, v in prior.items() if k not in ignored}) == canonical_json(
        {k: v for k, v in fresh.items() if k not in ignored}
    )


def _legacy_shape(
    inventory: Mapping[str, object],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]], list[dict[str, object]]]:
    catalog = inventory.get("catalog")
    roots = inventory.get("protected_roots")
    if not isinstance(catalog, Mapping) or not isinstance(roots, list):
        raise RuntimeError("inventory legacy shape is malformed")
    relations = catalog.get("relations")
    columns = catalog.get("columns")
    if not isinstance(relations, list) or not isinstance(columns, list):
        raise RuntimeError("inventory legacy shape is malformed")
    names = _root_relation_names(relations)
    if len(names) > MAX_ROOT_RELATIONS:
        raise RuntimeError("inventory legacy relation count exceeds bounds")
    by_relation: dict[str, list[tuple[int, str]]] = {name: [] for name in names}
    for column in columns:
        if not isinstance(column, Mapping) or column.get("schema") != "public":
            continue
        relation = column.get("relation")
        name = column.get("name")
        position = column.get("position")
        if relation in by_relation:
            if (
                not isinstance(name, str)
                or _IDENTIFIER.fullmatch(name) is None
                or not isinstance(position, int)
                or position < 1
            ):
                raise RuntimeError("inventory legacy column identity is unsafe")
            by_relation[str(relation)].append((position, name))
    ordered: dict[str, tuple[str, ...]] = {}
    for relation in names:
        values = sorted(by_relation[relation])
        if not values or len({position for position, _ in values}) != len(values):
            raise RuntimeError("inventory legacy columns are incomplete")
        ordered[relation] = tuple(name for _, name in values)
    checked_roots = _validate_roots(roots, names)
    return names, ordered, checked_roots


def _root_sql(expression: str, relation: str, prefix: str) -> str:
    limit = MAX_ROOT_ROWS_PER_RELATION + 1
    return (
        f"{prefix}_{relation}_hashes AS MATERIALIZED ("
        f"SELECT extensions.digest(convert_to(({expression})::text, 'UTF8'), 'sha256') AS row_hash "
        f"FROM public.{relation} AS row LIMIT {limit}), "
        f"{prefix}_{relation}_stats AS MATERIALIZED ("
        "SELECT count(*)::bigint AS count, "
        "encode(extensions.digest(convert_to(COALESCE(string_agg(encode(row_hash, 'hex'), '' "
        "ORDER BY row_hash), ''), 'UTF8'), 'sha256'), 'hex') AS root_sha256 "
        f"FROM {prefix}_{relation}_hashes)"
    )


def _snapshot_query(
    names: Sequence[str],
    expected_roots: Sequence[Mapping[str, object]],
) -> str:
    expected = {str(item["relation"]): item for item in expected_roots}
    ctes: list[str] = []
    snapshot_pairs: list[str] = []
    root_items: list[str] = []
    validations: list[str] = []
    for index, relation in enumerate(names):
        rows = f"snapshot_{index}_rows"
        stats = f"snapshot_{index}_stats"
        ctes.append(
            f"{rows} AS MATERIALIZED (SELECT to_jsonb(row) AS row_json, "
            "extensions.digest(convert_to(to_jsonb(row)::text, 'UTF8'), 'sha256') AS row_hash "
            f"FROM public.{relation} AS row LIMIT {MAX_ROOT_ROWS_PER_RELATION + 1})"
        )
        ctes.append(
            f"{stats} AS MATERIALIZED (SELECT count(*)::bigint AS count, "
            "encode(extensions.digest(convert_to(COALESCE(string_agg(encode(row_hash, 'hex'), '' "
            "ORDER BY row_hash), ''), 'UTF8'), 'sha256'), 'hex') AS root_sha256 "
            f"FROM {rows})"
        )
        snapshot_pairs.extend(
            [
                _sql_literal(relation),
                f"COALESCE((SELECT jsonb_agg(row_json ORDER BY row_json::text) FROM {rows}), '[]'::jsonb)",
            ]
        )
        root_items.append(
            "jsonb_build_object('relation', "
            + _sql_literal(relation)
            + f", 'count', (SELECT count FROM {stats}), 'root_sha256', "
            + f"(SELECT root_sha256 FROM {stats}))"
        )
        expected_item = expected[relation]
        validations.append(
            f"((SELECT count FROM {stats}) = {int(expected_item['count'])} AND "
            f"(SELECT count FROM {stats}) <= {MAX_ROOT_ROWS_PER_RELATION} AND "
            f"(SELECT root_sha256 FROM {stats}) = {_sql_literal(str(expected_item['root_sha256']))})"
        )
    snapshot = "jsonb_build_object(" + ", ".join(snapshot_pairs) + ")" if snapshot_pairs else "'{}'::jsonb"
    roots = "jsonb_build_array(" + ", ".join(root_items) + ")" if root_items else "'[]'::jsonb"
    validation = " AND ".join(validations) if validations else "true"
    ctes.extend(
        [
            f"snapshot_payload AS MATERIALIZED (SELECT {snapshot} AS snapshot, {roots} AS protected_roots)",
            "snapshot_guard AS MATERIALIZED (SELECT 1 / CASE WHEN "
            + validation
            + " THEN 1 ELSE 0 END AS permitted)",
            "snapshot_size_guard AS MATERIALIZED (SELECT 1 / CASE WHEN "
            "octet_length(snapshot::text) + octet_length(protected_roots::text) <= "
            + str(MAX_MANAGEMENT_RESPONSE_BYTES - 1024 * 1024)
            + " THEN 1 ELSE 0 END AS permitted FROM snapshot_payload)",
        ]
    )
    return (
        "WITH "
        + ",\n".join(ctes)
        + "\nSELECT snapshot, protected_roots FROM snapshot_payload "
        "CROSS JOIN snapshot_guard CROSS JOIN snapshot_size_guard"
    )


def _read_only_query(
    request: ManagementRequest, project_ref: str, query: str, label: str
) -> list[dict[str, object]]:
    response = request(
        "POST", f"/v1/projects/{project_ref}/database/query/read-only", {"query": query}
    )
    _canonical_bytes(response, label, MAX_MANAGEMENT_RESPONSE_BYTES)
    if not isinstance(response, list) or not all(isinstance(row, Mapping) for row in response):
        raise RuntimeError(f"{label} is malformed")
    return [dict(row) for row in response]


def capture_legacy_snapshot(
    request: ManagementRequest, project_ref: str, inventory: Mapping[str, object]
) -> dict[str, object]:
    """Fetch every legacy public base/partitioned table in one bounded query."""
    project_ref = _validate_project_ref(project_ref)
    names, columns, expected_roots = _legacy_shape(inventory)
    rows = _read_only_query(
        request,
        project_ref,
        _snapshot_query(names, expected_roots),
        "legacy snapshot response",
    )
    if len(rows) != 1 or set(rows[0]) != {"snapshot", "protected_roots"}:
        raise RuntimeError("legacy snapshot response is malformed")
    raw_tables = rows[0]["snapshot"]
    if not isinstance(raw_tables, Mapping) or set(raw_tables) != set(names):
        raise RuntimeError("legacy snapshot table set is malformed")
    tables: dict[str, list[dict[str, object]]] = {}
    expected_by_name = {str(item["relation"]): item for item in expected_roots}
    for relation in names:
        values = raw_tables[relation]
        if not isinstance(values, list) or len(values) != expected_by_name[relation]["count"]:
            raise RuntimeError("legacy snapshot row counts do not match inventory")
        checked_rows: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(columns[relation]):
                raise RuntimeError("legacy snapshot row shape is malformed")
            checked_rows.append(dict(value))
        tables[relation] = checked_rows
    actual_roots = _validate_roots(rows[0]["protected_roots"], names)
    if canonical_json(actual_roots) != canonical_json(expected_roots):
        raise RuntimeError("legacy snapshot roots do not match inventory")
    payload: dict[str, object] = {
        "format": SNAPSHOT_FORMAT,
        "tables": tables,
        "protected_roots": actual_roots,
    }
    _canonical_bytes(payload, "legacy snapshot", MAX_MANAGEMENT_RESPONSE_BYTES)
    return payload


def _exclusive_atomic_write(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("output destination is unavailable or unsafe")
    pending = path.parent / ("." + path.name + ".pending")
    if pending.exists() or pending.is_symlink():
        raise RuntimeError("output destination is unavailable or unsafe")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        path.chmod(0o600)
    except BaseException:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_encrypted_snapshot(
    payload: Mapping[str, object],
    artifact: Path,
    key: bytes,
    *,
    prior_inventory_run_id: str,
    inventory_receipt_sha256: str,
) -> dict[str, object]:
    """Encrypt in memory, persist only ciphertext, and verify authentication."""
    prior_inventory_run_id = _validate_run_id(
        prior_inventory_run_id, "prior inventory run ID"
    )
    if not isinstance(inventory_receipt_sha256, str) or _DIGEST.fullmatch(
        inventory_receipt_sha256
    ) is None:
        raise RuntimeError("prior inventory receipt digest is invalid")
    plaintext = _canonical_bytes(payload, "legacy snapshot", MAX_MANAGEMENT_RESPONSE_BYTES)
    try:
        cipher = Fernet(key)
        ciphertext = cipher.encrypt(plaintext)
        if cipher.decrypt(ciphertext) != plaintext:
            raise RuntimeError("encrypted legacy snapshot self-check failed")
        altered = bytearray(ciphertext)
        altered[len(altered) // 2] ^= 1
        try:
            cipher.decrypt(bytes(altered))
        except InvalidToken:
            pass
        else:
            raise RuntimeError("encrypted legacy snapshot tamper self-check failed")
    except (ValueError, InvalidToken) as error:
        raise RuntimeError("release recovery key or encrypted snapshot is invalid") from error
    if len(ciphertext) > MAX_MANAGEMENT_RESPONSE_BYTES:
        raise RuntimeError("encrypted legacy snapshot exceeds the size limit")

    artifact = Path(artifact)
    sidecar_path = artifact.with_suffix(artifact.suffix + ".receipt.json")
    tables = payload.get("tables")
    roots = payload.get("protected_roots")
    if not isinstance(tables, Mapping) or not isinstance(roots, list):
        raise RuntimeError("legacy snapshot is malformed")
    sidecar: dict[str, object] = {
        "format": SNAPSHOT_FORMAT,
        "prior_inventory_run_id": prior_inventory_run_id,
        "inventory_receipt_sha256": inventory_receipt_sha256,
        "artifact_sha256": _sha256(ciphertext),
        "plaintext_sha256": _sha256(plaintext),
        "protected_roots_sha256": _sha256(canonical_json(roots).encode()),
        "table_count": len(tables),
        "row_count": sum(len(value) for value in tables.values() if isinstance(value, list)),
    }
    sidecar_raw = _canonical_bytes(
        sidecar, "legacy snapshot receipt", MAX_SNAPSHOT_SIDECAR_BYTES
    ) + b"\n"
    _exclusive_atomic_write(artifact, ciphertext)
    _exclusive_atomic_write(sidecar_path, sidecar_raw)
    # Verify the bytes actually persisted, not just the in-memory token.
    persisted = artifact.read_bytes()
    if _sha256(persisted) != sidecar["artifact_sha256"] or cipher.decrypt(persisted) != plaintext:
        raise RuntimeError("persisted encrypted legacy snapshot self-check failed")
    return sidecar


def verify_writer_identity(request: ManagementRequest, project_ref: str) -> dict[str, object]:
    """Prove the documented writer route is the privileged writable identity."""
    project_ref = _validate_project_ref(project_ref)
    query = (
        "SELECT current_user AS role, "
        "current_setting('transaction_read_only') AS transaction_read_only, "
        "current_database() AS database, role.rolsuper AS superuser "
        "FROM pg_catalog.pg_roles AS role WHERE role.rolname = current_user"
    )
    response = request(
        "POST", f"/v1/projects/{project_ref}/database/query", {"query": query}
    )
    _canonical_bytes(response, "writer identity response", MAX_MANAGEMENT_RESPONSE_BYTES)
    fields = {"role", "transaction_read_only", "database", "superuser"}
    if (
        not isinstance(response, list)
        or len(response) != 1
        or not isinstance(response[0], Mapping)
        or set(response[0]) != fields
        or response[0].get("role") != "postgres"
        or response[0].get("transaction_read_only") != "off"
        or response[0].get("superuser") is not True
        or not isinstance(response[0].get("database"), str)
        or not response[0].get("database")
    ):
        raise RuntimeError("writer identity is unavailable or unsafe")
    return dict(response[0])


def load_reconciliation_sql(repo_root: Path = ROOT) -> dict[str, object]:
    """Load only the fixed reconciliation path from the bound checkout."""
    root = Path(repo_root).resolve()
    path = root / RECONCILIATION_RELATIVE_PATH
    if (
        not path.is_file()
        or path.is_symlink()
        or path.parent.is_symlink()
        or path.parent.parent.is_symlink()
        or path.stat().st_size > MAX_RECONCILIATION_SQL_BYTES
    ):
        raise RuntimeError("immutable reconciliation SQL is unavailable or unsafe")
    raw = path.read_bytes()
    try:
        sql = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("immutable reconciliation SQL is not UTF-8") from error
    statements = normalize_migration_statements(sql)
    digest = migration_statements_sha256(statements)
    return {
        "path": RECONCILIATION_RELATIVE_PATH.as_posix(),
        "version": RECONCILIATION_VERSION,
        "sha256": digest,
        "statements": statements,
        "sql": sql,
    }


def _projection_expression(columns: Sequence[str]) -> str:
    return "jsonb_build_object(" + ", ".join(
        f"{_sql_literal(column)}, row.{column}" for column in columns
    ) + ")"


def _projected_roots_insert(
    table_name: str,
    names: Sequence[str],
    columns: Mapping[str, Sequence[str]],
    prefix: str,
) -> str:
    selects: list[str] = []
    for relation in names:
        expression = _projection_expression(columns[relation])
        hashes = f"{prefix}_{relation}_hashes"
        selects.append(
            "SELECT "
            + _sql_literal(relation)
            + "::text AS relation, count(*)::bigint AS count, "
            "encode(extensions.digest(convert_to(COALESCE(string_agg(encode(row_hash, 'hex'), '' "
            "ORDER BY row_hash), ''), 'UTF8'), 'sha256'), 'hex') AS root_sha256 "
            f"FROM (WITH {hashes} AS MATERIALIZED (SELECT extensions.digest(convert_to("
            f"({expression})::text, 'UTF8'), 'sha256') AS row_hash FROM public.{relation} AS row "
            f"LIMIT {MAX_ROOT_ROWS_PER_RELATION + 1}), cap_guard AS MATERIALIZED (SELECT 1 / CASE "
            f"WHEN count(*) <= {MAX_ROOT_ROWS_PER_RELATION} THEN 1 ELSE 0 END AS permitted FROM {hashes}) "
            f"SELECT row_hash FROM {hashes} CROSS JOIN cap_guard) AS bounded_hashes"
        )
    body = " UNION ALL ".join(selects)
    if not body:
        body = "SELECT NULL::text, 0::bigint, NULL::text WHERE false"
    return f"INSERT INTO {table_name}(relation, count, root_sha256) {body};"


def _root_assertion(table_name: str, expected_roots: Sequence[Mapping[str, object]], label: str) -> str:
    values = ", ".join(
        "("
        + _sql_literal(str(item["relation"]))
        + f", {int(item['count'])}::bigint, "
        + _sql_literal(str(item["root_sha256"]))
        + ")"
        for item in expected_roots
    )
    expected_relation = (
        f"(VALUES {values}) AS expected(relation, count, root_sha256)"
        if values
        else "(SELECT NULL::text AS relation, 0::bigint AS count, NULL::text AS root_sha256 WHERE false) AS expected"
    )
    return f"""
DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM {table_name} AS actual
    FULL JOIN {expected_relation} USING (relation)
    WHERE actual.relation IS NULL OR expected.relation IS NULL
       OR actual.count IS DISTINCT FROM expected.count
       OR actual.root_sha256 IS DISTINCT FROM expected.root_sha256
  ) THEN
    RAISE EXCEPTION '{label}' USING ERRCODE = '55000';
  END IF;
END;
$guard$;
""".strip()


def _catalog_guard_sql(prior_inventory: Mapping[str, object]) -> str:
    expected_catalog = _sql_literal(canonical_json(prior_inventory["catalog"]))
    expected_presence = _sql_literal(canonical_json(prior_inventory["relation_presence"]))
    catalog_query = _catalog_query()
    # The inspector validates catalog arrays after sorting them by canonical
    # JSON.  Normalize both JSONB values by their server representation here so
    # array order cannot create a false mismatch while membership remains exact.
    normalized_actual = """
(SELECT jsonb_object_agg(category, COALESCE((
   SELECT jsonb_agg(item ORDER BY item::text)
   FROM jsonb_array_elements(value) AS items(item)
 ), '[]'::jsonb))
 FROM jsonb_each(actual_catalog) AS category_rows(category, value))
""".strip()
    normalized_expected = """
(SELECT jsonb_object_agg(category, COALESCE((
   SELECT jsonb_agg(item ORDER BY item::text)
   FROM jsonb_array_elements(value) AS items(item)
 ), '[]'::jsonb))
 FROM jsonb_each(expected_catalog) AS category_rows(category, value))
""".strip()
    return f"""
DO $stock_agent_catalog_guard$
DECLARE
  actual_catalog jsonb;
  actual_presence jsonb;
  expected_catalog jsonb := {expected_catalog}::jsonb;
  expected_presence jsonb := {expected_presence}::jsonb;
BEGIN
  SELECT snapshot.catalog, snapshot.relation_presence
    INTO actual_catalog, actual_presence
  FROM ({catalog_query}) AS snapshot;
  IF {normalized_actual} IS DISTINCT FROM {normalized_expected}
     OR actual_presence IS DISTINCT FROM expected_presence THEN
    RAISE EXCEPTION 'reconciliation catalog or presence differs from inventory'
      USING ERRCODE = '55000';
  END IF;
END;
$stock_agent_catalog_guard$;
""".strip()


def build_mutation_sql(
    prior_inventory: Mapping[str, object], reconciliation: Mapping[str, object]
) -> str:
    """Build the sole writer request: one locked, guarded transaction."""
    names, columns, roots = _legacy_shape(prior_inventory)
    required_reconciliation = {"path", "version", "sha256", "statements", "sql"}
    if not isinstance(reconciliation, Mapping) or set(reconciliation) != required_reconciliation:
        raise RuntimeError("immutable reconciliation manifest is malformed")
    path = reconciliation["path"]
    version = reconciliation["version"]
    digest = reconciliation["sha256"]
    statements = reconciliation["statements"]
    sql = reconciliation["sql"]
    if (
        path != RECONCILIATION_RELATIVE_PATH.as_posix()
        or version != RECONCILIATION_VERSION
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or not isinstance(statements, list)
        or not statements
        or not all(isinstance(item, str) and item for item in statements)
        or not isinstance(sql, str)
        or normalize_migration_statements(sql) != statements
        or migration_statements_sha256(statements) != digest
    ):
        raise RuntimeError("immutable reconciliation manifest is malformed")
    locks = (
        "LOCK TABLE " + ", ".join(f"public.{name}" for name in names) + " IN SHARE MODE;"
        if names
        else ""
    )
    statement_literals = ", ".join(_sql_literal(item) for item in statements)
    return "\n".join(
        part
        for part in (
            "BEGIN;",
            f"SET LOCAL statement_timeout = '{MAX_DB_STATEMENT_TIMEOUT_MS // 1000}s';",
            f"SET LOCAL lock_timeout = '{DB_LOCK_TIMEOUT_SECONDS}s';",
            f"SET LOCAL idle_in_transaction_session_timeout = '{MAX_DB_STATEMENT_TIMEOUT_MS // 1000}s';",
            "DO $identity$ BEGIN IF current_user <> 'postgres' OR "
            "current_setting('transaction_read_only') <> 'off' OR NOT EXISTS ("
            "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = current_user AND rolsuper) "
            "THEN RAISE EXCEPTION 'unsafe reconciliation writer identity' USING ERRCODE = '42501'; "
            "END IF; END; $identity$;",
            "SELECT pg_advisory_xact_lock(hashtextextended('stock_agent_protected_release', 0));",
            locks,
            _catalog_guard_sql(prior_inventory),
            "CREATE TEMP TABLE pg_temp.stock_agent_pre_projection_guard ("
            "relation text PRIMARY KEY, count bigint NOT NULL, root_sha256 text NOT NULL) ON COMMIT DROP;",
            _projected_roots_insert(
                "pg_temp.stock_agent_pre_projection_guard", names, columns, "pre_projection"
            ),
            _root_assertion(
                "pg_temp.stock_agent_pre_projection_guard",
                roots,
                "pre-reconciliation projected roots differ from inventory",
            ),
            sql.strip(),
            "INSERT INTO supabase_migrations.schema_migrations(version, statements) VALUES ("
            f"{_sql_literal(str(version))}, ARRAY[{statement_literals}]::text[]);",
            "INSERT INTO public.stock_agent_release_migration_ledger(path, version, sha256) VALUES ("
            f"{_sql_literal(str(path))}, {_sql_literal(str(version))}, {_sql_literal(str(digest))});",
            "CREATE TEMP TABLE pg_temp.stock_agent_post_projection_guard ("
            "relation text PRIMARY KEY, count bigint NOT NULL, root_sha256 text NOT NULL) ON COMMIT DROP;",
            _projected_roots_insert(
                "pg_temp.stock_agent_post_projection_guard", names, columns, "post_projection"
            ),
            _root_assertion(
                "pg_temp.stock_agent_post_projection_guard",
                roots,
                "post-reconciliation projected roots differ from inventory",
            ),
            "COMMIT;",
        )
        if part
    )


def execute_with_uncertainty(
    writer: Callable[[], object], classify: Callable[[], str]
) -> dict[str, object]:
    """Execute once; after transport uncertainty, observe state before retrying."""
    try:
        writer()
        return {"status": "committed", "attempts": 1}
    except Exception as first_error:
        try:
            state = classify()
        except Exception as classify_error:
            raise RuntimeError("writer outcome is ambiguous; no retry was attempted") from classify_error
        if state == "committed":
            return {"status": "committed_after_unknown", "attempts": 1}
        if state != "proven_uncommitted":
            raise RuntimeError("writer outcome is ambiguous; no retry was attempted") from first_error
    try:
        writer()
        return {"status": "committed_after_retry", "attempts": 2}
    except Exception as retry_error:
        try:
            state = classify()
        except Exception as classify_error:
            raise RuntimeError("writer retry outcome is ambiguous") from classify_error
        if state == "committed":
            return {"status": "committed_after_retry", "attempts": 2}
        if state == "proven_uncommitted":
            raise RuntimeError("writer retry is proven uncommitted; retry limit reached") from retry_error
        raise RuntimeError("writer retry outcome is ambiguous") from retry_error


def _projected_roots_query(
    names: Sequence[str], columns: Mapping[str, Sequence[str]]
) -> str:
    parts: list[str] = []
    for relation in names:
        expression = _projection_expression(columns[relation])
        parts.append(
            "SELECT "
            + _sql_literal(relation)
            + "::text AS relation, count(*)::bigint AS count, "
            "encode(extensions.digest(convert_to(COALESCE(string_agg(encode(row_hash, 'hex'), '' "
            "ORDER BY row_hash), ''), 'UTF8'), 'sha256'), 'hex') AS root_sha256 "
            "FROM (WITH limited_hashes AS MATERIALIZED (SELECT extensions.digest(convert_to("
            f"({expression})::text, 'UTF8'), 'sha256') AS row_hash FROM public.{relation} AS row "
            f"LIMIT {MAX_ROOT_ROWS_PER_RELATION + 1}), cap_guard AS MATERIALIZED (SELECT 1 / CASE WHEN "
            f"count(*) <= {MAX_ROOT_ROWS_PER_RELATION} THEN 1 ELSE 0 END AS permitted FROM limited_hashes) "
            "SELECT row_hash FROM limited_hashes CROSS JOIN cap_guard) AS bounded_hashes"
        )
    roots = " UNION ALL ".join(parts)
    if not roots:
        roots = "SELECT NULL::text AS relation, 0::bigint AS count, NULL::text AS root_sha256 WHERE false"
    return (
        "WITH roots AS ("
        + roots
        + ") SELECT COALESCE(jsonb_agg(jsonb_build_object('relation', relation, 'count', count, "
        "'root_sha256', root_sha256) ORDER BY relation), '[]'::jsonb) AS protected_roots FROM roots"
    )


def _observer_query(
    names: Sequence[str], columns: Mapping[str, Sequence[str]]
) -> str:
    catalog_query = _catalog_query()
    roots_query = _projected_roots_query(names, columns)
    # query_to_xml defers parsing the two ledger relation names until execution.
    # That lets the same SELECT safely describe both the reviewed prestate (the
    # relations do not exist) and the final state without durable helper DDL.
    return f"""
WITH observer_lock AS MATERIALIZED (
  SELECT role.rolname AS role,
    current_setting('transaction_read_only') AS transaction_read_only,
    role.rolsuper AS superuser,
    settings.setting::int AS statement_timeout_ms,
    pg_try_advisory_xact_lock(hashtextextended('stock_agent_protected_release', 0)) AS lock_acquired
  FROM pg_catalog.pg_roles AS role
  JOIN pg_catalog.pg_settings AS settings ON settings.name = 'statement_timeout'
  WHERE role.rolname = current_user
), observed AS MATERIALIZED (
  SELECT catalog_snapshot.catalog, catalog_snapshot.relation_presence,
    root_snapshot.protected_roots,
    CASE WHEN pg_catalog.to_regclass('supabase_migrations.schema_migrations') IS NULL THEN NULL
      ELSE query_to_xml($native_receipts$
        SELECT version, cardinality(statements)::bigint AS statement_count,
          encode(extensions.digest(convert_to(array_to_json(statements)::text, 'UTF8'), 'sha256'), 'hex') AS statements_sha256
        FROM supabase_migrations.schema_migrations ORDER BY version
      $native_receipts$, true, false, '')::text END AS native_receipts_xml,
    CASE WHEN pg_catalog.to_regclass('public.stock_agent_release_migration_ledger') IS NULL THEN NULL
      ELSE query_to_xml($private_receipts$
        SELECT path, version, sha256 FROM public.stock_agent_release_migration_ledger ORDER BY path
      $private_receipts$, true, false, '')::text END AS private_receipts_xml
  FROM observer_lock AS lock
  CROSS JOIN LATERAL ({catalog_query}) AS catalog_snapshot
  CROSS JOIN LATERAL ({roots_query}) AS root_snapshot
  WHERE lock.lock_acquired
    AND lock.statement_timeout_ms BETWEEN 1 AND 120000
)
SELECT lock.lock_acquired, lock.role, lock.transaction_read_only, lock.superuser,
  lock.statement_timeout_ms, observed.catalog, observed.relation_presence,
  observed.protected_roots, observed.native_receipts_xml,
  observed.private_receipts_xml
FROM observer_lock AS lock
LEFT JOIN observed ON true
""".strip()


def _xml_receipt_rows(
    value: object, fields: tuple[str, ...], label: str
) -> list[dict[str, str]] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_MANAGEMENT_RESPONSE_BYTES:
        raise RuntimeError(f"{label} is malformed")
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError as error:
        raise RuntimeError(f"{label} is malformed") from error
    if root.tag.rsplit("}", 1)[-1] != "table":
        raise RuntimeError(f"{label} is malformed")
    result: list[dict[str, str]] = []
    for row in root:
        if row.tag.rsplit("}", 1)[-1] != "row":
            raise RuntimeError(f"{label} is malformed")
        item: dict[str, str] = {}
        for field in row:
            name = field.tag.rsplit("}", 1)[-1]
            if name in item or field.text is None or list(field):
                raise RuntimeError(f"{label} is malformed")
            item[name] = field.text
        if set(item) != set(fields):
            raise RuntimeError(f"{label} is malformed")
        result.append(item)
    return result


def _ledger_pair_matches(
    native_xml: object,
    private_xml: object,
    reconciliation: Mapping[str, object],
) -> bool:
    try:
        native = _xml_receipt_rows(
            native_xml,
            ("version", "statement_count", "statements_sha256"),
            "native reconciliation receipts",
        )
        private = _xml_receipt_rows(
            private_xml,
            ("path", "version", "sha256"),
            "private reconciliation receipts",
        )
    except RuntimeError:
        return False
    if native is None or private is None or len(native) != 1 or len(private) != 1:
        return False
    expected_statement_count = len(reconciliation["statements"])
    return native == [
        {
            "version": str(reconciliation["version"]),
            "statement_count": str(expected_statement_count),
            "statements_sha256": str(reconciliation["sha256"]),
        }
    ] and private == [
        {
            "path": str(reconciliation["path"]),
            "version": str(reconciliation["version"]),
            "sha256": str(reconciliation["sha256"]),
        }
    ]


def observe_reconciliation_state(
    request: ManagementRequest,
    project_ref: str,
    main_sha: str,
    prior_inventory: Mapping[str, object],
    reconciliation: Mapping[str, object],
) -> tuple[str, dict[str, object], list[dict[str, object]] | None]:
    project_ref = _validate_project_ref(project_ref)
    _validate_main_sha(main_sha)
    names, columns, expected_roots = _legacy_shape(prior_inventory)
    response = request(
        "POST",
        f"/v1/projects/{project_ref}/database/query",
        {"query": _observer_query(names, columns)},
    )
    _canonical_bytes(response, "locked reconciliation observation", MAX_MANAGEMENT_RESPONSE_BYTES)
    fields = {
        "lock_acquired",
        "role",
        "transaction_read_only",
        "superuser",
        "statement_timeout_ms",
        "catalog",
        "relation_presence",
        "protected_roots",
        "native_receipts_xml",
        "private_receipts_xml",
    }
    if (
        not isinstance(response, list)
        or len(response) != 1
        or not isinstance(response[0], Mapping)
        or set(response[0]) != fields
    ):
        raise RuntimeError("locked reconciliation observation is malformed")
    row = response[0]
    if (
        row.get("role") != "postgres"
        or row.get("transaction_read_only") != "off"
        or row.get("superuser") is not True
        or type(row.get("statement_timeout_ms")) is not int
        or not 1 <= row["statement_timeout_ms"] <= MAX_DB_STATEMENT_TIMEOUT_MS
    ):
        raise RuntimeError("locked reconciliation observer identity is unsafe")
    if row.get("lock_acquired") is not True:
        return "ambiguous", {"lock_acquired": False}, None
    catalog = _validate_catalog(row.get("catalog"))
    presence = _validate_presence(row.get("relation_presence"), catalog["relations"])
    projected = _validate_roots(row.get("protected_roots"), names)
    observation: dict[str, object] = {
        "lock_acquired": True,
        "catalog": catalog,
        "relation_presence": presence,
    }
    roots_match = canonical_json(projected) == canonical_json(expected_roots)
    is_exact_prestate = (
        canonical_json(catalog) == canonical_json(prior_inventory["catalog"])
        and canonical_json(presence) == canonical_json(prior_inventory["relation_presence"])
        and roots_match
        and row.get("native_receipts_xml") is None
        and row.get("private_receipts_xml") is None
    )
    if is_exact_prestate:
        return "proven_uncommitted", observation, projected
    if (
        roots_match
        and all(
            presence.get(f"{schema}.{relation}") is True
            for schema, relation in RELATION_PRESENCE
        )
        and _ledger_pair_matches(
            row.get("native_receipts_xml"),
            row.get("private_receipts_xml"),
            reconciliation,
        )
    ):
        return "committed", observation, projected
    return "ambiguous", observation, projected


_RECEIPT_FIELDS: Final[tuple[str, ...]] = (
    "main_sha",
    "ci_workflow_run_id",
    "prior_inventory_run_id",
    "prior_inventory_receipt_sha256",
    "reconciliation_path",
    "reconciliation_sha256",
    "snapshot_artifact_sha256",
    "snapshot_plaintext_sha256",
    "before_projected_roots_sha256",
    "after_projected_roots_sha256",
    "after_inventory_receipt_sha256",
    "transaction_status",
    "writer_attempts",
    "table_count",
    "row_count",
    "expected_relation_count",
)


def write_reconciliation_receipt(path: Path, evidence: Mapping[str, object]) -> dict[str, object]:
    """Write only the fixed bounded set of non-row reconciliation evidence."""
    receipt: dict[str, object] = {"format": RECEIPT_FORMAT}
    for field in _RECEIPT_FIELDS:
        if field not in evidence:
            raise RuntimeError("reconciliation receipt evidence is incomplete")
        receipt[field] = evidence[field]
    for field in (
        "prior_inventory_receipt_sha256",
        "reconciliation_sha256",
        "snapshot_artifact_sha256",
        "snapshot_plaintext_sha256",
        "before_projected_roots_sha256",
        "after_projected_roots_sha256",
        "after_inventory_receipt_sha256",
    ):
        if not isinstance(receipt[field], str) or _DIGEST.fullmatch(receipt[field]) is None:
            raise RuntimeError("reconciliation receipt digest is malformed")
    _validate_main_sha(receipt["main_sha"])
    _validate_run_id(receipt["ci_workflow_run_id"], "CI workflow run ID")
    _validate_run_id(receipt["prior_inventory_run_id"], "prior inventory run ID")
    if receipt["reconciliation_path"] != RECONCILIATION_RELATIVE_PATH.as_posix():
        raise RuntimeError("reconciliation receipt path is malformed")
    if receipt["before_projected_roots_sha256"] != receipt["after_projected_roots_sha256"]:
        raise RuntimeError("reconciliation projected roots changed")
    if receipt["transaction_status"] not in {
        "committed",
        "committed_after_unknown",
        "committed_after_retry",
    }:
        raise RuntimeError("reconciliation transaction status is malformed")
    if (
        type(receipt["writer_attempts"]) is not int
        or receipt["writer_attempts"] not in {1, 2}
        or any(
            type(receipt[field]) is not int or receipt[field] < 0
            for field in ("table_count", "row_count", "expected_relation_count")
        )
    ):
        raise RuntimeError("reconciliation receipt counts are malformed")
    receipt["receipt_sha256"] = _sha256(
        _canonical_bytes(receipt, "reconciliation receipt", MAX_RECONCILIATION_RECEIPT_BYTES)
    )
    raw = _canonical_bytes(
        receipt, "reconciliation receipt", MAX_RECONCILIATION_RECEIPT_BYTES - 1
    ) + b"\n"
    _exclusive_atomic_write(Path(path), raw)
    return receipt


def _writer_call(
    request: ManagementRequest, project_ref: str, mutation_sql: str
) -> object:
    response = request(
        "POST", f"/v1/projects/{project_ref}/database/query", {"query": mutation_sql}
    )
    _canonical_bytes(response, "reconciliation writer response", MAX_MANAGEMENT_RESPONSE_BYTES)
    if not isinstance(response, list):
        raise RuntimeError("reconciliation writer response is malformed")
    return response


def run_reconciliation(
    request: ManagementRequest,
    *,
    project_ref: str,
    recovery_key: bytes,
    prior_inventory_path: Path,
    prior_inventory_run_id: str,
    prior_inventory_head_sha: str,
    main_sha: str,
    ci_workflow_run_id: str,
    output_dir: Path,
    repo_root: Path = ROOT,
) -> dict[str, object]:
    project_ref = _validate_project_ref(project_ref)
    prior_inventory_run_id = _validate_run_id(
        prior_inventory_run_id, "prior inventory run ID"
    )
    if (
        prior_inventory_run_id != AUTHORIZED_PRIOR_INVENTORY_RUN_ID
        or prior_inventory_head_sha != AUTHORIZED_PRIOR_INVENTORY_HEAD_SHA
    ):
        raise RuntimeError("authorized prior inventory binding is required")
    ci_workflow_run_id = _validate_run_id(ci_workflow_run_id, "CI workflow run ID")
    main_sha = _validate_main_sha(main_sha)
    prior = validate_prior_inventory(
        prior_inventory_path,
        project_ref,
        prior_inventory_run_id,
        prior_inventory_head_sha,
    )
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_dir() or output_dir.is_symlink() or any(output_dir.iterdir()):
        raise RuntimeError("reconciliation output directory must be an empty exact directory")

    fresh = inspect_production_schema(request, project_ref, main_sha)
    if not inventories_match(prior, fresh):
        raise RuntimeError("fresh production inventory differs from the prior receipt")
    snapshot = capture_legacy_snapshot(request, project_ref, prior)
    snapshot_sidecar = write_encrypted_snapshot(
        snapshot,
        output_dir / "legacy-snapshot.enc",
        recovery_key,
        prior_inventory_run_id=prior_inventory_run_id,
        inventory_receipt_sha256=str(prior["receipt_sha256"]),
    )
    # Close the interval between the atomic snapshot and the writer request.
    fresh_after_snapshot = inspect_production_schema(request, project_ref, main_sha)
    if not inventories_match(prior, fresh_after_snapshot):
        raise RuntimeError("production inventory changed after the encrypted snapshot")
    verify_writer_identity(request, project_ref)
    reconciliation = load_reconciliation_sql(repo_root)
    mutation_sql = build_mutation_sql(prior, reconciliation)

    def classify() -> str:
        state, _, _ = observe_reconciliation_state(
            request, project_ref, main_sha, prior, reconciliation
        )
        return state

    transaction = execute_with_uncertainty(
        lambda: _writer_call(request, project_ref, mutation_sql), classify
    )
    state, locked_observation, projected = observe_reconciliation_state(
        request, project_ref, main_sha, prior, reconciliation
    )
    if state != "committed" or projected is None:
        raise RuntimeError("reconciliation postcondition is not a proven commit")
    after_inventory = inspect_production_schema(request, project_ref, main_sha)
    if (
        canonical_json(after_inventory["catalog"])
        != canonical_json(locked_observation["catalog"])
        or canonical_json(after_inventory["relation_presence"])
        != canonical_json(locked_observation["relation_presence"])
        or not all(
            after_inventory["relation_presence"].get(f"{schema}.{relation}") is True
            for schema, relation in RELATION_PRESENCE
        )
    ):
        raise RuntimeError("post-reconciliation inventory differs from the locked observation")
    before_roots_sha = _sha256(canonical_json(prior["protected_roots"]).encode())
    after_roots_sha = _sha256(canonical_json(projected).encode())
    receipt = write_reconciliation_receipt(
        output_dir / "schema-reconciliation-receipt.json",
        {
            "main_sha": main_sha,
            "ci_workflow_run_id": ci_workflow_run_id,
            "prior_inventory_run_id": prior_inventory_run_id,
            "prior_inventory_receipt_sha256": prior["receipt_sha256"],
            "reconciliation_path": reconciliation["path"],
            "reconciliation_sha256": reconciliation["sha256"],
            "snapshot_artifact_sha256": snapshot_sidecar["artifact_sha256"],
            "snapshot_plaintext_sha256": snapshot_sidecar["plaintext_sha256"],
            "before_projected_roots_sha256": before_roots_sha,
            "after_projected_roots_sha256": after_roots_sha,
            "after_inventory_receipt_sha256": after_inventory["receipt_sha256"],
            "transaction_status": transaction["status"],
            "writer_attempts": transaction["attempts"],
            "table_count": snapshot_sidecar["table_count"],
            "row_count": snapshot_sidecar["row_count"],
            "expected_relation_count": len(RELATION_PRESENCE),
        },
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-inventory", required=True, type=Path)
    parser.add_argument("--prior-inventory-run-id", required=True)
    parser.add_argument("--prior-inventory-head-sha", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--ci-workflow-run-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    project_ref = os.environ.get("SUPABASE_PROJECT_REF", "")
    recovery_key = os.environ.get("RELEASE_RECOVERY_KEY", "").encode()
    if not token or not project_ref or not recovery_key:
        raise RuntimeError("required protected reconciliation environment is unavailable")
    receipt = run_reconciliation(
        SupabaseManagementApi(token),
        project_ref=project_ref,
        recovery_key=recovery_key,
        prior_inventory_path=args.prior_inventory,
        prior_inventory_run_id=args.prior_inventory_run_id,
        prior_inventory_head_sha=args.prior_inventory_head_sha,
        main_sha=args.main_sha,
        ci_workflow_run_id=args.ci_workflow_run_id,
        output_dir=args.output_dir,
    )
    print(
        canonical_json(
            {
                "status": "written",
                "receipt_sha256": receipt["receipt_sha256"],
                "snapshot_artifact_sha256": receipt["snapshot_artifact_sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
