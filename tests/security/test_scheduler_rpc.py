from __future__ import annotations

from psycopg.types.json import Jsonb

from tests.security.test_agent_gateway_rpc import request as agent_request, rpc as agent_rpc


OWNER_A = "11111111-1111-4111-8111-111111111111"


def rpc(database, name, value):
    try:
        with database.transaction():
            database.execute("SET LOCAL ROLE stock_agent_scheduler")
            return database.execute(
                f"SELECT machine.{name}(%s::jsonb)", (Jsonb(value),)
            ).fetchone()[0]
    finally:
        database.execute("RESET ROLE")


def configure_triggers(database):
    database.execute(
        """
        UPDATE app.agent_connections
        SET trigger_url = CASE owner_id
          WHEN %s THEN 'https://api.anthropic.com/v1/claude_code/routines/trig_AAAAAA/fire'
          ELSE 'https://api.anthropic.com/v1/claude_code/routines/trig_BBBBBB/fire' END
        """,
        (OWNER_A,),
    )
    database.execute(
        """
        INSERT INTO vault.secrets(id, secret) VALUES
          ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', %s),
          ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', %s)
        """,
        ("a" * 32, "b" * 32),
    )


def test_scheduler_claims_each_canonical_slot_once_and_does_not_retry_unknown(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        first = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-03T11:31:00Z", "limit": 10,
        })
        assert len(first["slots"]) == 2
        assert all(row["phase"] == "pre-market" and not row["holiday"] for row in first["slots"])
        chosen = first["slots"][0]
        secret = rpc(tenant_database, "scheduler_read_trigger_secret", {
            "attempt_id": chosen["attempt_id"],
        })
        assert secret["trigger_request_id"] == chosen["trigger_request_id"]
        assert secret["token"] in ("a" * 32, "b" * 32)
        receipt = rpc(tenant_database, "scheduler_record_trigger_result", {
            "attempt_id": chosen["attempt_id"], "status": "trigger_unknown",
            "response_status": None, "session_url": None, "response_digest": "c" * 64,
        })
        assert receipt == {"status": "trigger_unknown", "duplicate": False}
        assert rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-03T11:32:00Z", "limit": 10,
        })["slots"] == []
        assert tenant_database.execute(
            "SELECT count(*) FROM app.routine_trigger_attempts"
        ).fetchone()[0] == 2


def test_scheduler_enforces_durable_product_monthly_budget(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        tenant_database.execute(
            "UPDATE machine.routine_budget_config SET monthly_limit = 1 WHERE singleton"
        )
        result = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-03T11:31:00Z", "limit": 10,
        })
        assert len(result["slots"]) == 1
        assert tenant_database.execute(
            "SELECT invocation_count FROM machine.routine_monthly_usage WHERE usage_month = DATE '2026-09-01'"
        ).fetchone()[0] == 1
        assert tenant_database.execute(
            "SELECT count(*) FROM app.scheduled_run_slots WHERE status = 'budget_suppressed'"
        ).fetchone()[0] == 1
        assert tenant_database.execute(
            "SELECT count(*) FROM app.operational_events WHERE code = 'ROUTINE_MONTHLY_BUDGET_REACHED'"
        ).fetchone()[0] == 1


def test_holiday_claim_creates_one_publication_without_provider_attempt(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        result = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-07T11:31:00Z", "limit": 10,
        })
        assert len(result["slots"]) == 2
        assert all(row["holiday"] and row["attempt_id"] is None for row in result["slots"])
        slot = result["slots"][0]
        first = rpc(tenant_database, "scheduler_publish_holiday", {"slot_id": slot["slot_id"]})
        second = rpc(tenant_database, "scheduler_publish_holiday", {"slot_id": slot["slot_id"]})
        assert first["publication_id"] == second["publication_id"]
        assert second["duplicate"] is True
        assert tenant_database.execute(
            "SELECT count(*) FROM app.routine_trigger_attempts"
        ).fetchone()[0] == 0
        assert tenant_database.execute(
            "SELECT rendered_body FROM app.market_publications WHERE owner_id = %s",
            (str(tenant_database.execute(
                "SELECT owner_id FROM app.scheduled_run_slots WHERE id = %s", (slot["slot_id"],)
            ).fetchone()[0]),),
        ).fetchone()[0] == "🏛 Market closed today — US public holiday. No brief."


def test_early_close_and_delayed_window_are_server_derived(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        assert rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-11-27T16:31:00Z", "limit": 10,
        })["slots"][0]["phase"] == "intraday"

    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        result = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-03T12:31:00Z", "limit": 10,
        })
        assert result["slots"] == []


def test_provider_start_resolves_trigger_to_one_canonical_run(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        slot = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-03T11:31:00Z", "limit": 1,
        })["slots"][0]
        owner_id, public_id = tenant_database.execute(
            """
            SELECT slot.owner_id, connection.public_id
            FROM app.scheduled_run_slots AS slot
            JOIN app.agent_connections AS connection
              ON connection.owner_id = slot.owner_id AND connection.id = slot.connection_id
            WHERE slot.id = %s
            """,
            (slot["slot_id"],),
        ).fetchone()
        secret = "aa" * 32 if str(owner_id) == OWNER_A else "bb" * 32
        payload = {"trigger_request_id": slot["trigger_request_id"]}
        first = agent_rpc(tenant_database, "agent_start_run", agent_request(
            public_id, secret, "start_run", payload=payload,
        ))
        second = agent_rpc(tenant_database, "agent_start_run", agent_request(
            public_id, secret, "start_run", payload=payload,
        ))
        assert first["run_id"] == second["run_id"]
        assert second["canonical"] is True
        context = agent_rpc(tenant_database, "agent_read_bounded_context", agent_request(
            public_id, secret, "read_bounded_context", first["run_id"],
        ))
        assert context["phase"] == slot["phase"]
        assert context["market_date"] == slot["market_date"]
        assert context["contract_version"] == 2
        slot_status = tenant_database.execute(
            "SELECT status, canonical_run_id FROM app.scheduled_run_slots WHERE id = %s",
            (slot["slot_id"],),
        ).fetchone()
        assert slot_status[0] == "provider_started"
        assert str(slot_status[1]) == first["run_id"]
        late_fire_receipt = rpc(tenant_database, "scheduler_record_trigger_result", {
            "attempt_id": slot["attempt_id"], "status": "triggered", "response_status": 200,
            "session_url": "https://claude.ai/code/session_ABC123",
            "response_digest": "e" * 64,
        })
        assert late_fire_receipt["status"] == "provider_started"
