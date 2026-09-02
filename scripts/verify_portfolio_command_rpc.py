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
CHAT_ID = 908070601
USER_ID = 908070602


def _cleanup(sb):
    sb.table("portfolio_commands").delete().eq("ticker", TICKER).execute()
    sb.table("transactions").delete().eq("ticker", TICKER).execute()
    sb.table("holdings").delete().eq("ticker", TICKER).execute()


def _pending(sb, *, operation, expected_shares, qty=None, price=None, bucket=None, stop=None,
             executed_on=None):
    row = {
        "id": str(uuid4()),
        "telegram_update_id": time.time_ns(),
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "operation": operation,
        "ticker": TICKER,
        "qty": qty,
        "price": price,
        "executed_on": executed_on,
        "bucket": bucket,
        "stop": stop,
        "expected_shares": expected_shares,
        "preview": {"verification": True},
    }
    sb.table("portfolio_commands").insert(row).execute()
    return row["id"]


def _holding(sb):
    rows = sb.table("holdings").select("*").eq("ticker", TICKER).execute().data
    return rows[0] if rows else None


def _transaction_count(sb):
    return len(sb.table("transactions").select("id").eq("ticker", TICKER).execute().data)


def _transactions(sb):
    return sb.table("transactions").select("*").eq("ticker", TICKER).order("id").execute().data


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
