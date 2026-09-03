from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER = "44444444-4444-4444-8444-444444444444"
CHAT_ID = 4001


def call(connection, name: str, value: dict):
    try:
        with connection.transaction():
            connection.execute("SET LOCAL ROLE stock_agent_telegram")
            return connection.execute(
                f"SELECT machine.{name}(%s::jsonb)", (Jsonb(value),)
            ).fetchone()[0]
    finally:
        connection.execute("RESET ROLE")


@pytest.fixture(autouse=True)
def telegram_owner(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO auth.users(id, email) VALUES (%s, 'webhook@example.test') ON CONFLICT DO NOTHING",
            (OWNER,),
        )
        tenant_database.execute(
            "INSERT INTO app.profiles(id, status) VALUES (%s, 'active') ON CONFLICT DO NOTHING",
            (OWNER,),
        )
        tenant_database.execute(
            "INSERT INTO app.telegram_links(owner_id, telegram_chat_id, telegram_user_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (owner_id) DO UPDATE SET "
            "telegram_chat_id = EXCLUDED.telegram_chat_id, telegram_user_id = EXCLUDED.telegram_user_id, "
            "status = 'active', revoked_at = NULL",
            (OWNER, CHAT_ID, CHAT_ID),
        )
        yield


def prepare(
    tenant_database,
    ticker="TGSAFE",
    update_id=8001,
    confirm_digest="c" * 64,
    cancel_digest="d" * 64,
):
    return call(tenant_database, "telegram_prepare_command", {
        "chat_id": CHAT_ID,
        "user_id": CHAT_ID,
        "update_id": update_id,
        "idempotency_key": str(uuid4()),
        "command": {
            "operation": "buy",
            "ticker": ticker,
            "quantity": "2",
            "fill_price": "10.25",
            "fees": "0.50",
            "cash_total": "21.00",
            "executed_on": date.today().isoformat(),
            "bucket": "growth",
        },
        "confirm_digest": confirm_digest,
        "cancel_digest": cancel_digest,
    })


def test_callback_confirmation_is_atomic_owner_bound_and_single_use(tenant_database):
    command = prepare(tenant_database)
    assert command["claimed"] is True

    result = call(tenant_database, "telegram_apply_callback", {
        "chat_id": CHAT_ID,
        "user_id": CHAT_ID,
        "update_id": 8002,
        "action": "confirm",
        "token_digest": "c" * 64,
    })
    assert result["action"] == "confirm"
    assert result["status"] == "applied"
    assert result["result"]["ticker"] == "TGSAFE"
    assert tenant_database.execute(
        "SELECT shares, avg_cost FROM app.holdings WHERE owner_id = %s AND ticker = 'TGSAFE'",
        (OWNER,),
    ).fetchone() == (2, 10.5)
    assert tenant_database.execute(
        "SELECT consumed_at IS NOT NULL FROM app.telegram_callback_tokens WHERE token_digest = decode(%s, 'hex')",
        ("c" * 64,),
    ).fetchone()[0]
    assert tenant_database.execute(
        "SELECT invalidated_at IS NOT NULL FROM app.telegram_callback_tokens WHERE token_digest = decode(%s, 'hex')",
        ("d" * 64,),
    ).fetchone()[0]

    unavailable = call(tenant_database, "telegram_apply_callback", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8003,
        "action": "confirm", "token_digest": "c" * 64,
    })
    assert unavailable == {
        "claimed": True, "action": "confirm", "status": "unavailable",
        "reason": "callback_unavailable",
    }


def test_callback_action_and_active_link_must_match(tenant_database):
    prepare(tenant_database, "TGMATCH", update_id=8010)
    mismatch = call(tenant_database, "telegram_apply_callback", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8011,
        "action": "cancel", "token_digest": "c" * 64,
    })
    assert mismatch["status"] == "unavailable"
    with pytest.raises(Exception, match="telegram link unavailable"):
        call(tenant_database, "telegram_apply_callback", {
            "chat_id": CHAT_ID + 1, "user_id": CHAT_ID + 1, "update_id": 8012,
            "action": "confirm", "token_digest": "c" * 64,
        })


def test_stale_callback_returns_a_stable_rejection_without_false_success(tenant_database):
    stale = prepare(tenant_database, "TGSTALE", update_id=8015)
    prepare(
        tenant_database,
        "TGSTALE",
        update_id=8016,
        confirm_digest="e" * 64,
        cancel_digest="f" * 64,
    )
    assert call(tenant_database, "telegram_apply_callback", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8017,
        "action": "confirm", "token_digest": "e" * 64,
    })["status"] == "applied"

    rejected = call(tenant_database, "telegram_apply_callback", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8018,
        "action": "confirm", "token_digest": "c" * 64,
    })
    assert rejected["claimed"] is True
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "command_no_longer_applicable"
    assert "result" not in rejected
    assert tenant_database.execute(
        "SELECT status FROM app.portfolio_commands WHERE id = %s",
        (stale["command_id"],),
    ).fetchone()[0] == "error"


def test_owner_scoped_reads_are_bounded_and_delivery_receipts_are_body_free(tenant_database):
    prepare(tenant_database, "TGREAD", update_id=8020)
    holdings = call(tenant_database, "telegram_portfolio", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8021,
    })
    plans = call(tenant_database, "telegram_plans", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 8022,
    })
    assert holdings == {"claimed": True, "holdings": []}
    assert plans == {"claimed": True, "plans": []}

    claim = call(tenant_database, "telegram_claim_update", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID,
        "update_id": 9001, "kind": "message",
    })
    assert claim["claimed"] is True
    receipt = call(tenant_database, "telegram_record_delivery", {
        "chat_id": CHAT_ID, "user_id": CHAT_ID,
        "update_id": 9001, "kind": "message",
        "status": "delivery_unknown", "message_id": None,
    })
    assert receipt == {"recorded": True, "status": "delivery_unknown"}
    columns = {
        row[0] for row in tenant_database.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'telegram_deliveries'"
        ).fetchall()
    }
    assert not columns.intersection({"text", "body", "payload", "response", "token"})
    assert tenant_database.execute(
        "SELECT count(*) FROM app.telegram_deliveries WHERE owner_id = %s", (OWNER,)
    ).fetchone()[0] == 1

    pairing_receipt = call(tenant_database, "telegram_record_pairing_delivery", {
        "update_id": 9002, "pairing_status": "invalid_code",
        "status": "delivery_failed", "message_id": None,
    })
    assert pairing_receipt == {"recorded": True, "status": "delivery_failed"}
    pairing_columns = {
        row[0] for row in tenant_database.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'telegram_pairing_deliveries'"
        ).fetchall()
    }
    assert not pairing_columns.intersection({"owner_id", "chat_id", "user_id", "text", "body", "payload", "token"})


def test_prepare_rolls_back_claim_when_preview_fails(tenant_database):
    with pytest.raises(Exception):
        call(tenant_database, "telegram_prepare_command", {
            "chat_id": CHAT_ID, "user_id": CHAT_ID, "update_id": 9030,
            "idempotency_key": str(uuid4()),
            "command": {"operation": "buy", "ticker": "BAD"},
            "confirm_digest": "a" * 64, "cancel_digest": "b" * 64,
        })
    assert tenant_database.execute(
        "SELECT count(*) FROM app.telegram_updates WHERE telegram_update_id = 9030"
    ).fetchone()[0] == 0


def test_machine_boundary_rejects_non_private_legacy_link_identity(tenant_database):
    assert call(tenant_database, "telegram_resolve_link", {
        "chat_id": 1001, "user_id": 2001,
    }) == {"linked": False}
    with pytest.raises(Exception, match="invalid telegram update"):
        call(tenant_database, "telegram_claim_update", {
            "chat_id": 1001, "user_id": 2001,
            "update_id": 9100, "kind": "message",
        })
