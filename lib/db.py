import psycopg, pathlib
from psycopg import sql
from lib import config
ROOT = pathlib.Path(__file__).resolve().parents[1]

def conn():
    return psycopg.connect(config.secret("postgres_url"))

def init_schema():
    sql = (ROOT / "sql" / "schema.sql").read_text()
    with conn() as c:
        c.execute(sql); c.commit()

def _insert(table, row) -> int:
    cols = list(row)
    q = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) RETURNING id").format(
        t=sql.Identifier(table),
        c=sql.SQL(",").join(map(sql.Identifier, cols)),
        v=sql.SQL(",").join([sql.Placeholder()] * len(cols)))
    with conn() as c:
        cur = c.execute(q, list(row.values())); c.commit()
        r = cur.fetchone()
        return r[0] if r else None

def insert_suggestion(row): return _insert("suggestions", row)
def insert_transaction(row): return _insert("transactions", row)
def insert_observation(row): return _insert("stock_observations", row)
def insert_grade(row): return _insert("suggestion_grades", row)

def upsert_holding(row):
    q = """INSERT INTO holdings (ticker,shares,avg_cost,bucket,opened_at,notes)
           VALUES (%(ticker)s,%(shares)s,%(avg_cost)s,%(bucket)s,%(opened_at)s,%(notes)s)
           ON CONFLICT (ticker) DO UPDATE SET shares=EXCLUDED.shares,
           avg_cost=EXCLUDED.avg_cost, bucket=EXCLUDED.bucket, notes=EXCLUDED.notes"""
    with conn() as c: c.execute(q, row); c.commit()

def upsert_daily_snapshot(row):
    q = """INSERT INTO daily_snapshots (snap_date,ticker,close,day_move_pct,rsi14,sma50,sma200,macd_hist)
           VALUES (%(snap_date)s,%(ticker)s,%(close)s,%(day_move_pct)s,%(rsi14)s,%(sma50)s,%(sma200)s,%(macd_hist)s)
           ON CONFLICT (snap_date,ticker) DO UPDATE SET close=EXCLUDED.close,
           day_move_pct=EXCLUDED.day_move_pct, rsi14=EXCLUDED.rsi14, sma50=EXCLUDED.sma50,
           sma200=EXCLUDED.sma200, macd_hist=EXCLUDED.macd_hist"""
    with conn() as c: c.execute(q, row); c.commit()

def _rows(q, args=()):
    with conn() as c:
        cur = c.execute(q, args); cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def get_holdings(): return _rows("SELECT * FROM holdings ORDER BY ticker")
def get_open_suggestions():
    return _rows("SELECT * FROM suggestions WHERE valid_until >= CURRENT_DATE AND action='Buy' ORDER BY date DESC")
def get_observations(ticker): return _rows("SELECT * FROM stock_observations WHERE ticker=%s ORDER BY obs_date DESC", (ticker,))
def recent_lessons_rows(): return _rows("SELECT * FROM suggestion_grades ORDER BY graded_at DESC LIMIT 50")
def get_dry_powder(month):
    r = _rows("SELECT * FROM dry_powder WHERE month=%s", (month,)); return r[0] if r else None
def set_dry_powder(row):
    q = """INSERT INTO dry_powder (month,growth_available,spec_available,rolled_months)
           VALUES (%(month)s,%(growth_available)s,%(spec_available)s,%(rolled_months)s)
           ON CONFLICT (month) DO UPDATE SET growth_available=EXCLUDED.growth_available,
           spec_available=EXCLUDED.spec_available, rolled_months=EXCLUDED.rolled_months"""
    with conn() as c: c.execute(q, row); c.commit()
