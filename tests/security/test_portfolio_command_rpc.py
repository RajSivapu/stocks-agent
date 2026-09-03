from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from scripts.verify_portfolio_command_rpc import verify_database


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def append_trade(connection, ticker, side, qty, price, *, fees="0", bucket="unclassified"):
    return connection.execute(
        "SELECT app.append_ledger_trade(%s, %s::jsonb)",
        (
            OWNER_A,
            Jsonb({
                "ticker": ticker,
                "side": side,
                "qty": qty,
                "price": price,
                "fees": fees,
                "executed_on": "2026-09-02",
                "bucket": bucket if side == "buy" else None,
                "source_channel": "operator",
            }),
        ),
    ).fetchone()[0]


@contextmanager
def as_owner(connection):
    with connection.transaction():
        connection.execute("SET LOCAL ROLE authenticated")
        connection.execute(
            "SELECT set_config('request.jwt.claim.sub', %s, true)", (OWNER_A,)
        )
        yield connection


def preview(connection, command, *, key=None):
    return connection.execute(
        "SELECT app.preview_portfolio_command(%s, 'web', %s, %s, %s::jsonb)",
        (OWNER_A, OWNER_A, key or uuid4(), Jsonb(command)),
    ).fetchone()[0]


def confirm(connection, receipt, *, digest=None):
    return connection.execute(
        "SELECT app.confirm_portfolio_command(%s, 'web', %s, %s, %s)",
        (OWNER_A, OWNER_A, receipt["command_id"], digest or receipt["preview_digest"]),
    ).fetchone()[0]


def buy_command(ticker, *, qty="2", price="10", fees="1", cash="21.00", bucket="unclassified"):
    return {
        "operation": "buy",
        "ticker": ticker,
        "quantity": qty,
        "fill_price": price,
        "fees": fees,
        "cash_total": cash,
        "executed_on": "2026-09-02",
        **({} if bucket is None else {"bucket": bucket}),
    }


def test_preview_confirm_apply_is_the_only_success_path_and_replay_is_idempotent(tenant_database):
    ticker = "CMDNEW"
    receipt = preview(tenant_database, buy_command(ticker))

    assert receipt["status"] == "previewed"
    assert receipt["before"]["shares"] == 0
    assert receipt["after"]["shares"] == 2
    assert receipt["warnings"] == ["UNCLASSIFIED_BUCKET"]
    assert tenant_database.execute(
        "SELECT expires_at <= created_at + interval '15 minutes 1 second' FROM app.portfolio_commands WHERE id = %s",
        (receipt["command_id"],),
    ).fetchone()[0]
    applied = confirm(tenant_database, receipt)
    duplicate = confirm(tenant_database, receipt)

    assert applied["status"] == "applied"
    assert duplicate == {**applied, "duplicate": True}
    assert tenant_database.execute(
        "SELECT count(*) FROM app.transactions WHERE owner_id = %s AND ticker = %s AND event_type = 'trade'",
        (OWNER_A, ticker),
    ).fetchone()[0] == 1


def test_mismatched_digest_cancellation_expiry_and_illegal_transition_fail(tenant_database):
    mismatch = preview(tenant_database, buy_command("CMDDIG"))
    with pytest.raises(Exception, match="preview digest"):
        confirm(tenant_database, mismatch, digest="b" * 64)

    cancelled = preview(tenant_database, buy_command("CMDCAN"))
    result = tenant_database.execute(
        "SELECT app.cancel_portfolio_command(%s, 'web', %s, %s, %s)",
        (OWNER_A, OWNER_A, cancelled["command_id"], cancelled["preview_digest"]),
    ).fetchone()[0]
    assert result["status"] == "cancelled"
    with pytest.raises(Exception, match="cancelled"):
        confirm(tenant_database, cancelled)

    expired = preview(tenant_database, buy_command("CMDEXP"))
    tenant_database.execute(
        "UPDATE app.portfolio_commands SET status = 'expired' WHERE id = %s",
        (expired["command_id"],),
    )
    with pytest.raises(Exception, match="expired"):
        confirm(tenant_database, expired)

    command_id = uuid4()
    tenant_database.execute(
        """
        INSERT INTO app.portfolio_commands
          (id, owner_id, channel, actor_key, idempotency_key, operation,
           normalized_input, input_digest, status, expires_at)
        VALUES (%s, %s, 'operator', 'test', %s, 'buy', '{}', %s, 'submitted', now() + interval '15 minutes')
        """,
        (command_id, OWNER_A, uuid4(), "a" * 64),
    )
    with pytest.raises(Exception, match="illegal command transition"):
        tenant_database.execute(
            "UPDATE app.portfolio_commands SET status = 'applied' WHERE id = %s",
            (command_id,),
        )
    tenant_database.rollback()


def test_stale_sequence_and_same_idempotency_with_different_input_fail(tenant_database):
    key = uuid4()
    receipt = preview(tenant_database, buy_command("CMDSTA"), key=key)
    duplicate = preview(tenant_database, buy_command("CMDSTA"), key=key)
    assert duplicate == receipt
    with pytest.raises(Exception, match="idempotency key"):
        preview(tenant_database, buy_command("CMDSTA", price="11", cash="23.00"), key=key)

    append_trade(tenant_database, "CMDSTA", "buy", "1", "10", fees="0", bucket="unclassified")
    with pytest.raises(Exception, match="stale ledger sequence"):
        confirm(tenant_database, receipt)


def test_cash_reconciliation_unknown_sell_and_explicit_first_buy_bucket(tenant_database):
    with pytest.raises(Exception, match="cash total"):
        preview(tenant_database, buy_command("CMDCASH", cash="25.00"))
    with pytest.raises(Exception, match="holding not found"):
        preview(tenant_database, {
            "operation": "sell",
            "ticker": "CMDMISS",
            "quantity": "1",
            "fill_price": "10",
            "fees": "0",
            "cash_total": "10.00",
            "executed_on": "2026-09-02",
        })
    with pytest.raises(Exception, match="bucket"):
        preview(tenant_database, buy_command("CMDBUCK", bucket=None))


def test_correction_appends_void_and_replacement_through_command(tenant_database):
    buy = confirm(tenant_database, preview(tenant_database, buy_command("CMDCOR", bucket="growth")))
    public_id = buy["result"]["transaction_id"]
    correction = preview(tenant_database, {
        "operation": "correct_transaction",
        "transaction_id": public_id,
        "replacement": buy_command("CMDCOR", price="12", cash="25.00", bucket="growth"),
    })
    applied = confirm(tenant_database, correction)

    assert applied["status"] == "applied"
    assert tenant_database.execute(
        "SELECT count(*) FROM app.transactions WHERE owner_id = %s AND ticker = 'CMDCOR' AND event_type = 'void'",
        (OWNER_A,),
    ).fetchone()[0] == 1


def test_plan_is_non_trading_and_advances_once_from_separate_deposit_amount(tenant_database):
    today = tenant_database.execute("SELECT current_date").fetchone()[0]
    next_month = tenant_database.execute(
        "SELECT (%s::date + interval '1 month')::date", (today,)
    ).fetchone()[0].isoformat()
    plan = preview(tenant_database, {
        "operation": "plan",
        "ticker": "VTI",
        "deposit_amount": "300.00",
        "cadence": "monthly",
        "next_due_on": today.isoformat(),
        "bucket": "core",
    })
    confirm(tenant_database, plan)
    before_trade_count = tenant_database.execute(
        "SELECT count(*) FROM app.transactions WHERE owner_id = %s AND ticker = 'VTI'",
        (OWNER_A,),
    ).fetchone()[0]

    command = buy_command(
        "VTI", qty="0.789142", price="376.63", fees="0", cash="297.21", bucket="core"
    )
    command["executed_on"] = today.isoformat()
    command["plan_deposit_amount"] = "300.00"
    receipt = preview(tenant_database, command)
    first = confirm(tenant_database, receipt)
    second = confirm(tenant_database, receipt)

    assert first["result"]["plan_advanced_to"] == next_month
    assert second["result"]["plan_advanced_to"] == next_month
    assert tenant_database.execute(
        "SELECT next_due_on FROM app.owner_investment_plans WHERE owner_id = %s AND ticker = 'VTI'",
        (OWNER_A,),
    ).fetchone()[0].isoformat() == next_month
    assert tenant_database.execute(
        "SELECT count(*) FROM app.transactions WHERE owner_id = %s AND ticker = 'VTI'",
        (OWNER_A,),
    ).fetchone()[0] == before_trade_count + 1


def test_sell_all_stop_plan_cancel_and_cross_owner_confirmation(tenant_database):
    today = tenant_database.execute("SELECT current_date").fetchone()[0].isoformat()
    buy = confirm(
        tenant_database,
        preview(tenant_database, buy_command("CMDOPS", qty="3", price="10", cash="31.00")),
    )
    stop_receipt = preview(
        tenant_database, {"operation": "stop", "ticker": "CMDOPS", "stop": "8.50"}
    )
    with pytest.raises(Exception, match="command unavailable"):
        tenant_database.execute(
            "SELECT app.confirm_portfolio_command(%s, 'web', %s, %s, %s)",
            (OWNER_B, OWNER_B, stop_receipt["command_id"], stop_receipt["preview_digest"]),
        )
    tenant_database.rollback()
    confirm(tenant_database, stop_receipt)
    assert tenant_database.execute(
        "SELECT stop FROM app.holdings WHERE owner_id = %s AND ticker = 'CMDOPS'", (OWNER_A,)
    ).fetchone()[0] == Decimal("8.5000")

    sell_all = preview(tenant_database, {
        "operation": "sell_all",
        "ticker": "CMDOPS",
        "fill_price": "12",
        "fees": "1",
        "cash_total": "35.00",
        "executed_on": "2026-09-02",
    })
    closed = confirm(tenant_database, sell_all)
    assert closed["result"]["holding"]["shares"] == 0

    plan = confirm(tenant_database, preview(tenant_database, {
        "operation": "plan",
        "ticker": "ITOT",
        "deposit_amount": "200",
        "cadence": "monthly",
        "next_due_on": today,
        "bucket": "core",
    }))
    assert plan["result"]["operation"] == "plan"
    cancelled = confirm(
        tenant_database,
        preview(tenant_database, {"operation": "cancel_plan", "ticker": "ITOT"}),
    )
    assert cancelled["result"]["active"] is False
    assert buy["result"]["operation"] == "buy"


def test_read_only_command_verifier_accepts_the_migrated_database(tenant_database):
    summary = verify_database(tenant_database)
    assert summary["commands"] >= summary["applied"]


def test_telegram_machine_role_resolves_owner_and_uses_the_same_state_machine(tenant_database):
    request = {
        "chat_id": 1001,
        "user_id": 1001,
        "update_id": 7001,
        "idempotency_key": str(uuid4()),
        "command": buy_command("CMDTG"),
        "confirm_digest": "a" * 64,
        "cancel_digest": "b" * 64,
    }
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "UPDATE app.telegram_links SET telegram_user_id = telegram_chat_id "
            "WHERE telegram_chat_id = 1001"
        )
        tenant_database.execute("SET LOCAL ROLE stock_agent_telegram")
        receipt = tenant_database.execute(
            "SELECT machine.telegram_prepare_command(%s::jsonb)", (Jsonb(request),)
        ).fetchone()[0]
        assert receipt["claimed"] is True
        applied = tenant_database.execute(
            "SELECT machine.telegram_apply_callback(%s::jsonb)",
            (Jsonb({
                "chat_id": 1001,
                "user_id": 1001,
                "update_id": 7002,
                "action": "confirm",
                "token_digest": "a" * 64,
            }),),
        ).fetchone()[0]
        assert applied["status"] == "applied"

    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute("SET LOCAL ROLE stock_agent_telegram")
        with pytest.raises(Exception, match="telegram link unavailable"):
            tenant_database.execute(
                "SELECT machine.telegram_prepare_command(%s::jsonb)",
                (Jsonb({**request, "chat_id": 1002, "user_id": 1002, "update_id": 7003}),),
            )
