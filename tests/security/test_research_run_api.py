from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb

from tests.security.test_postgrest_isolation import OWNER_A, OWNER_B, as_user


def _dispatch(connection, owner_id: str, route: str, ip_digest: str = "a" * 64):
    connection.execute("SET LOCAL ROLE authenticated")
    connection.execute(
        "SELECT set_config('request.jwt.claim.sub', %s, true)", (owner_id,)
    )
    try:
        return connection.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s)",
            (route, uuid4(), ip_digest, Jsonb({})),
        ).fetchone()[0]
    finally:
        connection.execute("RESET ROLE")


def test_research_and_run_views_are_owner_isolated_and_omit_sensitive_columns(tenant_database):
    run_a = uuid4()
    run_b = uuid4()
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            """
            INSERT INTO app.analysis_runs(owner_id, id, kind, market_date, provider, model, summary)
            VALUES (%s, %s, 'intraday', DATE '2026-09-03', 'claude', 'configured', 'owner-a'),
                   (%s, %s, 'intraday', DATE '2026-09-03', 'claude', 'configured', 'owner-b')
            """,
            (OWNER_A, run_a, OWNER_B, run_b),
        )
        with as_user(tenant_database, OWNER_A) as connection:
            observed = connection.execute(
                "SELECT run_id, summary FROM api.run_timeline WHERE run_id IN (%s, %s)",
                (run_a, run_b),
            ).fetchall()
            assert observed == [(run_a, "owner-a")]
            assert connection.execute("SELECT * FROM api.research").fetchall() == []

    timeline_columns = {
        name for (name,) in tenant_database.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'api' AND table_name = 'run_timeline'
            """
        ).fetchall()
    }
    research_columns = {
        name for (name,) in tenant_database.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'api' AND table_name = 'research'
            """
        ).fetchall()
    }
    assert not {
        "owner_id", "trigger_request_id", "lease_token", "handshake_challenge",
        "handshake_receipt", "response_digest", "rendered_body", "rendered_parts", "error",
        "payload", "normalized_input", "inbound_token_digest", "outbound_trigger_secret_id",
    } & (timeline_columns | research_columns)


def test_run_now_creates_a_distinct_rate_limited_on_demand_slot(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        connection_id = tenant_database.execute(
            "SELECT id FROM app.agent_connections WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone()[0]
        tenant_database.execute(
            """
            UPDATE app.agent_connections
            SET trigger_url = 'https://api.anthropic.com/v1/claude_code/routines/trig_Test123/fire'
            WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, connection_id),
        )

        first = _dispatch(tenant_database, OWNER_A, "POST /runs/on-demand")
        assert first["ok"] is True
        assert first["data"]["status"] == "queued"
        slot_id = first["data"]["slot_id"]
        assert tenant_database.execute(
            """
            SELECT phase, purpose, status, canonical_run_id
            FROM app.scheduled_run_slots WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, slot_id),
        ).fetchone() == ("on-demand", "on_demand", "pending", None)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.market_publications WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone() == (0,)

        second = _dispatch(tenant_database, OWNER_A, "POST /runs/on-demand")
        assert second["ok"] is False
        assert second["error"]["code"] == "RATE_LIMITED"


def test_scheduler_claims_web_on_demand_slot_without_reusing_scheduled_slot(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        connection_id = tenant_database.execute(
            "SELECT id FROM app.agent_connections WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone()[0]
        tenant_database.execute(
            """
            UPDATE app.agent_connections
            SET trigger_url = 'https://api.anthropic.com/v1/claude_code/routines/trig_Test123/fire'
            WHERE owner_id = %s AND id = %s
            """,
            (OWNER_A, connection_id),
        )
        created = _dispatch(tenant_database, OWNER_A, "POST /runs/on-demand", "b" * 64)
        observed_now = tenant_database.execute("SELECT clock_timestamp()").fetchone()[0].isoformat()
        claimed = tenant_database.execute(
            "SELECT machine.scheduler_claim_due_slots(%s)",
            (Jsonb({"now": observed_now, "limit": 20}),),
        ).fetchone()[0]
        matching = [slot for slot in claimed["slots"] if slot["slot_id"] == created["data"]["slot_id"]]
        assert len(matching) == 1
        assert matching[0]["phase"] == "on-demand"
        assert matching[0]["holiday"] is False
        assert tenant_database.execute(
            """
            SELECT purpose FROM app.scheduled_run_slots
            WHERE id = %s AND status = 'claimed'
            """,
            (created["data"]["slot_id"],),
        ).fetchone() == ("on_demand",)
