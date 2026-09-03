from __future__ import annotations

import random
import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from scripts.verify_ledger_projection import fold_events, verify_database


OWNER_A = "11111111-1111-4111-8111-111111111111"


def append_trade(
    connection,
    ticker,
    side,
    qty,
    price,
    *,
    fees="0",
    executed_on="2026-09-02",
    bucket="growth",
):
    return connection.execute(
        "SELECT app.append_ledger_trade(%s, %s::jsonb)",
        (
            OWNER_A,
            Jsonb({
                "ticker": ticker,
                "side": side,
                "qty": str(qty),
                "price": str(price),
                "fees": str(fees),
                "executed_on": executed_on,
                "bucket": bucket if side == "buy" else None,
                "source_channel": "operator",
            }),
        ),
    ).fetchone()[0]


def test_ledger_numeric_domains_and_immutability_are_catalog_enforced(tenant_database):
    columns = tenant_database.execute(
        """
        SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'app'
          AND (table_name, column_name) IN (
            ('transactions', 'qty'), ('transactions', 'price'), ('transactions', 'fees'),
            ('holdings', 'shares'), ('holdings', 'avg_cost'),
            ('holdings', 'projection_sequence')
          )
        """
    ).fetchall()
    observed = {(table, column): (kind, precision, scale, nullable) for table, column, kind, precision, scale, nullable in columns}

    assert observed[("transactions", "qty")] == ("numeric", 20, 8, "YES")
    assert observed[("transactions", "price")] == ("numeric", 20, 4, "YES")
    assert observed[("transactions", "fees")] == ("numeric", 20, 2, "NO")
    assert observed[("holdings", "shares")] == ("numeric", 20, 8, "NO")
    assert observed[("holdings", "avg_cost")] == ("numeric", 20, 4, "NO")
    assert observed[("holdings", "projection_sequence")][-1] == "NO"

    transaction_id = append_trade(tenant_database, "TSTIMM", "buy", "1", "10")["transaction_id"]
    with pytest.raises(Exception, match="ledger is append-only"):
        tenant_database.execute(
            "UPDATE app.transactions SET price = 11 WHERE id = %s", (transaction_id,)
        )
    tenant_database.rollback()


def test_fee_aware_partial_and_full_sell_projection(tenant_database):
    ticker = "TSTFEE"
    append_trade(tenant_database, ticker, "buy", "10", "10", fees="1", executed_on="2026-08-01")
    partial = append_trade(
        tenant_database, ticker, "sell", "4", "15", fees="1", executed_on="2026-08-02"
    )

    assert partial["holding"]["shares"] == 6
    assert Decimal(str(partial["holding"]["avg_cost"])) == Decimal("10.1000")
    assert Decimal(str(partial["holding"]["realized_pnl"])) == Decimal("18.60")

    closed = append_trade(
        tenant_database, ticker, "sell", "6", "11", fees="0.50", executed_on="2026-08-03"
    )
    assert closed["holding"]["shares"] == 0
    assert tenant_database.execute(
        "SELECT count(*) FROM app.holdings WHERE owner_id = %s AND ticker = %s",
        (OWNER_A, ticker),
    ).fetchone()[0] == 0


def test_backdated_trade_that_creates_historical_negative_balance_is_rejected(tenant_database):
    ticker = "TSTBACK"
    append_trade(tenant_database, ticker, "buy", "1", "10", executed_on="2026-08-10")

    with pytest.raises(Exception, match="negative historical balance"):
        append_trade(
            tenant_database, ticker, "sell", "1", "11", executed_on="2026-08-09"
        )
    tenant_database.rollback()

    assert tenant_database.execute(
        "SELECT count(*) FROM app.transactions WHERE owner_id = %s AND ticker = %s",
        (OWNER_A, ticker),
    ).fetchone()[0] == 1


def test_void_and_replace_correction_rebuilds_without_mutating_history(tenant_database):
    ticker = "TSTCORR"
    original = append_trade(
        tenant_database, ticker, "buy", "10", "10", fees="1", executed_on="2026-08-01"
    )
    append_trade(tenant_database, ticker, "sell", "4", "15", fees="1", executed_on="2026-08-02")

    corrected = tenant_database.execute(
        "SELECT app.correct_ledger_trade(%s, %s, %s::jsonb)",
        (
            OWNER_A,
            original["transaction_id"],
            Jsonb({
                "qty": "10",
                "price": "12",
                "fees": "1",
                "executed_on": "2026-08-01",
                "bucket": "growth",
                "source_channel": "operator",
            }),
        ),
    ).fetchone()[0]

    assert Decimal(str(corrected["holding"]["avg_cost"])) == Decimal("12.1000")
    assert Decimal(str(corrected["holding"]["realized_pnl"])) == Decimal("10.60")
    events = tenant_database.execute(
        """
        SELECT event_type, corrects_transaction_id
        FROM app.transactions
        WHERE owner_id = %s AND ticker = %s
        ORDER BY ledger_sequence
        """,
        (OWNER_A, ticker),
    ).fetchall()
    assert events[-2:] == [("void", original["transaction_id"]), ("trade", original["transaction_id"])]


def test_zero_balance_resets_opened_at_for_next_lifecycle(tenant_database):
    ticker = "TSTLIFE"
    append_trade(tenant_database, ticker, "buy", "2", "10", executed_on="2026-07-01")
    append_trade(tenant_database, ticker, "sell", "2", "12", executed_on="2026-07-02")
    reopened = append_trade(
        tenant_database, ticker, "buy", "1", "20", executed_on="2026-08-15", bucket="speculative"
    )

    assert reopened["holding"]["opened_at"] == "2026-08-15"
    assert reopened["holding"]["bucket"] == "speculative"


def test_sql_fold_matches_500_deterministic_python_interleavings(tenant_database):
    randomizer = random.Random(20260902)
    expected = {}
    rows = []
    sequence = tenant_database.execute(
        "SELECT coalesce(max(ledger_sequence), 0) FROM app.transactions WHERE owner_id = %s",
        (OWNER_A,),
    ).fetchone()[0]
    for case in range(500):
        ticker = f"TP{case:04d}"
        balance = Decimal("0")
        events = []
        event_date = date(2026, 1, 1)
        for _ in range(randomizer.randint(2, 8)):
            if balance == 0 or randomizer.random() < 0.65:
                side = "buy"
                qty = Decimal(randomizer.randint(1, 20)) / Decimal("8")
                balance += qty
            else:
                side = "sell"
                eighths = int(balance * 8)
                qty = Decimal(randomizer.randint(1, eighths)) / Decimal("8")
                balance -= qty
            price = Decimal(randomizer.randint(100, 5000)) / Decimal("10")
            fees = Decimal(randomizer.randint(0, 200)) / Decimal("100")
            sequence += 1
            event = {
                "event_type": "trade",
                "side": side,
                "qty": str(qty),
                "price": str(price),
                "fees": str(fees),
                "executed_on": event_date.isoformat(),
                "ledger_sequence": sequence,
                "bucket": "growth" if side == "buy" else None,
            }
            events.append(event)
            rows.append((OWNER_A, ticker, side, qty, price, fees, event_date, sequence, event["bucket"]))
            event_date += timedelta(days=randomizer.randint(0, 2))
        expected[ticker] = fold_events(events)

    with tenant_database.transaction(force_rollback=True):
        with tenant_database.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO app.transactions
                  (owner_id, ticker, event_type, side, qty, price, fees, executed_on,
                   ledger_sequence, bucket, source_channel)
                VALUES (%s, %s, 'trade', %s, %s, %s, %s, %s, %s, %s, 'operator')
                """,
                rows,
            )
        for ticker, python_result in expected.items():
            sql_result = tenant_database.execute(
                "SELECT app.fold_holding(%s, %s)", (OWNER_A, ticker)
            ).fetchone()[0]
            assert Decimal(str(sql_result["shares"])) == python_result["shares"], ticker
            assert Decimal(str(sql_result["avg_cost"])) == python_result["avg_cost"], ticker
            assert Decimal(str(sql_result["realized_pnl"])) == python_result["realized_pnl"], (
                ticker,
                python_result,
            )


def test_concurrent_first_buys_serialize_without_lost_updates(tenant_database):
    import psycopg
    from psycopg.types.json import Jsonb

    owner_id = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    tenant_database.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, 'ledger-race@example.com')",
        (owner_id,),
    )
    tenant_database.execute(
        "INSERT INTO app.profiles (id, status) VALUES (%s, 'active')", (owner_id,)
    )
    barrier = threading.Barrier(2)
    failures = []

    def worker():
        try:
            with psycopg.connect(tenant_database.info.dsn) as connection:
                barrier.wait(timeout=5)
                connection.execute(
                    "SELECT app.append_ledger_trade(%s, %s::jsonb)",
                    (
                        owner_id,
                        Jsonb({
                            "ticker": "TSTRACE",
                            "side": "buy",
                            "qty": "1",
                            "price": "10",
                            "fees": "0",
                            "executed_on": "2026-09-02",
                            "bucket": "growth",
                            "source_channel": "operator",
                        }),
                    ),
                )
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    assert all(not thread.is_alive() for thread in threads)
    assert tenant_database.execute(
        "SELECT shares FROM app.holdings WHERE owner_id = %s AND ticker = 'TSTRACE'",
        (owner_id,),
    ).fetchone()[0] == 2
    sequences = tenant_database.execute(
        """
        SELECT ledger_sequence FROM app.transactions
        WHERE owner_id = %s AND ticker = 'TSTRACE'
        ORDER BY ledger_sequence
        """,
        (owner_id,),
    ).fetchall()
    assert len(sequences) == 2
    assert sequences[0][0] != sequences[1][0]


def test_nightly_verifier_detects_missing_projection_without_exposing_identity(tenant_database):
    passing = verify_database(tenant_database)
    assert passing
    assert all(row["status"] == "passed" for row in passing)

    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "DELETE FROM app.holdings WHERE owner_id = %s AND ticker = 'TSTAAA'",
            (OWNER_A,),
        )
        failing = verify_database(tenant_database)
        assert any(row["status"] == "failed" for row in failing)
        rendered = str(failing)
        assert OWNER_A not in rendered
        assert "TSTAAA" not in rendered
