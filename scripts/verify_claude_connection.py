#!/usr/bin/env python3
"""Verify a Claude connection handshake without printing credentials or owner data."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

import psycopg


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_HOSTS = {
    "query1.finance.yahoo.com",
    "www.sec.gov",
    "finnhub.io",
}
REQUIRED_OPERATIONS = ["finish_run", "read_bounded_context", "start_run"]


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_source_receipt(receipt: Any, slot_updated_at: Any) -> bool:
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "contract_version", "challenge", "source_checks"
    }:
        return False
    if receipt.get("contract_version") != 2 or not isinstance(receipt.get("challenge"), str):
        return False
    if not HASH_RE.fullmatch(receipt["challenge"]):
        return False
    checks = receipt.get("source_checks")
    if not isinstance(checks, list) or len(checks) != len(REQUIRED_HOSTS):
        return False
    completed_at = _datetime(slot_updated_at)
    observed_hosts: set[str] = set()
    for check in checks:
        if not isinstance(check, Mapping) or set(check) != {
            "host", "status", "content_hash", "observed_at"
        }:
            return False
        host = check.get("host")
        content_hash = check.get("content_hash")
        observed_at = _datetime(check.get("observed_at"))
        if host not in REQUIRED_HOSTS or host in observed_hosts:
            return False
        if check.get("status") != "reachable" or not isinstance(content_hash, str):
            return False
        if not HASH_RE.fullmatch(content_hash) or observed_at is None or completed_at is None:
            return False
        if abs((completed_at - observed_at).total_seconds()) > 15 * 60:
            return False
        observed_hosts.add(str(host))
    return observed_hosts == REQUIRED_HOSTS


def evaluate_connection(record: Mapping[str, Any] | None) -> dict[str, Any]:
    row = dict(record or {})
    checks = {
        "connection_state_verified": (
            row.get("connection_status") in {"ready", "active"}
            and row.get("contract_version") == 2
            and row.get("last_handshake_at") is not None
        ),
        "application_callback_completed": (
            row.get("slot_status") == "completed" and row.get("run_status") == "completed"
        ),
        "protocol_operations_complete": sorted(row.get("operations") or []) == REQUIRED_OPERATIONS,
        "source_network_verified": _valid_source_receipt(
            row.get("receipt"), row.get("slot_updated_at")
        ),
        "zero_domain_writes": (
            row.get("publication_count") == 0
            and row.get("submission_count") == 0
            and row.get("artifact_operation_count") == 0
        ),
        "zero_notifications": row.get("telegram_message_count") == 0,
    }
    return {"ok": all(checks.values()), "checks": checks}


def read_connection(connection: psycopg.Connection, public_id: UUID) -> Mapping[str, Any] | None:
    row = connection.execute(
        """
        WITH selected_connection AS (
          SELECT owner_id, id, status, contract_version, last_handshake_at
          FROM app.agent_connections WHERE public_id = %s
        ), latest_handshake AS (
          SELECT slot.owner_id, slot.connection_id, slot.canonical_run_id,
                 slot.status, slot.updated_at, slot.handshake_receipt
          FROM app.scheduled_run_slots AS slot
          JOIN selected_connection AS connection
            ON connection.owner_id = slot.owner_id AND connection.id = slot.connection_id
          WHERE slot.purpose = 'handshake'
          ORDER BY slot.created_at DESC LIMIT 1
        )
        SELECT jsonb_build_object(
          'connection_status', connection.status,
          'contract_version', connection.contract_version,
          'last_handshake_at', connection.last_handshake_at,
          'slot_status', handshake.status,
          'slot_updated_at', handshake.updated_at,
          'run_status', run.status,
          'telegram_message_count', coalesce(jsonb_array_length(run.telegram_message_ids), 0),
          'publication_count', (SELECT count(*) FROM app.market_publications publication
            WHERE publication.owner_id = handshake.owner_id
              AND publication.run_id = handshake.canonical_run_id),
          'submission_count', (SELECT count(*) FROM app.agent_analysis_submissions submission
            WHERE submission.owner_id = handshake.owner_id
              AND submission.run_id = handshake.canonical_run_id),
          'artifact_operation_count', (SELECT count(*) FROM app.market_gateway_requests request
            WHERE request.owner_id = handshake.owner_id
              AND request.run_id = handshake.canonical_run_id
              AND request.operation = 'record_permitted_artifacts'),
          'operations', (SELECT coalesce(jsonb_agg(request.operation ORDER BY request.operation), '[]'::jsonb)
            FROM app.market_gateway_requests request
            WHERE request.owner_id = handshake.owner_id
              AND request.run_id = handshake.canonical_run_id
              AND request.status = 'completed'),
          'receipt', handshake.handshake_receipt
        )
        FROM selected_connection AS connection
        LEFT JOIN latest_handshake AS handshake
          ON handshake.owner_id = connection.owner_id AND handshake.connection_id = connection.id
        LEFT JOIN app.analysis_runs AS run
          ON run.owner_id = handshake.owner_id AND run.id = handshake.canonical_run_id
        """,
        (public_id,),
    ).fetchone()
    return row[0] if row else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-public-id", required=True, type=UUID)
    parser.add_argument("--db-url-env", default="STAGING_POSTGRES_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url = os.environ.get(args.db_url_env)
    if not database_url:
        print("connection verification failed: database environment is not configured", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url) as connection:
            result = evaluate_connection(read_connection(connection, args.connection_public_id))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    except psycopg.Error:
        print("connection verification failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
