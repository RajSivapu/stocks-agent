"""Destructive-to-test-data verification for the confirmed portfolio RPCs.

Uses only the reserved TSTTG ticker and always removes its rows. No credentials or
database responses are printed.
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import db


TICKER = "TSTTG"
PLAN_TICKER = "TSTPLN"
CHAT_ID = 908070601
USER_ID = 908070602


def _cleanup(sb):
    for ticker in (TICKER, PLAN_TICKER):
        sb.table("portfolio_commands").delete().eq("ticker", ticker).execute()
        sb.table("transactions").delete().eq("ticker", ticker).execute()
        sb.table("holdings").delete().eq("ticker", ticker).execute()
    sb.table("owner_investment_plans").delete().eq("ticker", PLAN_TICKER).execute()


def _pending(sb, *, operation, expected_shares, qty=None, price=None, bucket=None, stop=None,
             executed_on=None, ticker=TICKER, amount=None, cadence=None, next_due_on=None,
             expected_plan_updated_at=None):
    row = {
        "id": str(uuid4()),
        "telegram_update_id": time.time_ns(),
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "operation": operation,
        "ticker": ticker,
        "qty": qty,
        "price": price,
        "executed_on": executed_on,
        "bucket": bucket,
        "stop": stop,
        "expected_shares": expected_shares,
        "amount": amount,
        "cadence": cadence,
        "next_due_on": next_due_on,
        "expected_plan_updated_at": expected_plan_updated_at,
        "preview": {"verification": True},
    }
    sb.table("portfolio_commands").insert(row).execute()
    return row["id"]


def _holding_for(sb, ticker):
    rows = sb.table("holdings").select("*").eq("ticker", ticker).execute().data
    return rows[0] if rows else None


def _holding(sb):
    return _holding_for(sb, TICKER)


def _transaction_count(sb):
    return len(sb.table("transactions").select("id").eq("ticker", TICKER).execute().data)


def _transactions(sb):
    return sb.table("transactions").select("*").eq("ticker", TICKER).order("id").execute().data


def _plan(sb):
    rows = sb.table("owner_investment_plans").select("*").eq("ticker", PLAN_TICKER).execute().data
    return rows[0] if rows else None


def _plan_transaction_count(sb):
    return len(sb.table("transactions").select("id").eq("ticker", PLAN_TICKER).execute().data)


def _rpc(sb, name, command_id):
    return sb.rpc(name, {
        "p_command_id": command_id,
        "p_chat_id": CHAT_ID,
        "p_user_id": USER_ID,
    }).execute().data


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    sb = db._sb()
    try:
        _cleanup(sb)

        historical_date = str(date.today() - timedelta(days=2))
        buy_id = _pending(
            sb, operation="buy", expected_shares=0, qty=2, price=100, bucket="growth",
            executed_on=historical_date,
        )
        buy = _rpc(sb, "apply_portfolio_command", buy_id)
        holding = _holding(sb)
        _require(buy["ok"] is True, "confirmed Buy RPC did not succeed")
        _require(holding is not None, "confirmed Buy did not create a holding")
        _require(Decimal(str(holding["shares"])) == Decimal("2"), "confirmed Buy recorded wrong shares")
        _require(Decimal(str(holding["avg_cost"])) == Decimal("100"), "confirmed Buy recorded wrong average cost")
        _require(holding["opened_at"] == historical_date,
                 "confirmed Buy did not preserve the holding open date")
        _require(_transaction_count(sb) == 1, "confirmed Buy did not create exactly one transaction")
        _require(_transactions(sb)[0]["executed_on"] == historical_date,
                 "confirmed Buy did not preserve its execution date")
        print("PASS: confirmed Buy is atomic")

        future_id = _pending(
            sb, operation="buy", expected_shares=2, qty=1, price=105, bucket="growth",
            executed_on=str(date.today() + timedelta(days=1)),
        )
        future = _rpc(sb, "apply_portfolio_command", future_id)
        _require(future["ok"] is False and future["status"] == "rejected",
                 "future execution date was not rejected")
        _require(_transaction_count(sb) == 1, "future execution date created a transaction")
        print("PASS: future execution date is rejected")

        duplicate = _rpc(sb, "apply_portfolio_command", buy_id)
        _require(duplicate["ok"] is True, "repeated Confirm was not idempotently accepted")
        _require(_transaction_count(sb) == 1, "repeated Confirm created a duplicate transaction")
        print("PASS: repeated Confirm is idempotent")

        stale_id = _pending(sb, operation="buy", expected_shares=1, qty=1, price=105, bucket="growth")
        stale = _rpc(sb, "apply_portfolio_command", stale_id)
        _require(stale["ok"] is False and stale["status"] == "rejected", "stale command was not rejected")
        _require(Decimal(str(_holding(sb)["shares"])) == Decimal("2"), "stale command changed the holding")
        _require(_transaction_count(sb) == 1, "stale command created a transaction")
        print("PASS: stale expected shares are rejected")

        sell_id = _pending(sb, operation="sell", expected_shares=2, qty=1, price=120)
        sell = _rpc(sb, "apply_portfolio_command", sell_id)
        _require(sell["ok"] is True, "confirmed Sell RPC did not succeed")
        _require(Decimal(str(sell["realized_pnl"])) == Decimal("20"), "confirmed Sell recorded wrong P&L")
        _require(Decimal(str(_holding(sb)["shares"])) == Decimal("1"), "confirmed Sell recorded wrong shares")
        _require(_transaction_count(sb) == 2, "confirmed Sell did not create exactly one transaction")
        print("PASS: confirmed Sell records realized P&L")

        cancel_id = _pending(sb, operation="stop", expected_shares=1, stop=90)
        cancelled = _rpc(sb, "cancel_portfolio_command", cancel_id)
        after_cancel = _rpc(sb, "apply_portfolio_command", cancel_id)
        _require(cancelled["ok"] is True and cancelled["status"] == "cancelled", "Cancel RPC did not succeed")
        _require(after_cancel["ok"] is False and after_cancel["status"] == "cancelled", "cancelled command was applied")
        _require(_holding(sb)["stop"] is None, "cancelled stop command changed the holding")
        _require(_transaction_count(sb) == 2, "cancelled command created a transaction")
        print("PASS: Cancel prevents mutation")

        _cleanup(sb)
        later_date = str(date.today() - timedelta(days=2))
        earlier_date = str(date.today() - timedelta(days=5))
        later_buy_id = _pending(
            sb, operation="buy", expected_shares=0, qty=1, price=100, bucket="growth",
            executed_on=later_date,
        )
        _require(_rpc(sb, "apply_portfolio_command", later_buy_id)["ok"] is True,
                 "later historical Buy did not succeed")
        earlier_buy_id = _pending(
            sb, operation="buy", expected_shares=1, qty=1, price=90, bucket="growth",
            executed_on=earlier_date,
        )
        _require(_rpc(sb, "apply_portfolio_command", earlier_buy_id)["ok"] is True,
                 "earlier historical Buy did not succeed")
        _require(_holding(sb)["opened_at"] == earlier_date,
                 "out-of-order Buy did not preserve the earliest holding open date")
        print("PASS: out-of-order Buys preserve the earliest open date")

        _cleanup(sb)
        due_on = str(date.today())
        create_plan_id = _pending(
            sb, operation="plan", expected_shares=None, ticker=PLAN_TICKER,
            bucket="core", amount=250, cadence="monthly", next_due_on=due_on,
        )
        created = _rpc(sb, "apply_portfolio_command", create_plan_id)
        plan = _plan(sb)
        _require(created["ok"] is True and created["operation"] == "plan",
                 "confirmed Plan RPC did not succeed")
        _require(plan is not None and plan["active"] is True, "confirmed Plan was not stored")
        _require(_plan_transaction_count(sb) == 0 and _holding_for(sb, PLAN_TICKER) is None,
                 "Plan created a transaction or holding")
        duplicate_plan = _rpc(sb, "apply_portfolio_command", create_plan_id)
        _require(duplicate_plan.get("duplicate") is True, "repeated Plan confirmation was not idempotent")
        print("PASS: confirmed Plan is non-trading and idempotent")

        old_updated_at = plan["updated_at"]
        stale_cancel_id = _pending(
            sb, operation="cancel_plan", expected_shares=None, ticker=PLAN_TICKER,
            expected_plan_updated_at=old_updated_at,
        )
        update_plan_id = _pending(
            sb, operation="plan", expected_shares=None, ticker=PLAN_TICKER,
            bucket="core", amount=300, cadence="monthly", next_due_on=due_on,
            expected_plan_updated_at=old_updated_at,
        )
        _require(_rpc(sb, "apply_portfolio_command", update_plan_id)["ok"] is True,
                 "confirmed Plan update did not succeed")
        stale_cancel = _rpc(sb, "apply_portfolio_command", stale_cancel_id)
        _require(stale_cancel["ok"] is False and stale_cancel["status"] == "rejected",
                 "stale Plan cancellation was not rejected")
        print("PASS: stale Plan confirmation is rejected")

        off_amount_buy_id = _pending(
            sb, operation="buy", expected_shares=0, ticker=PLAN_TICKER,
            qty=1, price=100, bucket="core", executed_on=due_on,
        )
        _require(_rpc(sb, "apply_portfolio_command", off_amount_buy_id)["ok"] is True,
                 "off-amount Buy did not succeed")
        _require(_plan(sb)["next_due_on"] == due_on, "off-amount Buy advanced the plan")
        matching_buy_id = _pending(
            sb, operation="buy", expected_shares=1, ticker=PLAN_TICKER,
            qty=2, price=150, bucket="core", executed_on=due_on,
        )
        matching_buy = _rpc(sb, "apply_portfolio_command", matching_buy_id)
        advanced_on = _plan(sb)["next_due_on"]
        _require(matching_buy.get("plan_advanced_to") == advanced_on and advanced_on > due_on,
                 "matching confirmed Buy did not advance the plan once")
        _rpc(sb, "apply_portfolio_command", matching_buy_id)
        _require(_plan(sb)["next_due_on"] == advanced_on,
                 "repeated Buy confirmation advanced the plan twice")
        print("PASS: only a matching confirmed Buy advances the Plan once")

        current_plan = _plan(sb)
        cancel_plan_id = _pending(
            sb, operation="cancel_plan", expected_shares=None, ticker=PLAN_TICKER,
            expected_plan_updated_at=current_plan["updated_at"],
        )
        cancelled_plan = _rpc(sb, "apply_portfolio_command", cancel_plan_id)
        _require(cancelled_plan["ok"] is True and _plan(sb)["active"] is False,
                 "confirmed Plan cancellation did not deactivate it")
        transactions_before_duplicate_cancel = _plan_transaction_count(sb)
        _rpc(sb, "apply_portfolio_command", cancel_plan_id)
        _require(_plan_transaction_count(sb) == transactions_before_duplicate_cancel,
                 "repeated Plan cancellation created a transaction")
        print("PASS: confirmed Plan cancellation is non-trading and idempotent")
        return 0
    except Exception as exc:
        print(f"FAIL: portfolio RPC verification ({type(exc).__name__})")
        return 1
    finally:
        try:
            _cleanup(sb)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
