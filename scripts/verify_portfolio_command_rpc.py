"""Destructive-to-test-data verification for the confirmed portfolio RPCs.

Uses only the reserved TSTTG ticker and always removes its rows. No credentials or
database responses are printed.
"""
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


def _pending(sb, *, operation, expected_shares, qty=None, price=None, bucket=None, stop=None):
    row = {
        "id": str(uuid4()),
        "telegram_update_id": time.time_ns(),
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "operation": operation,
        "ticker": TICKER,
        "qty": qty,
        "price": price,
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


def _rpc(sb, name, command_id):
    return sb.rpc(name, {
        "p_command_id": command_id,
        "p_chat_id": CHAT_ID,
        "p_user_id": USER_ID,
    }).execute().data


def main():
    sb = db._sb()
    try:
        _cleanup(sb)

        buy_id = _pending(sb, operation="buy", expected_shares=0, qty=2, price=100, bucket="growth")
        buy = _rpc(sb, "apply_portfolio_command", buy_id)
        holding = _holding(sb)
        assert buy["ok"] is True
        assert Decimal(str(holding["shares"])) == Decimal("2")
        assert Decimal(str(holding["avg_cost"])) == Decimal("100")
        assert _transaction_count(sb) == 1
        print("PASS: confirmed Buy is atomic")

        duplicate = _rpc(sb, "apply_portfolio_command", buy_id)
        assert duplicate["ok"] is True
        assert _transaction_count(sb) == 1
        print("PASS: repeated Confirm is idempotent")

        stale_id = _pending(sb, operation="buy", expected_shares=1, qty=1, price=105, bucket="growth")
        stale = _rpc(sb, "apply_portfolio_command", stale_id)
        assert stale["ok"] is False and stale["status"] == "rejected"
        assert Decimal(str(_holding(sb)["shares"])) == Decimal("2")
        assert _transaction_count(sb) == 1
        print("PASS: stale expected shares are rejected")

        sell_id = _pending(sb, operation="sell", expected_shares=2, qty=1, price=120)
        sell = _rpc(sb, "apply_portfolio_command", sell_id)
        assert sell["ok"] is True
        assert Decimal(str(sell["realized_pnl"])) == Decimal("20")
        assert Decimal(str(_holding(sb)["shares"])) == Decimal("1")
        assert _transaction_count(sb) == 2
        print("PASS: confirmed Sell records realized P&L")

        cancel_id = _pending(sb, operation="stop", expected_shares=1, stop=90)
        cancelled = _rpc(sb, "cancel_portfolio_command", cancel_id)
        after_cancel = _rpc(sb, "apply_portfolio_command", cancel_id)
        assert cancelled["ok"] is True and cancelled["status"] == "cancelled"
        assert after_cancel["ok"] is False and after_cancel["status"] == "cancelled"
        assert _holding(sb)["stop"] is None
        assert _transaction_count(sb) == 2
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
