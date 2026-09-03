from __future__ import annotations

from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from tests.security.test_agent_gateway_rpc import request as agent_request, rpc as agent_rpc


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


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


def test_ready_publication_is_claimed_and_delivered_exactly_once(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        slot = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-07T11:31:00Z", "limit": 1,
        })["slots"][0]
        published = rpc(tenant_database, "scheduler_publish_holiday", {
            "slot_id": slot["slot_id"],
        })
        claimed = rpc(tenant_database, "scheduler_claim_publications", {
            "now": "2026-09-07T11:32:00Z", "limit": 10,
        })["publications"]
        assert len(claimed) == 1
        assert claimed[0]["publication_id"] == published["publication_id"]
        assert claimed[0]["parts"] == ["🏛 Market closed today — US public holiday. No brief."]
        assert rpc(tenant_database, "scheduler_claim_publications", {
            "now": "2026-09-07T11:33:00Z", "limit": 10,
        })["publications"] == []
        finished = rpc(tenant_database, "scheduler_finish_publication", {
            "publication_id": claimed[0]["publication_id"],
            "lease_token": claimed[0]["lease_token"],
            "status": "delivered", "message_ids": [901],
        })
        assert finished["status"] == "delivered"
        with pytest.raises(Exception, match="publication lease unavailable"):
            rpc(tenant_database, "scheduler_finish_publication", {
                "publication_id": claimed[0]["publication_id"],
                "lease_token": claimed[0]["lease_token"],
                "status": "delivered", "message_ids": [902],
            })
        assert tenant_database.execute(
            "SELECT status, telegram_message_ids FROM app.market_publications WHERE id = %s",
            (claimed[0]["publication_id"],),
        ).fetchone() == ("delivered", [901])


def test_ready_publication_is_suppressed_when_telegram_link_is_revoked(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        configure_triggers(tenant_database)
        slot = rpc(tenant_database, "scheduler_claim_due_slots", {
            "now": "2026-09-07T11:31:00Z", "limit": 1,
        })["slots"][0]
        owner_id = tenant_database.execute(
            "SELECT owner_id FROM app.scheduled_run_slots WHERE id = %s", (slot["slot_id"],)
        ).fetchone()[0]
        publication = rpc(tenant_database, "scheduler_publish_holiday", {
            "slot_id": slot["slot_id"],
        })
        tenant_database.execute(
            "UPDATE app.telegram_links SET status = 'revoked', revoked_at = now() WHERE owner_id = %s",
            (owner_id,),
        )
        assert rpc(tenant_database, "scheduler_claim_publications", {
            "now": "2026-09-07T11:32:00Z", "limit": 10,
        })["publications"] == []
        assert tenant_database.execute(
            "SELECT status FROM app.market_publications WHERE id = %s",
            (publication["publication_id"],),
        ).fetchone()[0] == "suppressed"


def test_maintenance_expires_ephemera_flags_missed_runs_and_pauses_bad_projection(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        observed = tenant_database.execute("SELECT clock_timestamp()").fetchone()[0]
        connection_id = tenant_database.execute(
            "SELECT id FROM app.agent_connections WHERE owner_id = %s", (OWNER_A,)
        ).fetchone()[0]
        tenant_database.execute(
            """
            INSERT INTO app.portfolio_commands(
              owner_id, channel, actor_key, idempotency_key, operation,
              normalized_input, input_digest, preview_digest, status,
              created_at, expires_at
            ) VALUES
              (%s, 'web', 'owner', extensions.gen_random_uuid(), 'stop', '{}'::jsonb,
               repeat('a',64), repeat('b',64), 'previewed', %s, %s),
              (%s, 'web', 'owner', extensions.gen_random_uuid(), 'stop', '{}'::jsonb,
               repeat('c',64), repeat('d',64), 'previewed', %s, %s)
            """,
            (
                OWNER_A, observed - timedelta(hours=2), observed - timedelta(hours=1),
                OWNER_A, observed - timedelta(minutes=1), observed + timedelta(hours=1),
            ),
        )
        tenant_database.execute(
            """
            INSERT INTO app.telegram_pairing_codes(
              owner_id, code_digest, created_at, expires_at
            ) VALUES (%s, decode(repeat('ef',32),'hex'), %s, %s)
            """,
            (OWNER_A, observed - timedelta(hours=2), observed - timedelta(hours=1)),
        )
        tenant_database.execute(
            """
            INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind, received_at)
            VALUES (%s, 900001, 'message', %s)
            """,
            (OWNER_A, observed - timedelta(days=31)),
        )
        tenant_database.execute(
            """
            INSERT INTO app.scheduled_run_slots(
              owner_id, connection_id, market_date, phase, due_at, window_ends_at, status
            ) VALUES (%s, %s, %s, 'post-market', %s, %s, 'triggered')
            """,
            (
                OWNER_A, connection_id, (observed - timedelta(days=1)).date(),
                observed - timedelta(hours=2), observed - timedelta(hours=1),
            ),
        )
        tenant_database.execute(
            "UPDATE app.holdings SET shares = shares + 1 WHERE owner_id = %s AND ticker = 'TSTAAA'",
            (OWNER_A,),
        )
        connection_b = tenant_database.execute(
            "SELECT id FROM app.agent_connections WHERE owner_id = %s", (OWNER_B,)
        ).fetchone()[0]
        tenant_database.execute(
            "UPDATE app.agent_connections SET status = 'revoked' WHERE owner_id = %s",
            (OWNER_B,),
        )
        tenant_database.execute(
            """
            INSERT INTO app.analysis_runs(
              owner_id, kind, connection_id, market_date, status, finished_at
            ) VALUES (%s, 'intraday', %s, %s, 'partial', %s)
            """,
            (OWNER_B, connection_b, observed.date(), observed),
        )

        result = rpc(tenant_database, "scheduler_run_maintenance", {
            "now": observed.isoformat(),
        })
        assert result["expired_commands"] == 1
        assert result["expired_pairing_codes"] == 1
        assert result["deleted_telegram_updates"] == 1
        assert result["missed_slots"] == 1
        assert result["projection_failures"] == 1
        assert tenant_database.execute(
            "SELECT mutations_paused, reason_code FROM app.owner_operational_state WHERE owner_id = %s",
            (OWNER_A,),
        ).fetchone() == (True, "LEDGER_PROJECTION_MISMATCH")
        assert {
            row[0] for row in tenant_database.execute(
                "SELECT code FROM app.operational_events WHERE owner_id = %s", (OWNER_A,)
            ).fetchall()
        } >= {"EXPECTED_RUN_MISSED", "LEDGER_PROJECTION_MISMATCH"}
        assert {
            row[0] for row in tenant_database.execute(
                "SELECT code FROM app.operational_events WHERE owner_id = %s", (OWNER_B,)
            ).fetchall()
        } >= {"PROVIDER_DISCONNECTED", "RUN_PARTIAL"}
        alerts = rpc(tenant_database, "scheduler_claim_operational_alerts", {
            "now": observed.isoformat(), "limit": 10,
        })["alerts"]
        assert {alert["code"] for alert in alerts} >= {
            "EXPECTED_RUN_MISSED", "LEDGER_PROJECTION_MISMATCH",
            "PROVIDER_DISCONNECTED", "RUN_PARTIAL",
        }
        assert rpc(tenant_database, "scheduler_claim_operational_alerts", {
            "now": observed.isoformat(), "limit": 10,
        })["alerts"] == []
        chosen_alert = alerts[0]
        finished = rpc(tenant_database, "scheduler_finish_operational_alert", {
            "alert_id": chosen_alert["alert_id"],
            "lease_token": chosen_alert["lease_token"],
            "status": "delivered",
            "message_ids": [902],
        })
        assert finished["status"] == "delivered"
        with pytest.raises(Exception, match="operational alert lease unavailable"):
            rpc(tenant_database, "scheduler_finish_operational_alert", {
                "alert_id": chosen_alert["alert_id"],
                "lease_token": chosen_alert["lease_token"],
                "status": "delivered",
                "message_ids": [903],
            })
        active_command = tenant_database.execute(
            "SELECT id FROM app.portfolio_commands WHERE status = 'previewed'"
        ).fetchone()[0]
        with pytest.raises(Exception, match="owner mutations are paused"):
            tenant_database.execute(
                "UPDATE app.portfolio_commands SET status = 'confirmed', confirmed_at = now() WHERE id = %s",
                (active_command,),
            )


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


def test_outcome_grading_is_owner_bound_and_writes_through_scheduler_only(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        observed = tenant_database.execute("SELECT clock_timestamp()").fetchone()[0]
        connection_id = tenant_database.execute(
            "SELECT id FROM app.agent_connections WHERE owner_id = %s", (OWNER_A,)
        ).fetchone()[0]
        run_id = tenant_database.execute(
            """
            INSERT INTO app.analysis_runs(owner_id, kind, connection_id, market_date)
            VALUES (%s, 'intraday', %s, %s) RETURNING id
            """,
            (OWNER_A, connection_id, observed.date() - timedelta(days=10)),
        ).fetchone()[0]
        request_id = tenant_database.execute(
            """
            INSERT INTO app.market_gateway_requests(
              owner_id, request_id, operation, run_id, status, lease_token
            ) VALUES (%s, gen_random_uuid(), 'evaluate_and_publish', %s, 'completed', gen_random_uuid())
            RETURNING request_id
            """,
            (OWNER_A, run_id),
        ).fetchone()[0]
        tenant_database.execute(
            """
            INSERT INTO public.market_policy_config(version, config, active)
            VALUES (1, '{}'::jsonb, true) ON CONFLICT (version) DO NOTHING
            """
        )
        evaluation_id = tenant_database.execute(
            """
            INSERT INTO app.decision_evaluations(
              owner_id, id, request_id, run_id, candidate_id, policy_version,
              input_digest, raw_action, final_action, policy_status
            ) VALUES (
              %s, gen_random_uuid(), %s, %s, gen_random_uuid(), 1,
              repeat('a', 64), 'watch', 'watch', 'approved'
            ) RETURNING id
            """,
            (OWNER_A, request_id, run_id),
        ).fetchone()[0]
        suggestion_id = tenant_database.execute(
            """
            INSERT INTO app.suggestions(
              owner_id, date, ticker, action, bucket, confidence,
              price_at_suggestion, run_id, evaluation_id, decision_source,
              evidence_as_of
            ) VALUES (%s, %s, 'AAPL', 'watch', 'growth', 'medium', 100,
                      %s, %s, 'gateway', %s)
            RETURNING id
            """,
            (
                OWNER_A, observed.date() - timedelta(days=10), run_id,
                evaluation_id, observed,
            ),
        ).fetchone()[0]

        due = rpc(tenant_database, "scheduler_read_due_decisions", {
            "now": observed.isoformat(), "limit": 10,
        })["due"]
        row = next(item for item in due if item["suggestion_id"] == suggestion_id)
        assert row["owner_id"] == OWNER_A
        assert row["decision_price"] == "100"

        grade = {
            "owner_id": OWNER_A,
            "suggestion_id": suggestion_id,
            "horizon_days": 5,
            "horizon_sessions": 0,
            "coverage_status": "missing_history",
            "benchmark_ticker": "VOO",
            "stock_return_pct": None,
            "benchmark_return_pct": None,
            "excess_return_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "entry_hit_at": None,
            "stop_hit_at": None,
            "target_hit_at": None,
            "invalidation_hit_at": None,
            "policy_version": 1,
            "final_action": "watch",
            "direction_success": None,
        }
        with pytest.raises(Exception, match="outcome provenance mismatch"):
            rpc(tenant_database, "scheduler_apply_outcome_grades", {
                "grades": [{**grade, "owner_id": OWNER_B}],
            })
        result = rpc(tenant_database, "scheduler_apply_outcome_grades", {
            "grades": [grade],
        })
        assert result == {"inserted": 1, "updated": 0, "incomplete": 1}
        replay = rpc(tenant_database, "scheduler_apply_outcome_grades", {
            "grades": [grade],
        })
        assert replay == {"inserted": 0, "updated": 1, "incomplete": 1}
        due_again = rpc(tenant_database, "scheduler_read_due_decisions", {
            "now": observed.isoformat(), "limit": 10,
        })["due"]
        repeated = next(item for item in due_again if item["suggestion_id"] == suggestion_id)
        assert repeated["completed_horizons"] == [5]
        saved = tenant_database.execute(
            """
            SELECT owner_id, coverage_status FROM app.suggestion_grades
            WHERE suggestion_id = %s AND horizon_days = 5
            """,
            (suggestion_id,),
        ).fetchone()
        assert str(saved[0]) == OWNER_A
        assert saved[1] == "missing_history"


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
