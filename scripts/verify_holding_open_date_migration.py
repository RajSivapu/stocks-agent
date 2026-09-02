"""Verify the historical holding-date repair inside a rolled-back transaction."""
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import config


TICKERS = (
    "TSTOD1", "TSTOD2", "TSTOD3", "TSTOD4", "TSTOD5", "TSTOD6", "TSTOD7"
)
MIGRATION = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "20260901_reliable_stock_agent.sql"


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _seed(cur):
    cur.execute("DELETE FROM public.portfolio_commands WHERE ticker = ANY(%s)", (list(TICKERS),))
    cur.execute("DELETE FROM public.transactions WHERE ticker = ANY(%s)", (list(TICKERS),))
    cur.execute("DELETE FROM public.holdings WHERE ticker = ANY(%s)", (list(TICKERS),))

    cur.execute(
        """
        INSERT INTO public.holdings (ticker, shares, avg_cost, bucket, opened_at)
        VALUES
          ('TSTOD1', 2, 100, 'growth', DATE '2026-09-02'),
          ('TSTOD2', 3, 100, 'growth', DATE '2026-09-02'),
          ('TSTOD3', 2, 110, 'growth', DATE '2026-09-02'),
          ('TSTOD4', 1, 100, 'growth', DATE '2026-09-01'),
          ('TSTOD5', 2, 100, 'growth', DATE '2026-09-01'),
          ('TSTOD6', 2, 100, 'growth', DATE '2026-09-01'),
          ('TSTOD7', 2, 100, 'growth', DATE '2026-09-01')
        """
    )
    cur.execute(
        """
        INSERT INTO public.transactions (ticker, side, qty, price, source, executed_on, ts)
        VALUES
          ('TSTOD1', 'buy', 2, 100, 'telegram', DATE '2026-08-01', TIMESTAMPTZ '2026-09-02 15:00:00+00'),
          ('TSTOD2', 'buy', 1, 110, 'telegram', DATE '2026-08-10', TIMESTAMPTZ '2026-09-02 15:00:00+00'),
          ('TSTOD2', 'buy', 2, 95, 'telegram', DATE '2026-08-05', TIMESTAMPTZ '2026-09-03 15:00:00+00'),
          ('TSTOD3', 'buy', 4, 90, 'telegram', DATE '2026-07-01', TIMESTAMPTZ '2026-07-01 15:00:00+00'),
          ('TSTOD3', 'sell', 4, 100, 'telegram', DATE '2026-07-10', TIMESTAMPTZ '2026-07-10 15:00:00+00'),
          ('TSTOD3', 'buy', 2, 110, 'telegram', DATE '2026-08-20', TIMESTAMPTZ '2026-09-02 15:00:00+00'),
          ('TSTOD4', 'buy', 1, 100, 'owner', DATE '2026-08-01', TIMESTAMPTZ '2026-09-01 15:00:00+00'),
          ('TSTOD5', 'buy', 1, 100, 'telegram', DATE '2026-08-01', TIMESTAMPTZ '2026-09-01 15:00:00+00'),
          ('TSTOD6', 'buy', 1, 100, 'telegram', DATE '2026-08-01', TIMESTAMPTZ '2026-09-01 15:00:00+00'),
          ('TSTOD6', 'buy', 1, 100, NULL, DATE '2026-08-02', TIMESTAMPTZ '2026-09-01 16:00:00+00'),
          ('TSTOD7', 'sell', 1, 100, 'telegram', DATE '2026-08-01', TIMESTAMPTZ '2026-09-01 15:00:00+00'),
          ('TSTOD7', 'buy', 3, 100, 'telegram', DATE '2026-08-02', TIMESTAMPTZ '2026-09-01 16:00:00+00')
        """
    )


def _dates(cur):
    cur.execute(
        "SELECT ticker, opened_at::text FROM public.holdings WHERE ticker = ANY(%s) ORDER BY ticker",
        (list(TICKERS),),
    )
    return dict(cur.fetchall())


def main():
    conn = psycopg.connect(config.secret("postgres_url"))
    try:
        with conn.cursor() as cur:
            _seed(cur)
            migration = MIGRATION.read_text()
            cur.execute(migration)
            first = _dates(cur)
            _require(first["TSTOD1"] == "2026-08-01", "legacy open date was not repaired")
            _require(first["TSTOD2"] == "2026-08-05", "out-of-order buys were not repaired")
            _require(first["TSTOD3"] == "2026-08-20", "reopened position used an older lifecycle")
            _require(first["TSTOD4"] == "2026-09-01", "unrelated owner holding was changed")
            _require(first["TSTOD5"] == "2026-09-01", "incomplete ledger holding was changed")
            _require(first["TSTOD6"] == "2026-09-01", "NULL-source ledger holding was changed")
            _require(first["TSTOD7"] == "2026-09-01", "negative-balance ledger holding was changed")

            cur.execute(migration)
            _require(_dates(cur) == first, "holding-date repair is not idempotent")
        print("PASS: holding open-date migration is lifecycle-safe and idempotent")
        return 0
    except Exception as exc:
        print(f"FAIL: holding open-date migration verification ({type(exc).__name__})")
        return 1
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
