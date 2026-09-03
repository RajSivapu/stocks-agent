from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER_C = "33333333-3333-4333-8333-333333333333"
CODE_DIGEST = "a" * 64
OTHER_CODE_DIGEST = "b" * 64
IDENTITY_DIGEST = "c" * 64


@pytest.fixture(autouse=True)
def fresh_unpaired_owner(tenant_database):
    tenant_database.execute(
        "INSERT INTO auth.users(id, email) VALUES (%s, 'pairing@example.test') ON CONFLICT DO NOTHING",
        (OWNER_C,),
    )
    tenant_database.execute(
        "INSERT INTO app.profiles(id, status) VALUES (%s, 'active') ON CONFLICT DO NOTHING",
        (OWNER_C,),
    )
    for table in (
        "telegram_callback_tokens", "portfolio_commands", "telegram_pairing_codes",
        "telegram_updates", "telegram_links",
    ):
        tenant_database.execute(f"DELETE FROM app.{table} WHERE owner_id = %s", (OWNER_C,))
    tenant_database.execute("DELETE FROM machine.telegram_pairing_attempts")
    yield
    for table in (
        "telegram_callback_tokens", "portfolio_commands", "telegram_pairing_codes",
        "telegram_updates", "telegram_links",
    ):
        tenant_database.execute(f"DELETE FROM app.{table} WHERE owner_id = %s", (OWNER_C,))
    tenant_database.execute("DELETE FROM machine.telegram_pairing_attempts")


def issue(connection, digest=CODE_DIGEST):
    return connection.execute(
        "SELECT app.issue_telegram_pairing_code(%s, %s)", (OWNER_C, digest)
    ).fetchone()[0]


def consume(connection, code_digest=CODE_DIGEST, *, chat_id=3001, confirm=False, update_id=None):
    with connection.transaction():
        connection.execute("SET LOCAL ROLE stock_agent_telegram")
        return connection.execute(
            "SELECT machine.telegram_consume_pairing(%s::jsonb)",
            (Jsonb({
                "chat_id": chat_id,
                "user_id": chat_id,
                "update_id": update_id or uuid4().int % 1_000_000_000,
                "code_digest": code_digest,
                "identity_digest": IDENTITY_DIGEST,
                "confirm_relink": confirm,
            }),),
        ).fetchone()[0]


def test_pairing_stores_only_digest_and_consumes_once_in_a_private_identity(tenant_database):
    receipt = issue(tenant_database)
    assert receipt["status"] == "issued"
    result = consume(tenant_database)
    assert result["status"] == "linked"
    assert tenant_database.execute(
        "SELECT telegram_chat_id, telegram_user_id, status FROM app.telegram_links WHERE owner_id = %s",
        (OWNER_C,),
    ).fetchone() == (3001, 3001, "active")
    assert consume(tenant_database)["status"] == "invalid_code"

    columns = {
        row[0]
        for row in tenant_database.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'app' AND table_name = 'telegram_pairing_codes'
            """
        ).fetchall()
    }
    assert "code" not in columns
    assert "code_digest" in columns


def test_authenticated_app_dispatch_issues_only_a_digest_receipt(tenant_database):
    with tenant_database.transaction():
        tenant_database.execute("SET LOCAL ROLE authenticated")
        tenant_database.execute(
            "SELECT set_config('request.jwt.claim.sub', %s, true)", (OWNER_C,)
        )
        result = tenant_database.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s::jsonb)",
            (
                "POST /telegram/pairing-code",
                uuid4(),
                "9" * 64,
                Jsonb({"code_digest": CODE_DIGEST}),
            ),
        ).fetchone()[0]
    assert result["ok"] is True
    assert result["data"]["status"] == "issued"
    assert "code" not in result["data"]


def test_expired_code_and_five_failed_identity_attempts_fail_closed(tenant_database):
    issue(tenant_database)
    tenant_database.execute(
        "UPDATE app.telegram_pairing_codes SET expires_at = created_at + interval '1 microsecond' WHERE owner_id = %s",
        (OWNER_C,),
    )
    assert consume(tenant_database)["status"] == "invalid_code"
    tenant_database.execute("DELETE FROM machine.telegram_pairing_attempts")

    valid_digest = "f" * 64
    issue(tenant_database, valid_digest)
    for _ in range(5):
        assert consume(tenant_database, OTHER_CODE_DIGEST)["status"] == "invalid_code"
    assert consume(tenant_database, valid_digest)["status"] == "rate_limited"


def test_relink_requires_confirmation_and_conflicting_owner_is_indistinguishable(tenant_database):
    tenant_database.execute(
        "INSERT INTO app.telegram_links(owner_id, telegram_chat_id, telegram_user_id) VALUES (%s, 3000, 3000)",
        (OWNER_C,),
    )
    issue(tenant_database)
    assert consume(tenant_database, chat_id=3001)["status"] == "relink_required"
    assert consume(tenant_database, chat_id=3001, confirm=True)["status"] == "linked"

    tenant_database.execute(
        "UPDATE app.telegram_pairing_codes SET consumed_at = NULL, expires_at = now() + interval '10 minutes' WHERE owner_id = %s",
        (OWNER_C,),
    )
    assert consume(tenant_database, chat_id=1002, confirm=True)["status"] == "invalid_code"


def test_unlink_invalidates_callbacks_and_cancels_pending_commands(tenant_database):
    issue(tenant_database)
    assert consume(tenant_database)["status"] == "linked"
    command = tenant_database.execute(
        "SELECT app.preview_portfolio_command(%s, 'telegram', %s, %s, %s::jsonb)",
        (
            OWNER_C,
            f"telegram:{OWNER_C}",
            uuid4(),
            Jsonb({
                "operation": "buy", "ticker": "UNLINK", "quantity": "1",
                "fill_price": "10", "fees": "0", "cash_total": "10",
                "executed_on": "2026-09-02", "bucket": "unclassified",
            }),
        ),
    ).fetchone()[0]
    tenant_database.execute(
        "SELECT app.create_telegram_callback_tokens(%s, %s, %s, %s)",
        (OWNER_C, command["command_id"], "d" * 64, "e" * 64),
    )

    callback = tenant_database.execute(
        "SELECT action, command_id FROM app.telegram_callback_tokens "
        "WHERE token_digest = decode(%s, 'hex')",
        ("d" * 64,),
    ).fetchone()
    assert callback[0] == "confirm"
    assert str(callback[1]) == command["command_id"]

    with tenant_database.transaction():
        tenant_database.execute("SET LOCAL ROLE stock_agent_telegram")
        result = tenant_database.execute(
            "SELECT machine.telegram_unlink(%s::jsonb)",
            (Jsonb({"chat_id": 3001, "user_id": 3001, "update_id": 701}),),
        ).fetchone()[0]
    assert result["status"] == "unlinked"
    assert tenant_database.execute(
        "SELECT status FROM app.portfolio_commands WHERE id = %s", (command["command_id"],)
    ).fetchone()[0] == "cancelled"
    assert tenant_database.execute(
        "SELECT count(*) FROM app.telegram_callback_tokens WHERE owner_id = %s AND invalidated_at IS NOT NULL",
        (OWNER_C,),
    ).fetchone()[0] == 2
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("SET LOCAL ROLE stock_agent_telegram")
        with pytest.raises(Exception, match="telegram link unavailable"):
            tenant_database.execute(
                "SELECT machine.telegram_apply_callback(%s::jsonb)",
                (Jsonb({
                    "chat_id": 3001,
                    "user_id": 3001,
                    "update_id": 702,
                    "action": "confirm",
                    "token_digest": "d" * 64,
                }),),
            )


def test_update_claim_is_set_membership_not_monotonic_max(tenant_database):
    issue(tenant_database)
    consume(tenant_database, update_id=500)
    with tenant_database.transaction():
        tenant_database.execute("SET LOCAL ROLE stock_agent_telegram")
        high = tenant_database.execute(
            "SELECT machine.telegram_claim_update(%s::jsonb)",
            (Jsonb({"chat_id": 3001, "user_id": 3001, "update_id": 1000, "kind": "message"}),),
        ).fetchone()[0]
        low = tenant_database.execute(
            "SELECT machine.telegram_claim_update(%s::jsonb)",
            (Jsonb({"chat_id": 3001, "user_id": 3001, "update_id": 10, "kind": "message"}),),
        ).fetchone()[0]
        duplicate = tenant_database.execute(
            "SELECT machine.telegram_claim_update(%s::jsonb)",
            (Jsonb({"chat_id": 3001, "user_id": 3001, "update_id": 10, "kind": "message"}),),
        ).fetchone()[0]
    assert high["claimed"] is True
    assert low["claimed"] is True
    assert duplicate["claimed"] is False
