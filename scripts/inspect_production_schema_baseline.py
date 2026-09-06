#!/usr/bin/env python3
"""Create a bounded, read-only production schema inventory receipt.

This script deliberately uses only the Management API read-only query route.
It records catalog metadata, relation presence, counts, and server-calculated
roots; it never receives or writes portfolio rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
import re
import sys
from typing import Final

# Direct script execution starts with ``scripts/`` on sys.path rather than the
# repository root.  Keep package imports explicit and runner-independent.
if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.managed_isolated_restore import (  # noqa: E402
    MAX_MANAGEMENT_RESPONSE_BYTES,
    PROJECT_REF,
    SupabaseManagementApi,
    canonical_json,
)


MAIN_SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_CATALOG_ROWS = 10_000
MAX_ROOT_RELATIONS = 256
MAX_ROLE_MEMBERSHIPS = 64
MAX_RECEIPT_BYTES = 1_000_000
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")

# These are the complete recovery relation set plus the native migration
# marker.  The matrix is deliberately fixed so a partially applied migration
# cannot be hidden by an incomplete catalog response.
RECOVERY_PUBLIC_RELATIONS: Final[tuple[str, ...]] = (
    "analysis_runs",
    "decision_evaluations",
    "holdings",
    "market_collection_checkpoint_history",
    "market_collection_checkpoints",
    "market_evidence_packets",
    "market_gateway_requests",
    "market_intelligence_collection_completions",
    "market_intelligence_run_events",
    "market_intelligence_runs",
    "market_policy_comparisons",
    "market_policy_config",
    "market_publications",
    "market_report_publications",
    "market_report_request_origins",
    "market_reports",
    "market_run_terminal_outcomes",
    "market_source_quota_reservations",
    "portfolio_cash_ledger_state",
    "portfolio_command_acknowledgements",
    "portfolio_commands",
    "reconciled_cash_snapshots",
    "stock_agent_release_migration_ledger",
    "transactions",
)
POST_20260909_MARKER_RELATIONS: Final[tuple[str, ...]] = (
    "market_checkpoint_receipt_lineage",
    "market_intelligence_context_inputs",
    "market_intelligence_quote_attempts",
    "market_run_source_item_provenance",
    "market_scheduled_phase_deadlines",
    "market_source_item_provenance",
)
RELATION_PRESENCE: Final[tuple[tuple[str, str], ...]] = tuple(
    ("public", name) for name in (*RECOVERY_PUBLIC_RELATIONS, *POST_20260909_MARKER_RELATIONS)
) + (("supabase_migrations", "schema_migrations"),)

AUTHORIZATION_ROLES: Final[tuple[str, ...]] = (
    "stock_agent_dashboard",
    "stock_agent_dashboard_runtime",
    "stock_agent_release_reader",
    "stock_agent_release_reader_runtime",
)

_CATALOG_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "relations": ("schema", "name", "kind", "row_security", "force_row_security", "owner"),
    "columns": ("schema", "relation", "name", "position", "type", "not_null", "has_default", "default_sha256", "identity", "generated"),
    "constraints": ("schema", "relation", "name", "kind", "definition_sha256"),
    "functions": ("schema", "identity", "kind", "owner", "language", "volatility", "security_definer", "definition_sha256"),
    "indexes": ("schema", "relation", "name", "definition_sha256", "valid", "ready", "live"),
    "triggers": ("schema", "relation", "name", "enabled", "definition_sha256"),
    "policies": ("schema", "relation", "name", "command", "permissive", "roles", "using_sha256", "check_sha256"),
    "acls": ("object_kind", "schema", "object", "grantee", "grantor", "privilege", "grantable"),
    "column_acls": ("schema", "relation", "column", "grantee", "grantor", "privilege", "grantable"),
    "schema_acls": ("schema", "grantee", "grantor", "privilege", "grantable"),
    "default_privileges": ("scope", "owner", "object_type", "grantee", "grantor", "privilege", "grantable"),
    "roles": ("name", "exists", "login", "inherit", "superuser", "bypassrls", "createrole", "createdb", "replication", "member_of"),
    "memberships": ("member", "role", "grantor", "admin_option", "inherit_option", "set_option"),
}

_CATALOG_IDENTITIES: Final[dict[str, tuple[str, ...]]] = {
    "relations": ("schema", "name"),
    "columns": ("schema", "relation", "name"),
    "constraints": ("schema", "relation", "name"),
    "functions": ("schema", "identity"),
    "indexes": ("schema", "relation", "name"),
    "triggers": ("schema", "relation", "name"),
    "policies": ("schema", "relation", "name"),
    "acls": ("object_kind", "schema", "object", "grantee", "grantor", "privilege"),
    "column_acls": ("schema", "relation", "column", "grantee", "grantor", "privilege"),
    "schema_acls": ("schema", "grantee", "grantor", "privilege"),
    "default_privileges": ("scope", "owner", "object_type", "grantee", "grantor", "privilege"),
    "roles": ("name",),
    "memberships": ("member", "role", "grantor"),
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _query(request, project_ref: str, query: str) -> list[dict[str, object]]:
    response = request("POST", f"/v1/projects/{project_ref}/database/query/read-only", {"query": query})
    try:
        encoded = canonical_json(response).encode()
    except (TypeError, ValueError) as error:
        raise RuntimeError("read-only schema response is malformed") from error
    if len(encoded) > MAX_MANAGEMENT_RESPONSE_BYTES:
        raise RuntimeError("read-only schema response exceeds limit")
    if not isinstance(response, list) or not all(isinstance(row, Mapping) for row in response):
        raise RuntimeError("read-only schema response is malformed")
    return [dict(row) for row in response]


def _identity_query() -> str:
    return (
        "SELECT current_user AS role, current_setting('transaction_read_only') AS transaction_read_only, "
        "current_database() AS database"
    )


def _catalog_query() -> str:
    presence_values = ",".join("('%s','%s')" % pair for pair in RELATION_PRESENCE)
    return f"""
WITH expected(schema_name, relation_name) AS (VALUES {presence_values}),
expected_roles(role_name) AS (VALUES {','.join("('%s')" % role for role in AUTHORIZATION_ROLES)}),
relations AS (
  SELECT jsonb_build_object('schema', n.nspname, 'name', c.relname, 'kind', c.relkind::text,
    'row_security', c.relrowsecurity, 'force_row_security', c.relforcerowsecurity, 'owner', owner.rolname) AS item
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i', 'I', 'c')
), columns AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', c.relname, 'name', a.attname,
    'position', a.attnum, 'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
    'not_null', a.attnotnull, 'has_default', a.atthasdef,
    'default_sha256', encode(extensions.digest(convert_to(COALESCE(pg_catalog.pg_get_expr(d.adbin, d.adrelid), ''), 'UTF8'), 'sha256'), 'hex'),
    'identity', a.attidentity,
    'generated', a.attgenerated) AS item
  FROM pg_catalog.pg_attribute AS a
  JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  LEFT JOIN pg_catalog.pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i', 'I', 'c')
    AND a.attnum > 0 AND NOT a.attisdropped
), constraints AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', c.relname, 'name', con.conname,
    'kind', con.contype::text, 'definition_sha256',
    encode(extensions.digest(convert_to(pg_catalog.pg_get_constraintdef(con.oid, true), 'UTF8'), 'sha256'), 'hex')) AS item
  FROM pg_catalog.pg_constraint AS con
  JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
), functions AS (
  SELECT jsonb_build_object('schema', n.nspname, 'identity', p.proname || '(' || pg_catalog.pg_get_function_identity_arguments(p.oid) || ')',
    'kind', p.prokind::text, 'owner', owner.rolname, 'language', l.lanname, 'volatility', p.provolatile::text, 'security_definer', p.prosecdef,
    'definition_sha256', encode(extensions.digest(convert_to(CASE WHEN p.prokind IN ('f', 'p', 'w')
      THEN pg_catalog.pg_get_functiondef(p.oid) ELSE COALESCE(to_jsonb(aggregate)::text, '') END, 'UTF8'), 'sha256'), 'hex')) AS item
  FROM pg_catalog.pg_proc AS p
  JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
  JOIN pg_catalog.pg_language AS l ON l.oid = p.prolang
  JOIN pg_catalog.pg_roles AS owner ON owner.oid = p.proowner
  LEFT JOIN pg_catalog.pg_aggregate AS aggregate ON aggregate.aggfnoid = p.oid
  WHERE n.nspname = 'public'
), indexes AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', table_class.relname, 'name', index_class.relname,
    'definition_sha256', encode(extensions.digest(convert_to(pg_catalog.pg_get_indexdef(index_class.oid), 'UTF8'), 'sha256'), 'hex'),
    'valid', i.indisvalid, 'ready', i.indisready, 'live', i.indislive) AS item
  FROM pg_catalog.pg_index AS i
  JOIN pg_catalog.pg_class AS index_class ON index_class.oid = i.indexrelid
  JOIN pg_catalog.pg_class AS table_class ON table_class.oid = i.indrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = table_class.relnamespace
  WHERE n.nspname = 'public'
), triggers AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', c.relname, 'name', t.tgname,
    'enabled', t.tgenabled::text, 'definition_sha256',
    encode(extensions.digest(convert_to(pg_catalog.pg_get_triggerdef(t.oid, true), 'UTF8'), 'sha256'), 'hex')) AS item
  FROM pg_catalog.pg_trigger AS t
  JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND NOT t.tgisinternal
), policies AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', c.relname, 'name', p.polname,
    'command', p.polcmd::text, 'permissive', p.polpermissive, 'roles', COALESCE((SELECT jsonb_agg(CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE r.rolname END
      ORDER BY CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE r.rolname END)
      FROM unnest(p.polroles) AS policy_role(role_oid) LEFT JOIN pg_catalog.pg_roles AS r ON r.oid = role_oid), '[]'::jsonb),
    'using_sha256', encode(extensions.digest(convert_to(COALESCE(pg_catalog.pg_get_expr(p.polqual, p.polrelid), ''), 'UTF8'), 'sha256'), 'hex'),
    'check_sha256', encode(extensions.digest(convert_to(COALESCE(pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid), ''), 'UTF8'), 'sha256'), 'hex')) AS item
  FROM pg_catalog.pg_policy AS p
  JOIN pg_catalog.pg_class AS c ON c.oid = p.polrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
), acls AS (
  SELECT jsonb_build_object('object_kind', 'relation', 'schema', n.nspname, 'object', c.relname,
    'grantee', COALESCE(grantee.rolname, 'PUBLIC'), 'grantor', grantor.rolname,
    'privilege', x.privilege_type, 'grantable', x.is_grantable) AS item
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,
    pg_catalog.acldefault(CASE WHEN c.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END, c.relowner))) AS x
  LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = x.grantee
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = x.grantor
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
  UNION ALL
  SELECT jsonb_build_object('object_kind', 'function', 'schema', n.nspname,
    'object', p.proname || '(' || pg_catalog.pg_get_function_identity_arguments(p.oid) || ')',
    'grantee', COALESCE(grantee.rolname, 'PUBLIC'), 'grantor', grantor.rolname,
    'privilege', x.privilege_type, 'grantable', x.is_grantable) AS item
  FROM pg_catalog.pg_proc AS p
  JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))) AS x
  LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = x.grantee
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = x.grantor
  WHERE n.nspname = 'public'
), column_acls AS (
  SELECT jsonb_build_object('schema', n.nspname, 'relation', c.relname, 'column', a.attname,
    'grantee', COALESCE(grantee.rolname, 'PUBLIC'), 'grantor', grantor.rolname,
    'privilege', x.privilege_type, 'grantable', x.is_grantable) AS item
  FROM pg_catalog.pg_attribute AS a
  JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) AS x
  LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = x.grantee
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = x.grantor
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
    AND a.attnum > 0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
), schema_acls AS (
  SELECT jsonb_build_object('schema', n.nspname, 'grantee', COALESCE(grantee.rolname, 'PUBLIC'),
    'grantor', grantor.rolname, 'privilege', x.privilege_type, 'grantable', x.is_grantable) AS item
  FROM pg_catalog.pg_namespace AS n
  CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) AS x
  LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = x.grantee
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = x.grantor
  WHERE n.nspname = 'public'
), default_privileges AS (
  SELECT jsonb_build_object('scope', CASE WHEN defaults.defaclnamespace = 0 THEN 'global' ELSE 'public' END,
    'owner', owner.rolname, 'object_type', defaults.defaclobjtype::text,
    'grantee', COALESCE(grantee.rolname, 'PUBLIC'), 'grantor', grantor.rolname,
    'privilege', x.privilege_type, 'grantable', x.is_grantable) AS item
  FROM pg_catalog.pg_default_acl AS defaults
  JOIN pg_catalog.pg_namespace AS public_schema ON public_schema.nspname = 'public'
  JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
  CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS x
  LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = x.grantee
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = x.grantor
  WHERE defaults.defaclnamespace IN (0, public_schema.oid)
), roles AS (
  SELECT jsonb_build_object('name', expected_roles.role_name, 'exists', role.oid IS NOT NULL,
    'login', COALESCE(role.rolcanlogin, false), 'inherit', COALESCE(role.rolinherit, false),
    'superuser', COALESCE(role.rolsuper, false), 'bypassrls', COALESCE(role.rolbypassrls, false),
    'createrole', COALESCE(role.rolcreaterole, false), 'createdb', COALESCE(role.rolcreatedb, false),
    'replication', COALESCE(role.rolreplication, false), 'member_of',
    COALESCE((SELECT jsonb_agg(parent.rolname ORDER BY parent.rolname)
      FROM pg_catalog.pg_auth_members AS member
      JOIN pg_catalog.pg_roles AS parent ON parent.oid = member.roleid
      WHERE member.member = role.oid), '[]'::jsonb)) AS item
  FROM expected_roles
  LEFT JOIN pg_catalog.pg_roles AS role ON role.rolname = expected_roles.role_name
), memberships AS (
  SELECT jsonb_build_object('member', member_role.rolname, 'role', parent.rolname, 'grantor', grantor.rolname,
    'admin_option', membership.admin_option,
    'inherit_option', COALESCE(to_jsonb(membership)->'inherit_option', 'false'::jsonb),
    'set_option', COALESCE(to_jsonb(membership)->'set_option', 'false'::jsonb)) AS item
  FROM pg_catalog.pg_auth_members AS membership
  JOIN pg_catalog.pg_roles AS member_role ON member_role.oid = membership.member
  JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
  JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = membership.grantor
  WHERE member_role.rolname IN ({','.join("'%s'" % role for role in AUTHORIZATION_ROLES)})
)
SELECT
  jsonb_build_object(
    'relations', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM relations), '[]'::jsonb),
    'columns', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM columns), '[]'::jsonb),
    'constraints', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM constraints), '[]'::jsonb),
    'functions', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM functions), '[]'::jsonb),
    'indexes', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM indexes), '[]'::jsonb),
    'triggers', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM triggers), '[]'::jsonb),
    'policies', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM policies), '[]'::jsonb),
    'acls', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM acls), '[]'::jsonb),
    'column_acls', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM column_acls), '[]'::jsonb),
    'schema_acls', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM schema_acls), '[]'::jsonb),
    'default_privileges', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM default_privileges), '[]'::jsonb),
    'roles', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM roles), '[]'::jsonb),
    'memberships', COALESCE((SELECT jsonb_agg(item ORDER BY item::text) FROM memberships), '[]'::jsonb)
  ) AS catalog,
  (SELECT jsonb_object_agg(schema_name || '.' || relation_name,
    pg_catalog.to_regclass(pg_catalog.format('%I.%I', schema_name, relation_name)) IS NOT NULL)
   FROM expected) AS relation_presence
""".strip()


def _root_relation_names(relations: list[dict[str, object]]) -> tuple[str, ...]:
    names = []
    for relation in relations:
        if relation["kind"] not in {"r", "p"}:
            continue
        name = relation["name"]
        if not isinstance(name, str) or _IDENTIFIER.fullmatch(name) is None:
            raise RuntimeError("protected root relation identity is unsafe")
        names.append(name)
    if len(names) > MAX_ROOT_RELATIONS:
        raise RuntimeError("protected root relation count exceeds bounds")
    return tuple(sorted(names))


def _roots_query(relations: tuple[str, ...]) -> str:
    catalog_query = _catalog_query()
    parts = []
    for relation in relations:
        parts.append(
            "SELECT '%s'::text AS relation, count(*)::bigint AS count, "
            "encode(extensions.digest(convert_to(count(*)::text || ':' || "
            "COALESCE(bit_xor(('x' || encode(extensions.digest(convert_to(to_jsonb(row)::text, 'UTF8'), 'sha256'), 'hex'))::bit(256))::text, "
            "repeat('0', 256)), 'UTF8'), 'sha256'), 'hex') AS root_sha256 "
            "FROM public.%s AS row CROSS JOIN root_settings" % (relation, relation)
        )
    root_rows = " UNION ALL ".join(parts) if parts else "SELECT NULL::text AS relation, 0::bigint AS count, NULL::text AS root_sha256 WHERE false"
    return f"""
WITH root_settings AS (
  SELECT set_config('row_security', 'off', true), set_config('statement_timeout', '30000', true)
), root_identity AS (
  SELECT current_user AS role, current_setting('transaction_read_only') AS transaction_read_only,
    current_setting('row_security') AS row_security FROM root_settings
), roots AS ({root_rows})
SELECT root_identity.root_identity, snapshot.catalog, snapshot.relation_presence,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('relation', relation, 'count', count, 'root_sha256', root_sha256)
    ORDER BY relation) FROM roots), '[]'::jsonb) AS protected_roots
FROM (SELECT jsonb_build_object('role', role, 'transaction_read_only', transaction_read_only,
  'row_security', row_security) AS root_identity FROM root_identity) AS root_identity
CROSS JOIN ({catalog_query}) AS snapshot
""".strip()


def _require_string(value: object, label: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or (pattern is not None and pattern.fullmatch(value) is None):
        raise RuntimeError(f"schema {label} is malformed")
    return value


def _validate_catalog(catalog: object) -> dict[str, list[dict[str, object]]]:
    if not isinstance(catalog, Mapping) or set(catalog) != set(_CATALOG_FIELDS):
        raise RuntimeError("schema catalog is malformed")
    checked: dict[str, list[dict[str, object]]] = {}
    for category, fields in _CATALOG_FIELDS.items():
        values = catalog[category]
        max_rows = len(AUTHORIZATION_ROLES) * MAX_ROLE_MEMBERSHIPS if category == "memberships" else MAX_CATALOG_ROWS
        if not isinstance(values, list) or len(values) > max_rows:
            raise RuntimeError("schema catalog exceeds bounds")
        identities: set[tuple[object, ...]] = set()
        result: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(fields):
                raise RuntimeError("schema catalog is malformed")
            item = dict(value)
            if category not in {"roles", "memberships", "default_privileges"} and item.get("schema") != "public":
                raise RuntimeError("schema catalog contains an unexpected schema")
            for key, field in item.items():
                if key in {"position"}:
                    if not isinstance(field, int) or field < 1:
                        raise RuntimeError("schema catalog is malformed")
                elif key in {"not_null", "has_default", "security_definer", "grantable", "row_security", "force_row_security",
                             "exists", "login", "inherit", "superuser", "bypassrls", "createrole", "createdb", "replication",
                             "permissive", "valid", "ready", "live", "admin_option", "inherit_option", "set_option"}:
                    if not isinstance(field, bool):
                        raise RuntimeError("schema catalog is malformed")
                elif key in {"roles", "member_of"}:
                    if (not isinstance(field, list) or not all(isinstance(role, str) and role for role in field)
                            or field != sorted(set(field)) or len(field) > MAX_ROLE_MEMBERSHIPS):
                        raise RuntimeError("schema catalog is malformed")
                elif key.endswith("sha256"):
                    _require_string(field, "digest", pattern=_DIGEST)
                elif key in {"identity", "generated"}:
                    if not isinstance(field, str):
                        raise RuntimeError("schema catalog is malformed")
                else:
                    _require_string(field, key)
            if category == "relations" and item["kind"] not in {"r", "p", "v", "m", "f", "S", "i", "I", "c"}:
                raise RuntimeError("schema catalog is malformed")
            if category == "functions" and item["kind"] not in {"f", "a", "p", "w"}:
                raise RuntimeError("schema catalog is malformed")
            if category == "default_privileges" and (item["scope"] not in {"global", "public"} or item["object_type"] not in {"r", "S", "f", "T", "n"}):
                raise RuntimeError("schema catalog is malformed")
            if category == "memberships" and item["member"] not in AUTHORIZATION_ROLES:
                raise RuntimeError("schema catalog is malformed")
            identity = tuple(item[key] for key in _CATALOG_IDENTITIES[category])
            if identity in identities:
                raise RuntimeError("schema catalog contains duplicate identities")
            identities.add(identity)
            result.append(item)
        checked[category] = sorted(result, key=canonical_json)
    roles = checked["roles"]
    if len(roles) != len(AUTHORIZATION_ROLES) or {item["name"] for item in roles} != set(AUTHORIZATION_ROLES):
        raise RuntimeError("schema authorization roles are malformed")
    return checked


def _validate_presence(value: object, relations: list[dict[str, object]]) -> dict[str, bool]:
    expected = {f"{schema}.{name}" for schema, name in RELATION_PRESENCE}
    if not isinstance(value, Mapping) or set(value) != expected or not all(isinstance(present, bool) for present in value.values()):
        raise RuntimeError("schema relation-presence matrix is malformed")
    present_relations = {f"public.{item['name']}" for item in relations}
    for relation in (*RECOVERY_PUBLIC_RELATIONS, *POST_20260909_MARKER_RELATIONS):
        key = f"public.{relation}"
        if value[key] != (key in present_relations):
            raise RuntimeError("schema relation-presence matrix is inconsistent")
    return {key: bool(value[key]) for key in sorted(value)}


def _validate_roots(value: object, expected_relations: tuple[str, ...]) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != len(expected_relations):
        raise RuntimeError("protected root metadata is malformed")
    by_name: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"relation", "count", "root_sha256"}:
            raise RuntimeError("protected root metadata is malformed")
        relation = item.get("relation")
        count = item.get("count")
        digest = item.get("root_sha256")
        if relation not in expected_relations or not isinstance(count, int) or count < 0 or not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise RuntimeError("protected root metadata is malformed")
        if relation in by_name:
            raise RuntimeError("protected root metadata is malformed")
        by_name[relation] = {"relation": relation, "count": count, "root_sha256": digest}
    if set(by_name) != set(expected_relations):
        raise RuntimeError("protected root metadata is malformed")
    return [by_name[name] for name in sorted(by_name)]


def inspect_production_schema(request, project_ref: str, main_sha: str) -> dict[str, object]:
    """Return a deterministic, non-secret baseline receipt from fixed SQL."""
    if not isinstance(project_ref, str) or PROJECT_REF.fullmatch(project_ref) is None:
        raise RuntimeError("exact production project identity is required")
    if not isinstance(main_sha, str) or MAIN_SHA.fullmatch(main_sha) is None:
        raise RuntimeError("exact lowercase main SHA is required")

    identity = _query(request, project_ref, _identity_query())
    if (len(identity) != 1 or set(identity[0]) != {"role", "transaction_read_only", "database"}
            or identity[0].get("role") != "supabase_read_only_user"
            or identity[0].get("transaction_read_only") != "on"
            or not isinstance(identity[0].get("database"), str) or not identity[0]["database"]):
        raise RuntimeError("read-only identity is unavailable or unsafe")

    catalog_rows = _query(request, project_ref, _catalog_query())
    if len(catalog_rows) != 1 or set(catalog_rows[0]) != {"catalog", "relation_presence"}:
        raise RuntimeError("schema catalog response is malformed")
    catalog = _validate_catalog(catalog_rows[0]["catalog"])
    presence = _validate_presence(catalog_rows[0]["relation_presence"], catalog["relations"])
    root_relations = _root_relation_names(catalog["relations"])

    root_rows = _query(request, project_ref, _roots_query(root_relations))
    if len(root_rows) != 1 or set(root_rows[0]) != {"root_identity", "catalog", "relation_presence", "protected_roots"}:
        raise RuntimeError("protected root response is malformed")
    root_identity = root_rows[0]["root_identity"]
    if (not isinstance(root_identity, Mapping) or set(root_identity) != {"role", "transaction_read_only", "row_security"}
            or root_identity.get("role") != "supabase_read_only_user"
            or root_identity.get("transaction_read_only") != "on" or root_identity.get("row_security") != "off"):
        raise RuntimeError("protected root identity is unavailable or filtered")
    root_catalog = _validate_catalog(root_rows[0]["catalog"])
    root_presence = _validate_presence(root_rows[0]["relation_presence"], root_catalog["relations"])
    if canonical_json(root_catalog) != canonical_json(catalog) or canonical_json(root_presence) != canonical_json(presence):
        raise RuntimeError("protected root catalog snapshot changed during inventory")
    roots = _validate_roots(root_rows[0]["protected_roots"], root_relations)

    receipt: dict[str, object] = {
        "format": "stocks-production-schema-inventory-v1",
        "main_sha": main_sha,
        "production_binding_sha256": _sha256(("stocks-production-schema-inventory-v1\\0" + project_ref).encode()),
        "relation_presence": presence,
        "catalog": catalog,
        "root_algorithm": "sha256-row-xor-v1",
        "protected_roots": roots,
    }
    receipt["receipt_sha256"] = _sha256(canonical_json(receipt).encode())
    if len(canonical_json(receipt).encode()) > MAX_RECEIPT_BYTES:
        raise RuntimeError("schema inventory receipt exceeds bounds")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.name != "schema-inventory.json" or not output.parent.is_dir():
        raise RuntimeError("output must be a schema-inventory.json file in an existing directory")
    receipt = inspect_production_schema(SupabaseManagementApi(os.environ.get("SUPABASE_ACCESS_TOKEN", "")),
                                        args.production_project_ref, args.main_sha)
    output.write_text(canonical_json(receipt) + "\n")
    output.chmod(0o600)
    print(canonical_json({"receipt_sha256": receipt["receipt_sha256"], "status": "written"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
