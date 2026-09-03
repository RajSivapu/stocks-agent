from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from tests.security.test_agent_gateway_rpc import request as agent_request
from tests.security.test_agent_gateway_rpc import rpc as agent_rpc
from tests.security.test_scheduler_rpc import rpc as scheduler_rpc


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"
INBOUND_DIGEST = "ee" * 32
TRIGGER_TOKEN = "routine-token-with-at-least-24-chars"
TRIGGER_URL = "https://api.anthropic.com/v1/claude_code/routines/trig_HANDSHAKE/fire"


def app_call(database, name: str, owner: str, value: dict):
    with database.transaction():
        return database.execute(
            f"SELECT app.{name}(%s::uuid, %s::jsonb)",
            (owner, Jsonb(value)),
        ).fetchone()[0]


def consent(database, owner: str):
    database.execute(
        """
        INSERT INTO app.user_consents(owner_id, document_version, source)
        VALUES (%s, 'provider-data-v1', 'web')
        ON CONFLICT (owner_id, document_version) DO NOTHING
        """,
        (owner,),
    )


def create_connection(database, owner: str):
    return app_call(database, "create_agent_connection", owner, {
        "provider": "claude",
        "consent_version": "provider-data-v1",
        "inbound_token_digest": INBOUND_DIGEST,
    })


def test_connection_lifecycle_is_consent_bound_owner_scoped_and_reversible(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        with pytest.raises(Exception, match="current provider consent is required"):
            create_connection(tenant_database, OWNER_B)

        consent(tenant_database, OWNER_A)
        created = create_connection(tenant_database, OWNER_A)
        connection_id = created["connection_id"]
        public_id = created["public_id"]
        assert created["status"] == "disabled"
        assert tenant_database.execute(
            """
            SELECT encode(inbound_token_digest, 'hex'), outbound_trigger_secret_id,
                   trigger_url, status, contract_version
            FROM app.agent_connections WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, connection_id),
        ).fetchone() == (INBOUND_DIGEST, None, None, "disabled", 2)

        handshake = app_call(tenant_database, "begin_agent_connection_handshake", OWNER_A, {
            "connection_id": connection_id,
            "trigger_url": TRIGGER_URL,
            "trigger_token": TRIGGER_TOKEN,
        })
        assert handshake["status"] == "testing"
        secret_id, status = tenant_database.execute(
            """
            SELECT outbound_trigger_secret_id, status FROM app.agent_connections
            WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, connection_id),
        ).fetchone()
        assert status == "testing"
        assert tenant_database.execute(
            "SELECT secret FROM vault.secrets WHERE id = %s", (secret_id,)
        ).fetchone()[0] == TRIGGER_TOKEN

        # A handshake is infrastructure setup, not a market run. It remains claimable
        # even when no reviewed market-calendar row exists (for example, on Sunday).
        tenant_database.execute(
            """
            UPDATE app.scheduled_run_slots
            SET market_date = DATE '2026-09-06',
                due_at = TIMESTAMPTZ '2026-09-06T12:00:00Z',
                window_ends_at = TIMESTAMPTZ '2026-09-06T13:00:00Z'
            WHERE id = %s
            """,
            (handshake["handshake_id"],),
        )
        claimed = scheduler_rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-06T12:01:00Z", "limit": 1,
        })
        assert claimed["calendar_status"] == "unavailable"
        assert len(claimed["slots"]) == 1
        slot = claimed["slots"][0]
        assert slot["phase"] == "on-demand"
        assert scheduler_rpc(tenant_database, "scheduler_read_trigger_secret", {
            "attempt_id": slot["attempt_id"],
        }) == {
            "endpoint": TRIGGER_URL,
            "token": TRIGGER_TOKEN,
            "trigger_request_id": slot["trigger_request_id"],
        }
        scheduler_rpc(tenant_database, "scheduler_record_trigger_result", {
            "attempt_id": slot["attempt_id"],
            "status": "triggered",
            "response_status": 200,
            "session_url": "https://claude.ai/code/session_HANDSHAKE",
            "response_digest": "ab" * 32,
        })
        assert tenant_database.execute(
            "SELECT status FROM app.agent_connections WHERE id = %s", (connection_id,)
        ).fetchone()[0] == "testing"

        start = agent_rpc(tenant_database, "agent_start_run", agent_request(
            public_id,
            INBOUND_DIGEST,
            "start_run",
            payload={
                "trigger_request_id": slot["trigger_request_id"],
            },
        ))
        run_id = start["run_id"]
        context = agent_rpc(tenant_database, "agent_read_bounded_context", agent_request(
            public_id, INBOUND_DIGEST, "read_bounded_context", run_id,
        ))
        assert context["handshake"] is True
        assert context["phase"] == "on-demand"
        assert context["market_date"] == "2026-09-06"
        assert context["holdings"] == []
        assert context["plans"] == []
        assert len(context["challenge"]) == 64
        assert set(context["allowed_source_hosts"]) == {
            "query1.finance.yahoo.com", "www.sec.gov", "finnhub.io",
        }
        with pytest.raises(Exception, match="handshake operation is not permitted"):
            agent_rpc(tenant_database, "agent_record_permitted_artifacts", agent_request(
                public_id,
                INBOUND_DIGEST,
                "record_permitted_artifacts",
                run_id,
                payload={"mutations": [{
                    "kind": "lesson",
                    "entry_date": "2026-09-06",
                    "category": "process",
                    "content": "This must not be written by a handshake.",
                }]},
            ))
        observed_at = tenant_database.execute("SELECT clock_timestamp()").fetchone()[0].isoformat()
        source_checks = [
            {
                "host": host,
                "status": "reachable",
                "content_hash": character * 64,
                "observed_at": observed_at,
            }
            for host, character in (
                ("query1.finance.yahoo.com", "1"),
                ("www.sec.gov", "2"),
                ("finnhub.io", "3"),
            )
        ]
        with pytest.raises(Exception, match="handshake verification failed"):
            agent_rpc(tenant_database, "agent_finish_run", agent_request(
                public_id,
                INBOUND_DIGEST,
                "finish_run",
                run_id,
                payload={
                    "contract_version": 2,
                    "challenge": "0" * 64,
                    "source_checks": source_checks,
                },
            ))
        assert tenant_database.execute(
            "SELECT status FROM app.agent_connections WHERE id = %s", (connection_id,)
        ).fetchone()[0] == "testing"
        with pytest.raises(Exception, match="handshake verification failed"):
            agent_rpc(tenant_database, "agent_finish_run", agent_request(
                public_id,
                INBOUND_DIGEST,
                "finish_run",
                run_id,
                payload={
                    "contract_version": 2,
                    "challenge": context["challenge"],
                    "source_checks": [
                        {**source_checks[0], "status": "unreachable", "content_hash": None},
                        *source_checks[1:],
                    ],
                },
            ))
        assert agent_rpc(tenant_database, "agent_finish_run", agent_request(
            public_id,
            INBOUND_DIGEST,
            "finish_run",
            run_id,
            payload={
                "contract_version": 2,
                "challenge": context["challenge"],
                "source_checks": source_checks,
            },
        ))["status"] == "completed"
        assert tenant_database.execute(
            "SELECT status, last_handshake_at IS NOT NULL FROM app.agent_connections WHERE id = %s",
            (connection_id,),
        ).fetchone() == ("ready", True)
        assert tenant_database.execute(
            "SELECT handshake_receipt IS NOT NULL FROM app.scheduled_run_slots WHERE id = %s",
            (slot["slot_id"],),
        ).fetchone()[0] is True

        with pytest.raises(Exception, match="connection unavailable"):
            app_call(tenant_database, "activate_agent_connection", OWNER_B, {
                "connection_id": connection_id,
            })
        activated = app_call(tenant_database, "activate_agent_connection", OWNER_A, {
            "connection_id": connection_id,
        })
        assert activated["status"] == "active"
        assert str(tenant_database.execute(
            "SELECT primary_connection_id FROM app.analysis_schedules WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone()[0]) == connection_id

        revoked = app_call(tenant_database, "revoke_agent_connection", OWNER_A, {
            "connection_id": connection_id,
        })
        assert revoked["status"] == "revoked"
        assert tenant_database.execute(
            """
            SELECT status, inbound_token_digest, outbound_trigger_secret_id, trigger_url
            FROM app.agent_connections WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, connection_id),
        ).fetchone() == ("revoked", None, None, None)
        assert tenant_database.execute(
            "SELECT count(*) FROM vault.secrets WHERE id = %s", (secret_id,)
        ).fetchone()[0] == 0
        assert tenant_database.execute(
            "SELECT primary_connection_id FROM app.analysis_schedules WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone()[0] is None
        with pytest.raises(Exception, match="agent credential or run unavailable"):
            agent_rpc(tenant_database, "agent_start_run", agent_request(
                public_id,
                INBOUND_DIGEST,
                "start_run",
                payload={
                    "trigger_request_id": None,
                },
            ))


def test_failed_handshake_can_be_retried_without_leaking_old_vault_secret(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        consent(tenant_database, OWNER_A)
        created = create_connection(tenant_database, OWNER_A)
        first = app_call(tenant_database, "begin_agent_connection_handshake", OWNER_A, {
            "connection_id": created["connection_id"],
            "trigger_url": TRIGGER_URL,
            "trigger_token": TRIGGER_TOKEN,
        })
        first_secret = tenant_database.execute(
            "SELECT outbound_trigger_secret_id FROM app.agent_connections WHERE id = %s",
            (created["connection_id"],),
        ).fetchone()[0]
        tenant_database.execute(
            "UPDATE app.scheduled_run_slots SET status = 'trigger_failed' WHERE id = %s",
            (first["handshake_id"],),
        )
        second_token = "replacement-routine-token-12345"
        second = app_call(tenant_database, "begin_agent_connection_handshake", OWNER_A, {
            "connection_id": created["connection_id"],
            "trigger_url": TRIGGER_URL,
            "trigger_token": second_token,
        })
        assert second["duplicate"] is False
        assert second["handshake_id"] != first["handshake_id"]
        second_secret = tenant_database.execute(
            "SELECT outbound_trigger_secret_id FROM app.agent_connections WHERE id = %s",
            (created["connection_id"],),
        ).fetchone()[0]
        assert second_secret != first_secret
        assert tenant_database.execute(
            "SELECT count(*) FROM vault.secrets WHERE id = %s", (first_secret,)
        ).fetchone()[0] == 0
        assert tenant_database.execute(
            "SELECT secret FROM vault.secrets WHERE id = %s", (second_secret,)
        ).fetchone()[0] == second_token


def test_new_connection_public_ids_are_unique_and_contract_is_fixed(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        consent(tenant_database, OWNER_A)
        first = create_connection(tenant_database, OWNER_A)
        second = create_connection(tenant_database, OWNER_A)
        assert first["public_id"] != second["public_id"]
        bad_contract = agent_request(
            first["public_id"], INBOUND_DIGEST, "start_run",
            payload={"trigger_request_id": None},
        )
        bad_contract["contract_version"] = 1
        with pytest.raises(Exception, match="invalid agent request"):
            agent_rpc(tenant_database, "agent_start_run", bad_contract)
