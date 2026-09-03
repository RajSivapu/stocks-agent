from __future__ import annotations

import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"
FORBIDDEN_HEALTH_TERMS = {
    "email", "telegram_chat_id", "telegram_user_id", "ticker", "shares",
    "quantity", "cost_basis", "recommendation", "rendered_body", "prompt",
    "token", "owner_id", "user_id", "chat_id", "message_id",
}


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_public_health_is_exact_anonymous_and_contains_no_operational_data(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("SET LOCAL ROLE anon")
        value = tenant_database.execute("SELECT api.public_health()").fetchone()[0]
    assert value == {"status": "ok", "schema_version": 1}


def test_operator_health_is_admin_only_and_aggregate_only(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO app.app_admins(user_id, role) VALUES (%s, 'operator') ON CONFLICT DO NOTHING",
            (OWNER_A,),
        )
        value = tenant_database.execute(
            "SELECT app.read_operator_health(%s)", (OWNER_A,)
        ).fetchone()[0]
        with pytest.raises(Exception, match="operator unavailable"):
            tenant_database.execute("SELECT app.read_operator_health(%s)", (OWNER_B,)).fetchone()

    keys = {key.lower() for key in _walk_keys(value)}
    assert not keys.intersection(FORBIDDEN_HEALTH_TERMS)
    assert set(value) == {
        "status", "component_status", "deployed_versions", "provider_adapter",
        "missed_runs", "quota_pressure", "backup", "restore", "projection",
    }
    assert set(value["missed_runs"]) == {"last_24_hours", "last_7_days"}
    assert all(isinstance(item, int) and item >= 0 for item in value["missed_runs"].values())


def test_scheduler_retention_compacts_terminal_commands_and_never_ledger(tenant_database):
    before_transactions = tenant_database.execute("SELECT count(*) FROM app.transactions").fetchone()[0]
    with tenant_database.transaction(force_rollback=True):
        command_id = uuid4()
        tenant_database.execute(
            """
            INSERT INTO app.portfolio_commands(
              id, owner_id, channel, actor_key, idempotency_key, operation,
              normalized_input, input_digest, expected_ledger_sequence,
              before_projection, after_projection, warnings, preview_digest,
              status, expires_at, created_at, updated_at
            ) VALUES (
              %s, %s, 'web', 'owner', %s, 'buy',
              '{"ticker":"TSTOLD"}', repeat('a',64), 1,
              '{}', '{}', '[]', repeat('b',64), 'cancelled',
              now() - interval '99 days', now() - interval '100 days', now() - interval '99 days'
            )
            """,
            (command_id, OWNER_A, uuid4()),
        )
        result = tenant_database.execute(
            "SELECT machine.scheduler_apply_retention(%s::jsonb)",
            (json.dumps({"maintenance_id": str(uuid4())}),),
        ).fetchone()[0]
        assert result["commands_compacted"] == 1
        assert tenant_database.execute(
            "SELECT count(*) FROM app.portfolio_commands WHERE id = %s", (command_id,)
        ).fetchone()[0] == 0
        receipt = tenant_database.execute(
            """
            SELECT operation, terminal_status, owner_digest, command_digest
            FROM app.command_retention_receipts
            """
        ).fetchone()
        assert receipt[0:2] == ("buy", "cancelled")
        assert all(len(value) == 64 for value in receipt[2:4])
        assert tenant_database.execute("SELECT count(*) FROM app.transactions").fetchone()[0] == before_transactions


def test_retention_rejects_other_machine_roles_and_unreviewed_inputs(tenant_database):
    for role in (
        "anon", "authenticated", "service_role", "stock_agent_gateway",
        "stock_agent_telegram", "stock_agent_backup",
    ):
        with tenant_database.transaction(force_rollback=True):
            tenant_database.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(Exception, match="permission denied"):
                tenant_database.execute(
                    "SELECT machine.scheduler_apply_retention(%s::jsonb)",
                    (json.dumps({"maintenance_id": str(uuid4())}),),
                ).fetchone()
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("SET LOCAL ROLE stock_agent_scheduler")
        with pytest.raises(Exception, match="invalid retention request"):
            tenant_database.execute(
                "SELECT machine.scheduler_apply_retention(%s::jsonb)",
                (json.dumps({"maintenance_id": str(uuid4()), "owner_id": OWNER_A}),),
            ).fetchone()


def test_retention_compacts_payloads_but_preserves_citations_hashes_and_decisions(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        run_id = uuid4()
        submission_id = uuid4()
        tenant_database.execute(
            """
            INSERT INTO app.analysis_runs(owner_id, id, kind, market_date, provider, model)
            VALUES (%s, %s, 'intraday', DATE '2025-08-01', 'claude', 'test-model')
            """,
            (OWNER_A, run_id),
        )
        evidence_id = tenant_database.execute(
            """
            INSERT INTO app.run_evidence(
              owner_id, run_id, evidence_id, category, source_identifier,
              reference_identifier, retrieved_at, content_hash, claims, status, created_at
            ) VALUES (
              %s, %s, 'filing-1', 'filing', 'sec', 'accession-1',
              now() - interval '13 months', %s, %s, 'fresh', now() - interval '13 months'
            ) RETURNING id
            """,
            (OWNER_A, run_id, "c" * 64, Jsonb(["full claim to compact"])),
        ).fetchone()[0]
        tenant_database.execute(
            """
            INSERT INTO app.agent_analysis_submissions(
              id, owner_id, run_id, request_id, provider, model, phase,
              market_date, payload, payload_digest, status, created_at
            ) VALUES (
              %s, %s, %s, %s, 'claude', 'test-model', 'intraday', DATE '2025-08-01',
              %s, %s, 'accepted', now() - interval '13 months'
            )
            """,
            (submission_id, OWNER_A, run_id, uuid4(), Jsonb({"private_analysis": "large"}), "d" * 64),
        )
        result = tenant_database.execute(
            "SELECT machine.scheduler_apply_retention(%s::jsonb)",
            (json.dumps({"maintenance_id": str(uuid4())}),),
        ).fetchone()[0]
        evidence = tenant_database.execute(
            """
            SELECT claims, claims_compacted_at, source_identifier,
                   reference_identifier, content_hash
            FROM app.run_evidence WHERE id = %s
            """,
            (evidence_id,),
        ).fetchone()
        submission = tenant_database.execute(
            "SELECT payload, payload_digest, payload_compacted_at FROM app.agent_analysis_submissions WHERE id = %s",
            (submission_id,),
        ).fetchone()
        assert result["evidence_compacted"] == 1
        assert result["submissions_compacted"] == 1
        assert evidence[0] == [] and evidence[1] is not None
        assert evidence[2:] == ("sec", "accession-1", "c" * 64)
        assert submission[0] == {"compacted": True}
        assert submission[1] == "d" * 64 and submission[2] is not None
        assert tenant_database.execute(
            "SELECT count(*) FROM app.analysis_runs WHERE id = %s", (run_id,)
        ).fetchone()[0] == 1


def test_operations_sources_do_not_log_request_bodies_or_authorization_headers():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sources = [
        root / "supabase/functions/app-api/health.ts",
        root / "ops/health-monitor/src/index.ts",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "console." not in text
    assert "request.body" not in text
    assert "headers.get(\"authorization\")" not in text
