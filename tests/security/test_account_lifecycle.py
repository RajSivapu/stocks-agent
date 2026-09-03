from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from json import loads
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from tests.security.test_postgrest_isolation import OWNER_A, OWNER_B, as_user


IP_DIGEST = "9" * 64
SESSION_A = "a" * 64
SESSION_B = "b" * 64
SESSION_C = "c" * 64
CLEANUP_TOKEN = "C" * 43
CLEANUP_DIGEST = hashlib.sha256(CLEANUP_TOKEN.encode()).hexdigest()


def dispatch(connection, owner: str, route: str, body: dict):
    if route == "POST /account/delete/confirm":
        body = {**body, "cleanup_token_digest": CLEANUP_DIGEST}
    connection.execute("SET LOCAL ROLE authenticated")
    connection.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner,))
    try:
        return connection.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s::jsonb)",
            (route, uuid4(), IP_DIGEST, Jsonb(body)),
        ).fetchone()[0]
    finally:
        connection.execute("RESET ROLE")


def accept_consent(connection, owner: str = OWNER_A):
    result = dispatch(connection, owner, "POST /consents/accept", {
        "document_version": "provider-data-v1",
    })
    assert result["ok"] is True, result
    return result["data"]


def fresh_step_up(connection, owner: str = OWNER_A, first: str = SESSION_A, second: str = SESSION_B):
    challenge = dispatch(connection, owner, "POST /account/step-up/challenge", {
        "session_digest": first,
    })
    assert challenge["ok"] is True, challenge
    completed = dispatch(connection, owner, "POST /account/step-up/complete", {
        "challenge_id": challenge["data"]["challenge_id"],
        "session_digest": second,
        "auth_method": "otp",
        "authenticated_at": datetime.now(timezone.utc).isoformat(),
    })
    assert completed["ok"] is True, completed
    return completed["data"]["step_up_receipt_id"]


def test_consent_is_explicit_current_version_and_activates_only_the_caller(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("UPDATE app.profiles SET status = 'invited' WHERE id = %s", (OWNER_A,))
        accepted = accept_consent(tenant_database)
        assert accepted["status"] == "accepted"
        assert accepted["document_version"] == "provider-data-v1"
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("active",)
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_B,)
        ).fetchone() == ("active",)

        rejected = dispatch(tenant_database, OWNER_A, "POST /consents/accept", {
            "document_version": "provider-data-v0",
        })
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "INVALID_REQUEST"


def test_step_up_requires_a_new_otp_session_and_five_minute_freshness(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        challenge = dispatch(tenant_database, OWNER_A, "POST /account/step-up/challenge", {
            "session_digest": SESSION_A,
        })
        assert challenge["ok"] is True
        challenge_id = challenge["data"]["challenge_id"]

        same_session = dispatch(tenant_database, OWNER_A, "POST /account/step-up/complete", {
            "challenge_id": challenge_id,
            "session_digest": SESSION_A,
            "auth_method": "otp",
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
        })
        assert same_session["ok"] is False

        stale = dispatch(tenant_database, OWNER_A, "POST /account/step-up/complete", {
            "challenge_id": challenge_id,
            "session_digest": SESSION_B,
            "auth_method": "otp",
            "authenticated_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        })
        assert stale["ok"] is False

        not_otp = dispatch(tenant_database, OWNER_A, "POST /account/step-up/complete", {
            "challenge_id": challenge_id,
            "session_digest": SESSION_B,
            "auth_method": "token_refresh",
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
        })
        assert not_otp["ok"] is False

        completed = dispatch(tenant_database, OWNER_A, "POST /account/step-up/complete", {
            "challenge_id": challenge_id,
            "session_digest": SESSION_B,
            "auth_method": "otp",
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
        })
        assert completed["ok"] is True, completed
        assert completed["data"]["expires_at"]

        replay = dispatch(tenant_database, OWNER_A, "POST /account/step-up/complete", {
            "challenge_id": challenge_id,
            "session_digest": SESSION_C,
            "auth_method": "otp",
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
        })
        assert replay["ok"] is False


def test_exports_are_owner_bound_secret_free_no_store_payloads(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        accept_consent(tenant_database)
        account = dispatch(tenant_database, OWNER_A, "GET /export/account.json", {})
        assert account["ok"] is True, account
        download = account["data"]
        assert download["format"] == "json"
        assert download["filename"] == "stock-agent-account.json"
        document = loads(download["body"])
        assert document["schema_version"] == 1
        assert document["profile"]["display_name"] != "Owner B"
        assert "owner_id" not in download["body"]
        assert "telegram_chat_id" not in download["body"]
        assert "telegram_user_id" not in download["body"]
        assert "inbound_token_digest" not in download["body"]
        assert "outbound_trigger_secret_id" not in download["body"]
        assert "trigger_url" not in download["body"]
        assert set(document["telegram"]) <= {"status", "linked_at", "revoked_at"}

        ledger = dispatch(tenant_database, OWNER_A, "GET /export/ledger.csv", {})
        assert ledger["ok"] is True, ledger
        assert ledger["data"]["format"] == "csv"
        assert ledger["data"]["body"].startswith(
            "transaction_id,created_at,ticker,event_type,side,quantity,price,fees,executed_on,ledger_sequence,bucket,source_channel,corrects_transaction_id\n"
        )
        assert "TSTAAA" in ledger["data"]["body"]
        assert "9.00000000" not in ledger["data"]["body"]


def test_deletion_requires_owner_step_up_and_revokes_all_active_authority(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        accept_consent(tenant_database)
        tenant_database.execute(
            """
            INSERT INTO app.telegram_updates(owner_id, telegram_update_id, kind)
            VALUES (%s, 99001, 'message')
            """,
            (OWNER_A,),
        )
        tenant_database.execute(
            """
            INSERT INTO app.telegram_deliveries(
              owner_id, telegram_update_id, kind, status, telegram_message_id
            ) VALUES (%s, 99001, 'message', 'delivered', 88001)
            """,
            (OWNER_A,),
        )
        operational_event_id = uuid4()
        tenant_database.execute(
            """
            INSERT INTO app.operational_events(id, owner_id, code, period_key, status)
            VALUES (%s, %s, 'BACKUP_STALE', '2026-09-03', 'notified')
            """,
            (operational_event_id, OWNER_A),
        )
        tenant_database.execute(
            """
            INSERT INTO app.operational_alerts(
              owner_id, event_id, code, status, telegram_message_ids, delivered_at
            ) VALUES (%s, %s, 'BACKUP_STALE', 'delivered', '[88002]'::jsonb, now())
            """,
            (OWNER_A, operational_event_id),
        )
        receipt = fresh_step_up(tenant_database)
        other_owner = dispatch(tenant_database, OWNER_B, "POST /account/delete/request", {
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
        })
        assert other_owner["ok"] is False

        request = dispatch(tenant_database, OWNER_A, "POST /account/delete/request", {
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
        })
        assert request["ok"] is True, request
        deletion_id = request["data"]["deletion_request_id"]
        assert request["data"]["confirmation_phrase"] == "DELETE MY ACCOUNT"

        wrong_phrase = dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": deletion_id,
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
            "confirmation_phrase": "delete my account",
        })
        assert wrong_phrase["ok"] is False

        confirmed = dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": deletion_id,
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
            "confirmation_phrase": "DELETE MY ACCOUNT",
        })
        assert confirmed["ok"] is True, confirmed
        assert confirmed["data"]["status"] == "pending"
        assert confirmed["data"]["older_telegram_history_requires_manual_removal"] is True
        assert confirmed["data"]["_telegram_cleanup"] == {
            "record_required": True,
            "previous_status": None,
            "attempted": 0,
            "deleted": 0,
            "failed": 0,
            "chat_id": "1001",
            "message_ids": ["88001", "88002"],
        }
        recorded = dispatch(tenant_database, OWNER_A, "POST /account/delete/cleanup-result", {
            "deletion_request_id": deletion_id,
            "cleanup_token": CLEANUP_TOKEN,
            "attempted": 2,
            "deleted": 2,
            "failed": 0,
            "status": "completed",
        })
        assert recorded["ok"] is True, recorded
        assert tenant_database.execute(
            "SELECT telegram_cleanup_status->>'status' FROM app.account_deletion_requests WHERE id = %s",
            (deletion_id,),
        ).fetchone() == ("completed",)
        duplicate = dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": deletion_id,
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
            "confirmation_phrase": "DELETE MY ACCOUNT",
        })
        assert duplicate["ok"] is True
        assert duplicate["data"]["_telegram_cleanup"] == {
            "record_required": False,
            "previous_status": "completed",
            "attempted": 2,
            "deleted": 2,
            "failed": 0,
            "chat_id": None,
            "message_ids": [],
        }

        request_row = tenant_database.execute(
            """
            SELECT requested_at, cancel_until, delete_by
            FROM app.account_deletion_requests WHERE id = %s
            """,
            (deletion_id,),
        ).fetchone()
        assert request_row[1] - request_row[0] == timedelta(hours=72)
        assert request_row[2] - request_row[0] == timedelta(days=7)
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("deletion_pending",)
        assert tenant_database.execute(
            """
            SELECT count(*) FROM app.agent_connections
            WHERE owner_id = %s AND (status <> 'revoked' OR inbound_token_digest IS NOT NULL
              OR outbound_trigger_secret_id IS NOT NULL OR trigger_url IS NOT NULL)
            """,
            (OWNER_A,),
        ).fetchone() == (0,)
        assert tenant_database.execute(
            "SELECT status FROM app.telegram_links WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == ("revoked",)
        assert tenant_database.execute(
            """
            SELECT pre_market_enabled, intraday_enabled, post_market_enabled, primary_connection_id
            FROM app.analysis_schedules WHERE owner_id = %s
            """,
            (OWNER_A,),
        ).fetchone() == (False, False, False, None)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.portfolio_commands WHERE owner_id = %s AND status IN ('submitted','previewed','confirmed')",
            (OWNER_A,),
        ).fetchone() == (0,)

        normal = dispatch(tenant_database, OWNER_A, "GET /settings", {})
        assert normal == {"ok": False, "error": {"code": "ACCOUNT_DELETION_PENDING"}}
        export = dispatch(tenant_database, OWNER_A, "GET /export/account.json", {})
        assert export["ok"] is True
        with as_user(tenant_database, OWNER_A) as connection:
            assert connection.execute("SELECT * FROM api.holdings").fetchall() == []


def test_deletion_can_be_cancelled_for_72_hours_without_restoring_credentials(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        accept_consent(tenant_database)
        receipt = fresh_step_up(tenant_database)
        request = dispatch(tenant_database, OWNER_A, "POST /account/delete/request", {
            "step_up_receipt_id": receipt, "session_digest": SESSION_B,
        })
        deletion_id = request["data"]["deletion_request_id"]
        dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": deletion_id,
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
            "confirmation_phrase": "DELETE MY ACCOUNT",
        })

        cancel_receipt = fresh_step_up(tenant_database, first=SESSION_C, second=SESSION_A)
        cancelled = dispatch(tenant_database, OWNER_A, "POST /account/delete/cancel", {
            "step_up_receipt_id": cancel_receipt,
            "session_digest": SESSION_A,
        })
        assert cancelled["ok"] is True, cancelled
        assert cancelled["data"]["status"] == "cancelled"
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("active",)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.agent_connections WHERE owner_id = %s AND status <> 'revoked'",
            (OWNER_A,),
        ).fetchone() == (0,)

        late_receipt = fresh_step_up(tenant_database, first=SESSION_B, second=SESSION_C)
        late_request = dispatch(tenant_database, OWNER_A, "POST /account/delete/request", {
            "step_up_receipt_id": late_receipt, "session_digest": SESSION_C,
        })
        late_id = late_request["data"]["deletion_request_id"]
        dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": late_id,
            "step_up_receipt_id": late_receipt,
            "session_digest": SESSION_C,
            "confirmation_phrase": "DELETE MY ACCOUNT",
        })
        tenant_database.execute(
            """
            UPDATE app.account_deletion_requests
            SET requested_at = now() - interval '73 hours',
                cancel_until = now() - interval '1 hour',
                delete_by = now() - interval '73 hours' + interval '7 days'
            WHERE id = %s
            """,
            (late_id,),
        )
        too_late = dispatch(tenant_database, OWNER_A, "POST /account/delete/cancel", {
            "step_up_receipt_id": late_receipt,
            "session_digest": SESSION_C,
        })
        assert too_late["ok"] is False


def test_operator_purges_owner_rows_after_grace_then_auth_identity_last(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        accept_consent(tenant_database)
        receipt = fresh_step_up(tenant_database)
        requested = dispatch(tenant_database, OWNER_A, "POST /account/delete/request", {
            "step_up_receipt_id": receipt, "session_digest": SESSION_B,
        })
        deletion_id = requested["data"]["deletion_request_id"]
        confirmed = dispatch(tenant_database, OWNER_A, "POST /account/delete/confirm", {
            "deletion_request_id": deletion_id,
            "step_up_receipt_id": receipt,
            "session_digest": SESSION_B,
            "confirmation_phrase": "DELETE MY ACCOUNT",
        })
        assert confirmed["ok"] is True, confirmed
        tenant_database.execute(
            """
            UPDATE app.account_deletion_requests
            SET requested_at = now() - interval '4 days',
                cancel_until = now() - interval '1 day',
                delete_by = now() + interval '3 days'
            WHERE id = %s
            """,
            (deletion_id,),
        )
        owner_b_holdings = tenant_database.execute(
            "SELECT count(*) FROM app.holdings WHERE owner_id = %s", (OWNER_B,)
        ).fetchone()[0]

        prepared = tenant_database.execute(
            "SELECT app.operator_prepare_account_deletion(%s, %s, %s)",
            (OWNER_A, deletion_id, f"DELETE AUTH {OWNER_A}"),
        ).fetchone()[0]

        assert prepared["status"] == "ready_for_auth_deletion"
        assert prepared["duplicate"] is False
        owner_tables = tenant_database.execute(
            """
            SELECT DISTINCT table_name
            FROM information_schema.columns
            WHERE table_schema = 'app' AND column_name = 'owner_id'
            ORDER BY table_name
            """
        ).fetchall()
        for (table_name,) in owner_tables:
            expected = 1 if table_name == "deletion_tombstones" else 0
            actual = tenant_database.execute(
                f'SELECT count(*) FROM app."{table_name}" WHERE owner_id = %s',
                (OWNER_A,),
            ).fetchone()[0]
            assert actual == expected, table_name
        assert tenant_database.execute(
            "SELECT count(*) FROM app.app_admins WHERE user_id = %s", (OWNER_A,)
        ).fetchone() == (0,)
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("deletion_pending",)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.holdings WHERE owner_id = %s", (OWNER_B,)
        ).fetchone() == (owner_b_holdings,)

        duplicate = tenant_database.execute(
            "SELECT app.operator_prepare_account_deletion(%s, %s, %s)",
            (OWNER_A, deletion_id, f"DELETE AUTH {OWNER_A}"),
        ).fetchone()[0]
        assert duplicate["status"] == "ready_for_auth_deletion"
        assert duplicate["duplicate"] is True

        tenant_database.execute("DELETE FROM auth.users WHERE id = %s", (OWNER_A,))
        assert tenant_database.execute(
            "SELECT count(*) FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == (0,)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.deletion_tombstones WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == (1,)
def test_account_lifecycle_receipts_have_no_email_or_financial_columns(tenant_database):
    columns = tenant_database.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'app'
          AND table_name IN ('account_step_up_challenges','account_step_up_receipts',
            'account_deletion_requests','deletion_tombstones','owner_ledger_reset_receipts')
        """
    ).fetchall()
    names = {column for _, column in columns}
    assert not names.intersection({"email", "ticker", "shares", "price", "amount", "payload", "body"})
    assert "session_digest" in names


def test_operator_reset_boundary_is_not_executable_by_product_roles(tenant_database):
    signatures = [
        "app.operator_preview_ledger_reset(uuid,uuid)",
        "app.operator_apply_ledger_reset(uuid,uuid,text,text)",
        "app.operator_prepare_account_deletion(uuid,uuid,text)",
    ]
    for signature in signatures:
        for role in ("anon", "authenticated", "service_role", "stock_agent_gateway", "stock_agent_scheduler", "stock_agent_telegram", "stock_agent_backup"):
            assert tenant_database.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, signature)
            ).fetchone() == (False,)


def test_operator_reset_consumes_step_up_preserves_analysis_and_leaves_empty_projection(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        accept_consent(tenant_database)
        receipt = fresh_step_up(tenant_database)
        suggestion_count = tenant_database.execute(
            "SELECT count(*) FROM app.suggestions WHERE owner_id = %s", (OWNER_A,)
        ).fetchone()[0]
        preview = tenant_database.execute(
            "SELECT app.operator_preview_ledger_reset(%s, %s)", (OWNER_A, receipt)
        ).fetchone()[0]
        assert preview["holdings"] >= 1
        assert preview["transactions"] >= 1
        reset = tenant_database.execute(
            "SELECT app.operator_apply_ledger_reset(%s, %s, %s, %s)",
            (OWNER_A, receipt, "d" * 64, f"RESET {OWNER_A}"),
        ).fetchone()[0]
        assert reset["status"] == "reset"
        assert reset["row_counts"] == preview
        assert tenant_database.execute(
            "SELECT count(*) FROM app.holdings WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == (0,)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.transactions WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == (0,)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.suggestions WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == (suggestion_count,)
        second = tenant_database.execute(
            "SELECT count(*) FROM app.account_step_up_receipts WHERE id = %s AND consumed_at IS NULL",
            (receipt,),
        ).fetchone()
        assert second == (0,)


def test_invitation_bootstrap_is_service_role_only_exact_and_tombstone_aware(tenant_database):
    owner_c = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO auth.users(id, email) VALUES (%s, 'c@example.com')", (owner_c,)
        )
        tenant_database.execute("SET LOCAL ROLE service_role")
        result = tenant_database.execute(
            "SELECT api.operator_initialize_invited_user(%s)", (owner_c,)
        ).fetchone()[0]
        tenant_database.execute("RESET ROLE")
        assert result == {"status": "invited"}
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (owner_c,)
        ).fetchone() == ("invited",)
        assert tenant_database.execute(
            "SELECT count(*) FROM app.notification_preferences WHERE owner_id = %s", (owner_c,)
        ).fetchone() == (1,)
        with as_user(tenant_database, OWNER_A) as connection:
            try:
                connection.execute("SELECT api.operator_initialize_invited_user(%s)", (uuid4(),))
                assert False, "authenticated role unexpectedly invoked operator invitation"
            except Exception as error:
                assert "permission denied" in str(error)
