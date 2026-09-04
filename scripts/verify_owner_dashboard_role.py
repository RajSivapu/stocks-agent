#!/usr/bin/env python3
"""Verify the owner dashboard login remains structurally read-only."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg


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
    "suggestion_grades": {"id", "suggestion_id", "graded_at", "result", "price_then", "price_later", "horizon_days", "note"},
    "market_publications": {"id", "idempotency_key", "run_id", "market_date", "phase", "kind", "template_version", "rendered_body", "rendered_hash", "status", "telegram_message_ids", "attempt_count", "sending_started_at", "delivered_at", "error", "created_at", "updated_at", "telegram_accepted_at"},
    "market_policy_config": {"version", "config", "active", "created_at", "activated_at"},
    "market_alert_drafts": {"id", "request_id", "source_evaluation_id", "rule_snapshot", "fingerprint", "state", "publication_id", "expires_at", "created_at", "updated_at"},
    "market_alert_rules": {"id", "source_draft_id", "current_version", "state", "ticker", "profile", "severity", "session", "confirmation", "conditions", "cooldown_seconds", "fire_limit", "trigger_count", "valid_until", "snoozed_until", "owner_note", "armed_at", "last_triggered_at", "updated_at"},
    "market_alert_rule_versions": {"rule_id", "version", "snapshot", "created_at"},
    "market_alert_events": {"id", "request_id", "rule_id", "rule_version", "fingerprint", "status", "reason_codes", "observed_at", "evaluated_at", "persisted_at", "market_session", "condition_results", "evidence_ids", "publication_id"},
    "market_alert_actions": {"id", "draft_id", "rule_id", "event_id", "publication_id", "telegram_update_id", "action", "prior_state", "new_state", "expected_version", "resulting_version", "snoozed_until", "received_at"},
}


def evaluate_dashboard_privileges(snapshot: dict[str, Any]) -> dict[str, object]:
    role = snapshot.get("role") or {}
    if role.get("rolname") != RUNTIME_ROLE or not role.get("rolcanlogin"):
        raise RuntimeError("dashboard runtime role is missing or cannot login")
    if role.get("rolbypassrls"):
        raise RuntimeError("dashboard runtime role may bypass RLS")
    if any(role.get(name) for name in ("rolsuper", "rolcreatedb", "rolcreaterole")):
        raise RuntimeError("dashboard runtime role has unsafe role authority")
    if snapshot.get("memberships") != [PRIVILEGE_ROLE]:
        raise RuntimeError("dashboard runtime membership is not exact")
    if set(snapshot.get("schema_privileges", set())) != {"USAGE"}:
        raise RuntimeError("unexpected dashboard schema privilege")
    if snapshot.get("table_privileges"):
        raise RuntimeError("unexpected dashboard table privilege")

    actual_columns = {
        table: set(columns)
        for table, columns in (snapshot.get("column_privileges") or {}).items()
    }
    if actual_columns != EXPECTED_COLUMNS:
        raise RuntimeError("dashboard column privileges differ from the allowlist")
    if snapshot.get("application_function_execute"):
        raise RuntimeError("dashboard role can execute an application function")
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
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        return cursor.fetchall()


def collect_dashboard_privileges(connection) -> dict[str, Any]:
    role_rows = _fetch_all(
        connection,
        """SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
             FROM pg_catalog.pg_roles WHERE rolname = %s""",
        (RUNTIME_ROLE,),
    )
    role = {}
    if role_rows:
        names = ("rolname", "rolcanlogin", "rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls")
        role = dict(zip(names, role_rows[0], strict=True))

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
    schema_privileges = {
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT privilege_type FROM information_schema.usage_privileges
                WHERE grantee = %s AND object_type = 'SCHEMA' AND object_name = 'public'""",
            (PRIVILEGE_ROLE,),
        )
    }
    table_privileges: dict[str, set[str]] = {}
    for table, privilege in _fetch_all(
        connection,
        """SELECT table_name, privilege_type FROM information_schema.table_privileges
            WHERE grantee = %s AND table_schema = 'public'""",
        (PRIVILEGE_ROLE,),
    ):
        table_privileges.setdefault(table, set()).add(privilege)
    column_privileges: dict[str, set[str]] = {}
    for table, column, privilege in _fetch_all(
        connection,
        """SELECT table_name, column_name, privilege_type
             FROM information_schema.column_privileges
            WHERE grantee = %s AND table_schema = 'public'""",
        (PRIVILEGE_ROLE,),
    ):
        if privilege != "SELECT":
            table_privileges.setdefault(table, set()).add(privilege)
        column_privileges.setdefault(table, set()).add(column)

    application_function_execute = [
        row[0]
        for row in _fetch_all(
            connection,
            """SELECT routine_name FROM information_schema.routine_privileges
                WHERE grantee IN (%s, %s) AND routine_schema = 'public'
                  AND privilege_type = 'EXECUTE' ORDER BY routine_name""",
            (PRIVILEGE_ROLE, RUNTIME_ROLE),
        )
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
    policies: dict[str, dict[str, object]] = {}
    for table, command, roles in _fetch_all(
        connection,
        """SELECT tablename, cmd, roles FROM pg_catalog.pg_policies
            WHERE schemaname = 'public' AND policyname LIKE 'owner_dashboard_select_%'""",
    ):
        policies[table] = {"cmd": command, "roles": list(roles)}

    return {
        "role": role,
        "memberships": memberships,
        "schema_privileges": schema_privileges,
        "table_privileges": table_privileges,
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
