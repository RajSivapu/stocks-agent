#!/usr/bin/env python3
"""Verify the owner dashboard login remains structurally read-only."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from psycopg.rows import tuple_row


PRIVILEGE_ROLE = "stock_agent_dashboard"
RUNTIME_ROLE = "stock_agent_dashboard_runtime"

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "holdings": {"ticker", "shares", "avg_cost", "bucket", "opened_at", "stop", "target"},
    "transactions": {"id", "ts", "ticker", "side", "qty", "price", "source", "executed_on"},
    "owner_investment_plans": {"id", "ticker", "bucket", "amount", "cadence", "next_due_on", "due_day", "active", "created_at", "updated_at"},
    "analysis_runs": {"id", "kind", "started_at", "finished_at", "status", "data_as_of", "source_status", "symbols", "write_counts", "telegram_message_ids", "summary", "gateway_request_id"},
    "market_gateway_requests": {"request_id", "operation", "run_id", "status", "attempt_count", "response", "response_digest", "created_at", "claimed_at", "finished_at"},
    "decision_evaluations": {"id", "request_id", "run_id", "candidate_id", "policy_version", "raw_action", "final_action", "policy_status", "reason_codes", "explanations", "normalized", "evidence", "analyst", "checker", "created_at"},
    "suggestions": {"id", "ts", "date", "ticker", "action", "bucket", "depth", "entry_zone_low", "entry_zone_high", "valid_until", "stop", "target", "confidence", "bull", "bear", "decisive_factor", "risk_verdict", "invalidation_level", "reason", "score", "risk_band", "price_at_suggestion", "run_id", "evidence_as_of", "invalidation_price", "evaluation_id", "decision_source", "decision_mode"},
    "suggestion_grades": {
        "id", "suggestion_id", "graded_at", "result", "price_then", "price_later",
        "horizon_days", "note", "benchmark_ticker", "stock_return_pct",
        "benchmark_return_pct", "excess_return_pct", "mfe_pct", "mae_pct",
        "entry_hit_at", "stop_hit_at", "target_hit_at", "invalidation_hit_at",
        "coverage_status", "horizon_sessions", "policy_version", "final_action",
        "direction_success",
    },
    "lessons": {"id", "entry_date", "category", "content", "created_at"},
    "daily_snapshots": {
        "id", "snap_date", "ticker", "close", "day_move_pct", "rsi14", "sma50",
        "sma200", "macd_hist",
    },
    "market_publications": {"id", "idempotency_key", "run_id", "market_date", "phase", "kind", "template_version", "rendered_body", "rendered_hash", "status", "telegram_message_ids", "attempt_count", "sending_started_at", "delivered_at", "created_at", "updated_at", "telegram_accepted_at"},
    "market_policy_config": {"version", "config", "active", "created_at", "activated_at"},
    "market_alert_drafts": {"id", "request_id", "source_evaluation_id", "rule_snapshot", "fingerprint", "state", "publication_id", "expires_at", "created_at", "updated_at"},
    "market_alert_rules": {"id", "source_draft_id", "current_version", "state", "ticker", "profile", "severity", "session", "confirmation", "conditions", "cooldown_seconds", "fire_limit", "trigger_count", "valid_until", "snoozed_until", "owner_note", "armed_at", "last_triggered_at", "updated_at"},
    "market_alert_rule_versions": {"rule_id", "version", "snapshot", "created_at"},
    "market_alert_events": {"id", "request_id", "rule_id", "rule_version", "fingerprint", "status", "reason_codes", "observed_at", "evaluated_at", "persisted_at", "market_session", "condition_results", "evidence_ids", "publication_id"},
    "market_alert_actions": {"id", "draft_id", "rule_id", "event_id", "publication_id", "telegram_update_id", "action", "prior_state", "new_state", "expected_version", "resulting_version", "snoozed_until", "received_at"},
    "market_intelligence_runs": {"id", "phase", "market_date", "policy_version", "created_at"},
    "market_intelligence_run_events": {"run_id", "status"},
    "market_source_receipts": {"run_id", "provider", "status", "retrieved_at", "accepted_count", "dropped_count"},
    "market_source_items": {"id", "canonical_url", "title"},
    "market_events": {"id", "run_id", "event_type", "title", "summary", "occurred_at", "effective_at", "materiality", "confidence", "evidence_item_ids"},
    "market_event_relationships": {"id", "run_id", "source_key", "target_kind", "target_key", "relationship_type", "evidence_item_ids"},
    "market_candidate_rankings": {"id", "run_id", "event_id", "candidate_key", "ticker", "rank", "total_score", "qualified", "veto_reasons", "exposure_item_ids"},
    "market_reports": {"id", "run_id", "market_date", "kind", "report", "report_hash", "created_at"},
    "market_report_publications": {"report_id", "idempotency_key", "status", "telegram_message_ids", "telegram_accepted_at", "suppression_reason"},
    "market_reference_manifests": {"id", "reference_version", "revision", "capability_version", "taxonomy_version", "source_hash", "valid_from", "valid_to", "manifest", "content_hash", "created_at"},
    "market_security_reference_revisions": {"id", "security_id", "entity_id", "ticker", "exchange", "instrument_type", "eligible", "exclusion_reasons", "aliases", "valid_from", "valid_to", "content_hash", "created_at"},
    "market_discovery_stage_tasks": {"id", "stage", "capability_id", "provider", "query_kind", "query_hash", "state", "attempt_count", "request_budget", "created_at", "updated_at"},
    "market_exposure_facts": {"id", "security_revision_id", "theme_episode_revision_id", "exposure_kind", "fact", "source_ids", "valid_from", "valid_to", "content_hash", "created_at"},
    "market_theme_episode_revisions": {"id", "theme_id", "revision", "episode", "source_ids", "valid_from", "valid_to", "content_hash", "created_at"},
    "market_research_nominations": {"id", "security_revision_id", "theme_episode_revision_id", "exposure_fact_ids", "state", "rationale", "created_at", "updated_at"},
}

EXPECTED_FUNCTIONS = {
    "read_overdue_scheduled_market_phases(timestamp with time zone)",
    "read_owner_intelligence_v2(integer)",
}

EXPECTED_RUNTIME_ATTRIBUTES = {
    "rolname": RUNTIME_ROLE,
    "rolcanlogin": True,
    "rolinherit": True,
    "rolsuper": False,
    "rolcreatedb": False,
    "rolcreaterole": False,
    "rolreplication": False,
    "rolbypassrls": False,
}

EXPECTED_PRIVILEGE_ATTRIBUTES = {
    "rolname": PRIVILEGE_ROLE,
    "rolcanlogin": False,
    "rolinherit": False,
    "rolsuper": False,
    "rolcreatedb": False,
    "rolcreaterole": False,
    "rolreplication": False,
    "rolbypassrls": False,
}

EXPECTED_PRIVILEGE_MEMBERS = [{
    "member": RUNTIME_ROLE,
    "admin_option": False,
    "inherit_option": True,
    "set_option": True,
}]


def evaluate_dashboard_privileges(snapshot: dict[str, Any]) -> dict[str, object]:
    role = snapshot.get("role") or {}
    privilege_role = snapshot.get("privilege_role_state") or {}
    if role.get("rolname") != RUNTIME_ROLE or role.get("rolcanlogin") is not True:
        raise RuntimeError("dashboard runtime role is missing or cannot login")
    if role.get("rolbypassrls") is True:
        raise RuntimeError("dashboard runtime role may bypass RLS")
    if role != EXPECTED_RUNTIME_ATTRIBUTES:
        raise RuntimeError("dashboard runtime role has unsafe role authority")
    if privilege_role != EXPECTED_PRIVILEGE_ATTRIBUTES:
        raise RuntimeError("dashboard privilege role has unsafe role authority")
    if snapshot.get("memberships") != [PRIVILEGE_ROLE]:
        raise RuntimeError("dashboard runtime membership is not exact")
    if snapshot.get("privilege_memberships"):
        raise RuntimeError("dashboard privilege role membership is not exact")
    if snapshot.get("privilege_members") != EXPECTED_PRIVILEGE_MEMBERS:
        raise RuntimeError("dashboard privilege role members are not exact")
    if snapshot.get("runtime_members"):
        raise RuntimeError("dashboard runtime role has incoming members")
    if set(snapshot.get("database_privileges", set())) != {"CONNECT", "TEMPORARY"}:
        raise RuntimeError("unexpected dashboard database privilege")
    if set(snapshot.get("schema_privileges", set())) != {"USAGE"}:
        raise RuntimeError("unexpected dashboard schema privilege")
    if snapshot.get("other_schema_privileges"):
        raise RuntimeError("unexpected dashboard schema privilege outside public")
    if snapshot.get("table_privileges"):
        raise RuntimeError("unexpected dashboard table privilege")
    if snapshot.get("sequence_privileges"):
        raise RuntimeError("unexpected dashboard sequence privilege")

    actual_columns = {
        table: set(columns)
        for table, columns in (snapshot.get("column_privileges") or {}).items()
    }
    if actual_columns != EXPECTED_COLUMNS:
        raise RuntimeError("dashboard column privileges differ from the allowlist")
    if set(snapshot.get("application_function_execute") or []) != EXPECTED_FUNCTIONS:
        raise RuntimeError("dashboard executable function allowlist differs")
    if snapshot.get("owned_objects"):
        raise RuntimeError("dashboard role has object ownership")

    policies = snapshot.get("policies") or {}
    if set(policies) != set(EXPECTED_COLUMNS):
        raise RuntimeError("dashboard policy coverage is incomplete")
    for table, policy in policies.items():
        if policy.get("cmd") != "SELECT" or policy.get("roles") != [PRIVILEGE_ROLE]:
            raise RuntimeError(f"dashboard policy is unsafe for {table}")

    return {
        "status": "verified",
        "runtime_role": RUNTIME_ROLE,
        "privilege_role": PRIVILEGE_ROLE,
        "table_count": len(EXPECTED_COLUMNS),
        "write_privileges": 0,
        "application_function_execute": 0,
        "owned_objects": 0,
    }


def _fetch_all(connection, query: str, parameters: tuple[object, ...] = ()):
    with connection.cursor(row_factory=tuple_row) as cursor:
        if parameters:
            cursor.execute(query, parameters)
        else:
            cursor.execute(query)
        return cursor.fetchall()


def collect_dashboard_privileges(connection) -> dict[str, Any]:
    role_rows = _fetch_all(
        connection,
        """SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
                  rolreplication, rolbypassrls
             FROM pg_catalog.pg_roles WHERE rolname IN (%s, %s) ORDER BY rolname""",
        (PRIVILEGE_ROLE, RUNTIME_ROLE),
    )
    names = (
        "rolname", "rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole",
        "rolreplication", "rolbypassrls",
    )
    roles = {row[0]: dict(zip(names, row, strict=True)) for row in role_rows}
    role = roles.get(RUNTIME_ROLE, {})
    privilege_role_state = roles.get(PRIVILEGE_ROLE, {})

    memberships = [
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT granted.rolname
                 FROM pg_catalog.pg_auth_members membership
                 JOIN pg_catalog.pg_roles member ON member.oid = membership.member
                 JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
                WHERE member.rolname = %s ORDER BY granted.rolname""",
            (RUNTIME_ROLE,),
        )
    ]
    privilege_memberships = [
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT granted.rolname
                 FROM pg_catalog.pg_auth_members membership
                 JOIN pg_catalog.pg_roles member ON member.oid = membership.member
                 JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
                WHERE member.rolname = %s ORDER BY granted.rolname""",
            (PRIVILEGE_ROLE,),
        )
    ]
    privilege_members = [
        {
            "member": row[0],
            "admin_option": row[1],
            "inherit_option": row[2],
            "set_option": row[3],
        }
        for row in _fetch_all(
            connection,
            """SELECT member.rolname, membership.admin_option,
                      COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true),
                      COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
                 FROM pg_catalog.pg_auth_members membership
                 JOIN pg_catalog.pg_roles member ON member.oid = membership.member
                 JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
                WHERE granted.rolname = %s
                  AND NOT member.rolsuper
                ORDER BY member.rolname""",
            (PRIVILEGE_ROLE,),
        )
    ]
    runtime_members = [
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT member.rolname
                 FROM pg_catalog.pg_auth_members membership
                 JOIN pg_catalog.pg_roles member ON member.oid = membership.member
                 JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
                WHERE granted.rolname = %s
                  AND NOT member.rolsuper
                ORDER BY member.rolname""",
            (RUNTIME_ROLE,),
        )
    ]
    database_privileges = {
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT privilege
                 FROM unnest(ARRAY['CONNECT','CREATE','TEMPORARY']) privilege
                WHERE pg_catalog.has_database_privilege(
                    %s, pg_catalog.current_database(), privilege
                )
                ORDER BY privilege""",
            (RUNTIME_ROLE,),
        )
    }
    schema_privileges: set[str] = set()
    other_schema_privileges: dict[str, set[str]] = {}
    for schema, privilege in _fetch_all(
        connection,
        """SELECT namespace.nspname, privilege
             FROM pg_catalog.pg_namespace namespace
             CROSS JOIN unnest(ARRAY['USAGE','CREATE']) privilege
            WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
              AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
              AND pg_catalog.has_schema_privilege(%s, namespace.oid, privilege)
            ORDER BY namespace.nspname, privilege""",
        (RUNTIME_ROLE,),
    ):
        if schema == "public":
            schema_privileges.add(privilege)
        else:
            other_schema_privileges.setdefault(schema, set()).add(privilege)
    table_privileges: dict[str, set[str]] = {}
    for schema, table, privilege in _fetch_all(
        connection,
            """SELECT namespace.nspname, class.relname, privilege
                 FROM pg_catalog.pg_class class
                 JOIN pg_catalog.pg_namespace namespace ON namespace.oid = class.relnamespace
                 CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) privilege
                WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
                  AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
                  AND class.relkind IN ('r','p','v','m','f')
                  AND pg_catalog.has_schema_privilege(%s, namespace.oid, 'USAGE')
                  AND pg_catalog.has_table_privilege(%s, class.oid, privilege)""",
        (RUNTIME_ROLE, RUNTIME_ROLE),
    ):
        key = table if schema == "public" else f"{schema}.{table}"
        table_privileges.setdefault(key, set()).add(privilege)
    column_privileges: dict[str, set[str]] = {}
    for schema, table, column, privilege in _fetch_all(
        connection,
            """SELECT namespace.nspname, class.relname, attribute.attname, privilege
                 FROM pg_catalog.pg_class class
                 JOIN pg_catalog.pg_namespace namespace ON namespace.oid = class.relnamespace
                 JOIN pg_catalog.pg_attribute attribute ON attribute.attrelid = class.oid
                 CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) privilege
                WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
                  AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
                  AND class.relkind IN ('r','p','v','m','f')
                  AND attribute.attnum > 0 AND NOT attribute.attisdropped
                  AND pg_catalog.has_schema_privilege(%s, namespace.oid, 'USAGE')
                  AND pg_catalog.has_column_privilege(%s, class.oid, attribute.attnum, privilege)""",
        (RUNTIME_ROLE, RUNTIME_ROLE),
    ):
        key = table if schema == "public" else f"{schema}.{table}"
        if privilege != "SELECT":
            table_privileges.setdefault(key, set()).add(privilege)
        column_privileges.setdefault(key, set()).add(column)

    sequence_privileges: dict[str, set[str]] = {}
    for schema, sequence, privilege in _fetch_all(
        connection,
        """SELECT namespace.nspname, class.relname, privilege
             FROM pg_catalog.pg_class class
             JOIN pg_catalog.pg_namespace namespace ON namespace.oid = class.relnamespace
             CROSS JOIN unnest(ARRAY['USAGE','SELECT','UPDATE']) privilege
            WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
              AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
              AND class.relkind = 'S'
              AND pg_catalog.has_schema_privilege(%s, namespace.oid, 'USAGE')
              AND pg_catalog.has_sequence_privilege(%s, class.oid, privilege)""",
        (RUNTIME_ROLE, RUNTIME_ROLE),
    ):
        key = sequence if schema == "public" else f"{schema}.{sequence}"
        sequence_privileges.setdefault(key, set()).add(privilege)

    application_function_execute = _fetch_all(
            connection,
            """SELECT namespace.nspname, procedure.oid::regprocedure::text
                 FROM pg_catalog.pg_proc procedure
                 JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
                  AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
                  AND pg_catalog.has_schema_privilege(%s, namespace.oid, 'USAGE')
                  AND pg_catalog.has_function_privilege(%s, procedure.oid, 'EXECUTE')
                ORDER BY procedure.oid::regprocedure::text""",
            (RUNTIME_ROLE, RUNTIME_ROLE),
        )
    application_function_execute = [
        procedure if schema == "public" else f"{schema}.{procedure}"
        for schema, procedure in application_function_execute
    ]
    owned_objects = [
        f"{row[0]}.{row[1]}"
        for row in _fetch_all(
            connection,
            """SELECT namespace.nspname, class.relname
                 FROM pg_catalog.pg_class class
                 JOIN pg_catalog.pg_namespace namespace ON namespace.oid = class.relnamespace
                 JOIN pg_catalog.pg_roles owner ON owner.oid = class.relowner
                WHERE owner.rolname IN (%s, %s)
                ORDER BY namespace.nspname, class.relname""",
            (PRIVILEGE_ROLE, RUNTIME_ROLE),
        )
    ]
    owned_objects.extend(
        f"database:{row[0]}"
        for row in _fetch_all(
            connection,
            """SELECT database.datname
                 FROM pg_catalog.pg_database database
                 JOIN pg_catalog.pg_roles owner ON owner.oid = database.datdba
                WHERE owner.rolname IN (%s, %s)
                ORDER BY database.datname""",
            (PRIVILEGE_ROLE, RUNTIME_ROLE),
        )
    )
    owned_objects.extend(
        f"schema:{row[0]}"
        for row in _fetch_all(
            connection,
            """SELECT namespace.nspname
                 FROM pg_catalog.pg_namespace namespace
                 JOIN pg_catalog.pg_roles owner ON owner.oid = namespace.nspowner
                WHERE owner.rolname IN (%s, %s)
                ORDER BY namespace.nspname""",
            (PRIVILEGE_ROLE, RUNTIME_ROLE),
        )
    )
    owned_objects.extend(
        f"{row[0]}.{row[1]}"
        for row in _fetch_all(
            connection,
            """SELECT namespace.nspname, procedure.oid::regprocedure::text
                 FROM pg_catalog.pg_proc procedure
                 JOIN pg_catalog.pg_namespace namespace ON namespace.oid = procedure.pronamespace
                 JOIN pg_catalog.pg_roles owner ON owner.oid = procedure.proowner
                WHERE owner.rolname IN (%s, %s)
                ORDER BY namespace.nspname, procedure.oid::regprocedure::text""",
            (PRIVILEGE_ROLE, RUNTIME_ROLE),
        )
    )
    policies: dict[str, dict[str, object]] = {}
    for table, command, roles in _fetch_all(
        connection,
        """SELECT tablename, cmd, roles FROM pg_catalog.pg_policies
            WHERE schemaname = 'public' AND policyname LIKE 'owner_dashboard_select_%'""",
    ):
        policies[table] = {"cmd": command, "roles": list(roles)}

    return {
        "role": role,
        "privilege_role_state": privilege_role_state,
        "memberships": memberships,
        "privilege_memberships": privilege_memberships,
        "privilege_members": privilege_members,
        "runtime_members": runtime_members,
        "database_privileges": database_privileges,
        "schema_privileges": schema_privileges,
        "other_schema_privileges": other_schema_privileges,
        "table_privileges": table_privileges,
        "sequence_privileges": sequence_privileges,
        "column_privileges": column_privileges,
        "application_function_execute": application_function_execute,
        "owned_objects": owned_objects,
        "policies": policies,
    }


def verify_dashboard_role(connection) -> dict[str, object]:
    return evaluate_dashboard_privileges(collect_dashboard_privileges(connection))


def main() -> int:
    admin_url = os.environ.get("POSTGRES_URL", "")
    if not admin_url:
        raise SystemExit("POSTGRES_URL is required")
    with psycopg.connect(admin_url) as connection:
        receipt = verify_dashboard_role(connection)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
