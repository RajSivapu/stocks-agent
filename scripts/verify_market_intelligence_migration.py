#!/usr/bin/env python3
"""Rollback-only verification of the append-only market-intelligence schema."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260907_market_intelligence.sql"
GATEWAY_ROLE = "service_role"
TABLES = (
    "market_intelligence_runs",
    "market_intelligence_run_events",
    "market_source_quota_reservations",
    "market_source_receipts",
    "market_source_items",
    "market_intelligence_run_items",
    "market_events",
    "market_event_relationships",
    "market_candidate_rankings",
    "market_evidence_packets",
    "market_reports",
    "market_learning_observations",
)
RPCS = (
    "start_market_intelligence_run(uuid,text,date,integer,jsonb)",
    "record_market_intelligence(uuid,uuid,jsonb)",
    "record_market_report(uuid,uuid,jsonb)",
    "record_market_learning(uuid,jsonb)",
)
BEHAVIOR_ERRORS = {
    "new_run_not_duplicate": "new run was incorrectly marked duplicate",
    "duplicate_idempotency": "duplicate idempotency was not preserved",
    "bounded_cache_returned": "bounded cache entry was not returned",
    "over_quota_rejected": "over-quota request was not rejected",
    "mutation_rejected": "mutation was not rejected",
    "invalid_hash_rejected": "invalid hash was not rejected",
    "partial_write_rolled_back": "partial write was not rolled back",
    "oversized_json_rejected": "oversized JSON was not rejected",
    "wrong_run_reservation_rejected": "wrong-run reservation was not rejected",
    "failed_receipt_recorded": "failed receipt was not recorded",
    "report_idempotency": "report idempotency was not preserved",
    "incomplete_packet_rejected": "incomplete packet report was not rejected",
    "learning_type_rejected": "learning type was not rejected",
    "rollback_clean": "rollback left verifier rows behind",
}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def evaluate_snapshot(snapshot: dict[str, Any]) -> dict[str, object]:
    """Fail closed unless a catalog/behavior snapshot proves every invariant."""
    tables = snapshot.get("tables") or {}
    _require(set(tables) == set(TABLES), "intelligence table coverage is incomplete")
    append_only = 0
    rls = 0
    for table in TABLES:
        details = tables.get(table) or {}
        if details.get("append_only_trigger") != f"{table}_append_only":
            raise RuntimeError(f"append-only trigger is missing for {table}")
        append_only += 1
        if details.get("rls_enabled") is not True:
            raise RuntimeError(f"RLS is not enabled for {table}")
        rls += 1

    functions = snapshot.get("functions") or {}
    _require(set(functions) == set(RPCS), "intelligence RPC coverage is incomplete")
    public_execute = 0
    gateway_only = 0
    for signature in RPCS:
        details = functions.get(signature) or {}
        if details.get("search_path") != ["pg_catalog"]:
            raise RuntimeError(f"unsafe search_path for {signature}")
        if details.get("public_execute"):
            public_execute += 1
        if details.get("gateway_execute") is not True:
            raise RuntimeError(f"gateway execute is missing for {signature}")
        gateway_only += 1
    _require(public_execute == 0, "PUBLIC execute grant remains on an intelligence RPC")

    unexpected_grants = snapshot.get("unexpected_grants") or []
    if unexpected_grants:
        raise RuntimeError(f"unexpected grant: {unexpected_grants[0]}")
    brokerage_columns = snapshot.get("brokerage_columns") or []
    _require(not brokerage_columns, "brokerage column is present in intelligence schema")

    behavior = snapshot.get("behavior") or {}
    for field, message in BEHAVIOR_ERRORS.items():
        _require(behavior.get(field) is True, message)

    return {
        "status": "verified",
        "append_only_tables": append_only,
        "rls_tables": rls,
        "gateway_only_rpcs": gateway_only,
        "public_execute_grants": public_execute,
        "brokerage_columns": len(brokerage_columns),
        **behavior,
    }


def _fetch_all(cursor, query: str, parameters: tuple[object, ...] = ()):
    cursor.execute(query, parameters)
    return cursor.fetchall()


def collect_snapshot(cursor, behavior: dict[str, bool]) -> dict[str, Any]:
    table_rows = _fetch_all(
        cursor,
        """
        SELECT class.relname, class.relrowsecurity,
               EXISTS (
                 SELECT 1 FROM pg_catalog.pg_trigger trigger
                  WHERE trigger.tgrelid=class.oid AND NOT trigger.tgisinternal
                    AND trigger.tgname=class.relname || '_append_only'
               )
          FROM pg_catalog.pg_class class
          JOIN pg_catalog.pg_namespace namespace ON namespace.oid=class.relnamespace
         WHERE namespace.nspname='public' AND class.relname=ANY(%s)
         ORDER BY class.relname
        """,
        (list(TABLES),),
    )
    tables = {
        table: {
            "rls_enabled": rls_enabled,
            "append_only_trigger": f"{table}_append_only" if has_trigger else None,
        }
        for table, rls_enabled, has_trigger in table_rows
    }

    functions: dict[str, dict[str, object]] = {}
    for signature in RPCS:
        rows = _fetch_all(
            cursor,
            """
            SELECT procedure.proconfig,
                   EXISTS (
                     SELECT 1
                       FROM pg_catalog.aclexplode(COALESCE(
                         procedure.proacl,
                         pg_catalog.acldefault('f', procedure.proowner)
                       )) acl
                      WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
                   ) AS public_execute,
                   pg_catalog.has_function_privilege(%s, procedure.oid, 'EXECUTE')
              FROM pg_catalog.pg_proc procedure
             WHERE procedure.oid=pg_catalog.to_regprocedure(%s)
            """,
            (GATEWAY_ROLE, f"public.{signature}"),
        )
        if not rows:
            continue
        proconfig, public_execute, gateway_execute = rows[0]
        search_path = []
        for setting in proconfig or []:
            setting_text = setting.decode() if isinstance(setting, bytes) else str(setting)
            if setting_text.startswith("search_path="):
                search_path = setting_text.removeprefix("search_path=").split(", ")
        functions[signature] = {
            "search_path": search_path,
            "public_execute": public_execute,
            "gateway_execute": gateway_execute,
        }

    unexpected_grants = [
        f"{grantee}:{table}:{privilege}"
        for grantee, table, privilege in _fetch_all(
            cursor,
            """
            SELECT grantee, table_name, privilege_type
              FROM information_schema.table_privileges
             WHERE table_schema='public' AND table_name=ANY(%s)
               AND grantee IN ('PUBLIC','anon','authenticated','service_role')
               AND privilege_type IN (
                 'INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'
               )
             ORDER BY grantee, table_name, privilege_type
            """,
            (list(TABLES),),
        )
    ]
    brokerage_columns = [
        f"{table}.{column}"
        for table, column in _fetch_all(
            cursor,
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema='public' AND table_name=ANY(%s)
               AND lower(column_name) ~ '(broker|order_id|api_key|api_secret|credential)'
             ORDER BY table_name, column_name
            """,
            (list(TABLES),),
        )
    ]
    return {
        "tables": tables,
        "functions": functions,
        "unexpected_grants": unexpected_grants,
        "brokerage_columns": brokerage_columns,
        "behavior": behavior,
    }


def _call(cursor, name: str, *arguments: object):
    placeholders = ",".join(["%s"] * len(arguments))
    cursor.execute(f"SELECT public.{name}({placeholders})", arguments)
    return cursor.fetchone()[0]


def _expect_db_error(cursor, callback) -> bool:
    cursor.execute("SAVEPOINT expected_intelligence_error")
    try:
        callback()
    except psycopg.Error:
        cursor.execute("ROLLBACK TO SAVEPOINT expected_intelligence_error")
        cursor.execute("RELEASE SAVEPOINT expected_intelligence_error")
        return True
    cursor.execute("RELEASE SAVEPOINT expected_intelligence_error")
    raise RuntimeError("database operation unexpectedly succeeded")


def _policy_config() -> dict[str, object]:
    budgets = {
        provider: {
            "pre-market": 10,
            "intraday": 10,
            "post-market": 10,
            "on-demand": 10,
        }
        for provider in (
            "gdelt", "finnhub", "yahoo", "sec_edgar", "federal_register",
            "white_house", "doe", "dod", "eia", "fred", "bls", "bea",
        )
    }
    return {
        "intelligence": {
            "alpha_vantage_daily_ceiling": 20,
            "alpha_vantage_phase_budget": {
                "pre-market": 8,
                "intraday": 4,
                "post-market": 4,
                "on-demand": 2,
            },
            "provider_phase_budgets": budgets,
        }
    }


def _reservation(reservation_id: UUID, provider: str, requests: int, cache_keys: list[str]):
    return {
        "id": str(reservation_id),
        "provider": provider,
        "requests": requests,
        "cache_keys": cache_keys,
    }


def _completed_payload(
    *,
    reservation_id: UUID,
    receipt_id: UUID,
    item_id: UUID,
    run_item_id: UUID,
    packet_id: UUID,
    cache_key: str,
) -> dict[str, object]:
    canonical_content = "gdelt\nrollback-item\nRollback-only verifier item\nBounded normalized verifier content."
    return {
        "status": "completed",
        "coverage": {"sources_checked": ["gdelt"], "limitations": []},
        "receipts": [{
            "id": str(receipt_id),
            "reservation_id": str(reservation_id),
            "status": "succeeded",
            "cache_key": cache_key,
            "requested_window": {"hours": 24},
            "retrieved_at": "2099-09-04T12:00:00+00:00",
            "expires_at": "2099-09-05T12:00:00+00:00",
            "request_cost": 1,
            "upstream_remaining": 99,
            "returned_count": 1,
            "accepted_count": 1,
            "duplicate_count": 0,
            "dropped_count": 0,
            "error": None,
            "response_hash": "a" * 64,
        }],
        "items": [{
            "id": str(item_id),
            "run_item_id": str(run_item_id),
            "receipt_id": str(receipt_id),
            "upstream_item_id": "rollback-item",
            "canonical_url": "https://example.invalid/rollback-item",
            "published_at": "2099-09-04T11:00:00+00:00",
            "effective_at": None,
            "title": "Rollback-only verifier item",
            "normalized_text": "Bounded normalized verifier content.",
            "canonical_content": canonical_content,
            "content_hash": hashlib.sha256(canonical_content.encode("utf-8")).hexdigest(),
            "metadata": {"authority": "discovery"},
            "disposition": "accepted",
            "drop_reason": None,
        }],
        "events": [],
        "relationships": [],
        "rankings": [],
        "packet": {
            "id": str(packet_id),
            "candidate_count": 0,
            "evidence_count": 1,
            "packet": {
                "candidates": [],
                "evidence": [{"item_id": str(item_id)}],
                "coverage": {"sources_checked": ["gdelt"]},
                "limitations": [],
                "policy_version": 1,
            },
            "packet_hash": "c" * 64,
        },
        "error": None,
    }


def _failed_payload(*, reservation_id: UUID, receipt_id: UUID, cache_key: str):
    return {
        "status": "failed",
        "coverage": {"sources_checked": ["gdelt"], "limitations": ["provider failure"]},
        "receipts": [{
            "id": str(receipt_id),
            "reservation_id": str(reservation_id),
            "status": "failed",
            "cache_key": cache_key,
            "requested_window": {"hours": 24},
            "retrieved_at": "2099-09-04T12:00:00+00:00",
            "expires_at": None,
            "request_cost": 1,
            "upstream_remaining": None,
            "returned_count": 0,
            "accepted_count": 0,
            "duplicate_count": 0,
            "dropped_count": 0,
            "error": {"code": "ROLLBACK_ONLY_FAILURE"},
            "response_hash": None,
        }],
        "items": [],
        "events": [],
        "relationships": [],
        "rankings": [],
        "packet": None,
        "error": {"code": "ROLLBACK_ONLY_FAILURE"},
    }


def verify(cursor) -> tuple[dict[str, object], list[UUID]]:
    """Apply twice, exercise fail-closed behavior, and leave rollback to the caller."""
    cursor.execute(MIGRATION.read_text())
    cursor.execute(MIGRATION.read_text())
    policy_version = cursor.execute(
        "SELECT COALESCE(max(version),0)+1 FROM public.market_policy_config"
    ).fetchone()[0]
    policy = _policy_config()
    cursor.execute(
        "INSERT INTO public.market_policy_config(version,config,active) VALUES (%s,%s,false)",
        (policy_version, Jsonb(policy)),
    )

    created_runs: list[UUID] = []
    cache_key = f"gdelt:rollback:{uuid4()}"
    prior_run = uuid4()
    prior_reservation = uuid4()
    created_runs.append(prior_run)
    prior_start = _call(
        cursor,
        "start_market_intelligence_run",
        prior_run,
        "on-demand",
        date.today() - timedelta(days=1),
        policy_version,
        Jsonb({"reservations": [_reservation(prior_reservation, "gdelt", 1, [cache_key])]}),
    )
    _require(prior_start["reservation_ids"] == [str(prior_reservation)],
             "reservation identifier was not preserved")
    _require(prior_start["duplicate"] is False, "new intelligence run was marked duplicate")
    completion_id = uuid4()
    payload = _completed_payload(
        reservation_id=prior_reservation,
        receipt_id=uuid4(),
        item_id=uuid4(),
        run_item_id=uuid4(),
        packet_id=uuid4(),
        cache_key=cache_key,
    )
    payload["packet"]["packet"]["policy_version"] = policy_version
    recorded = _call(cursor, "record_market_intelligence", prior_run, completion_id, Jsonb(payload))
    replay = _call(cursor, "record_market_intelligence", prior_run, completion_id, Jsonb(payload))
    duplicate_idempotency = recorded["duplicate"] is False and replay["duplicate"] is True

    report_id = uuid4()
    report_key = uuid4()
    report_payload = {
        "id": str(report_id),
        "packet_id": payload["packet"]["id"],
        "market_date": (date.today() - timedelta(days=1)).isoformat(),
        "kind": "on-demand",
        "report": {"sections": [], "limitations": []},
        "report_hash": "d" * 64,
        "rendered_hash": "e" * 64,
    }
    report = _call(
        cursor, "record_market_report", prior_run, report_key, Jsonb(report_payload)
    )
    report_replay = _call(
        cursor, "record_market_report", prior_run, report_key, Jsonb(report_payload)
    )
    report_idempotency = (
        report["report_id"] == str(report_id)
        and report["duplicate"] is False
        and report_replay["duplicate"] is True
        and report_replay["report_hash"] == "d" * 64
        and report_replay["rendered_hash"] == "e" * 64
    )
    observation_id = uuid4()
    observation_payload = {
        "id": str(observation_id),
        "policy_version": policy_version,
        "observation_type": "outcome",
        "horizon_days": 5,
        "sample_size": 1,
        "benchmark": "SPY",
        "observation": {"status": "observation", "limitations": []},
        "content_hash": "f" * 64,
    }
    learning = _call(
        cursor, "record_market_learning", prior_run, Jsonb(observation_payload)
    )
    learning_replay = _call(
        cursor, "record_market_learning", prior_run, Jsonb(observation_payload)
    )
    _require(
        learning["duplicate"] is False and learning_replay["duplicate"] is True,
        "learning observation idempotency was not preserved",
    )
    invalid_observation = {**observation_payload, "id": str(uuid4()), "observation_type": "apply-policy"}
    learning_type_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor, "record_market_learning", prior_run, Jsonb(invalid_observation)
        ),
    )

    current_run = uuid4()
    current_reservation = uuid4()
    created_runs.append(current_run)
    started = _call(
        cursor,
        "start_market_intelligence_run",
        current_run,
        "on-demand",
        date.today(),
        policy_version,
        Jsonb({"reservations": [_reservation(current_reservation, "gdelt", 1, [cache_key])]}),
    )
    bounded_cache_returned = len(started["cache_entries"]) == 1
    _require(bounded_cache_returned, "valid requested cache entry was not returned")
    start_replay = _call(
        cursor,
        "start_market_intelligence_run",
        current_run,
        "on-demand",
        date.today(),
        policy_version,
        Jsonb({"reservations": [_reservation(current_reservation, "gdelt", 1, [cache_key])]}),
    )
    duplicate_idempotency = duplicate_idempotency and start_replay["duplicate"] is True
    incomplete_packet_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor,
            "record_market_report",
            current_run,
            uuid4(),
            Jsonb({**report_payload, "id": str(uuid4()), "packet_id": str(uuid4())}),
        ),
    )
    failed = _call(
        cursor,
        "record_market_intelligence",
        current_run,
        uuid4(),
        Jsonb(_failed_payload(
            reservation_id=current_reservation,
            receipt_id=uuid4(),
            cache_key="gdelt:rollback:failed",
        )),
    )
    failed_receipt_recorded = (
        failed["status"] == "failed" and failed["counts"]["source_receipts"] == 1
    )

    over_quota_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor,
            "start_market_intelligence_run",
            uuid4(),
            "pre-market",
            date.today(),
            policy_version,
            Jsonb({"reservations": [_reservation(uuid4(), "alpha_vantage", 21, [])]}),
        ),
    )
    mutation_rejected = _expect_db_error(
        cursor,
        lambda: cursor.execute(
            "UPDATE public.market_source_items SET title='mutated' WHERE id=%s",
            (payload["items"][0]["id"],),
        ),
    )

    invalid_run = uuid4()
    invalid_reservation = uuid4()
    created_runs.append(invalid_run)
    _call(
        cursor,
        "start_market_intelligence_run",
        invalid_run,
        "intraday",
        date.today(),
        policy_version,
        Jsonb({"reservations": [_reservation(invalid_reservation, "gdelt", 1, [])]}),
    )
    invalid_receipt_id = uuid4()
    invalid_payload = _completed_payload(
        reservation_id=invalid_reservation,
        receipt_id=invalid_receipt_id,
        item_id=uuid4(),
        run_item_id=uuid4(),
        packet_id=uuid4(),
        cache_key="gdelt:invalid-hash",
    )
    invalid_payload["packet"]["packet"]["policy_version"] = policy_version
    invalid_payload["items"][0]["content_hash"] = "0" * 64
    invalid_hash_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor, "record_market_intelligence", invalid_run, uuid4(), Jsonb(invalid_payload)
        ),
    )
    partial_write_rolled_back = cursor.execute(
        "SELECT count(*)=0 FROM public.market_source_receipts WHERE id=%s",
        (invalid_receipt_id,),
    ).fetchone()[0]
    oversized_json_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor,
            "record_market_intelligence",
            invalid_run,
            uuid4(),
            Jsonb({"oversized": "x" * 1_048_577}),
        ),
    )
    wrong_run_payload = _completed_payload(
        reservation_id=prior_reservation,
        receipt_id=uuid4(),
        item_id=uuid4(),
        run_item_id=uuid4(),
        packet_id=uuid4(),
        cache_key="gdelt:wrong-run",
    )
    wrong_run_payload["packet"]["packet"]["policy_version"] = policy_version
    wrong_run_reservation_rejected = _expect_db_error(
        cursor,
        lambda: _call(
            cursor, "record_market_intelligence", invalid_run, uuid4(), Jsonb(wrong_run_payload)
        ),
    )

    behavior = {
        "new_run_not_duplicate": prior_start["duplicate"] is False,
        "duplicate_idempotency": duplicate_idempotency,
        "bounded_cache_returned": bounded_cache_returned,
        "over_quota_rejected": over_quota_rejected,
        "mutation_rejected": mutation_rejected,
        "invalid_hash_rejected": invalid_hash_rejected,
        "partial_write_rolled_back": partial_write_rolled_back,
        "oversized_json_rejected": oversized_json_rejected,
        "wrong_run_reservation_rejected": wrong_run_reservation_rejected,
        "failed_receipt_recorded": failed_receipt_recorded,
        "report_idempotency": report_idempotency,
        "incomplete_packet_rejected": incomplete_packet_rejected,
        "learning_type_rejected": learning_type_rejected,
        "rollback_clean": False,
    }
    return collect_snapshot(cursor, behavior), created_runs


def remaining_test_rows(connection, run_ids: list[UUID]) -> int:
    with connection.cursor() as cursor:
        exists = cursor.execute(
            "SELECT pg_catalog.to_regclass('public.market_intelligence_runs') IS NOT NULL"
        ).fetchone()[0]
        if not exists:
            return 0
        return cursor.execute(
            "SELECT count(*) FROM public.market_intelligence_runs WHERE id=ANY(%s)",
            (run_ids,),
        ).fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback", action="store_true")
    arguments = parser.parse_args()
    if not arguments.rollback:
        raise SystemExit("verification is rollback-only; pass --rollback")
    postgres_url = os.environ.get("POSTGRES_URL", "")
    if not postgres_url:
        raise SystemExit("POSTGRES_URL is required")
    with psycopg.connect(postgres_url) as connection:
        with connection.cursor() as cursor:
            snapshot, run_ids = verify(cursor)
        connection.rollback()
        remaining = remaining_test_rows(connection, run_ids)
        snapshot["behavior"]["rollback_clean"] = remaining == 0
        receipt = evaluate_snapshot(snapshot)
        receipt["remaining_test_rows"] = remaining
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
