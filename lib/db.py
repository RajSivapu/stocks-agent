from supabase import create_client, Client
from lib import config
from datetime import date as _date


def _sb() -> Client:
    return create_client(
        config.secret("supabase_url"),
        config.secret("supabase_service_role_key"),
    )


def init_schema():
    _sb().table("holdings").select("ticker").limit(1).execute()


def _insert(table, row) -> int:
    res = _sb().table(table).insert(row).execute()
    return res.data[0]["id"] if res.data else None


def insert_suggestion(row): return _insert("suggestions", row)
def insert_transaction(row): return _insert("transactions", row)
def insert_observation(row): return _insert("stock_observations", row)
def insert_grade(row): return _insert("suggestion_grades", row)
def insert_paper_watch(row): return _insert("paper_watches", row)


def get_active_paper_watches():
    return _sb().table("paper_watches").select("*").eq("status", "active").order("created", desc=True).execute().data


def close_paper_watch(pid, close_price, closed_date):
    _sb().table("paper_watches").update({
        "status": "closed",
        "close_price": close_price,
        "closed_date": str(closed_date),
    }).eq("id", pid).execute()


def upsert_holding(row):
    row = {**{"stop": None, "target": None, "high_water_price": None}, **row}
    sb = _sb()
    existing = sb.table("holdings").select("stop,target,high_water_price").eq("ticker", row["ticker"]).execute().data
    if existing:
        ex = existing[0]
        row["stop"] = row["stop"] if row["stop"] is not None else ex.get("stop")
        row["target"] = row["target"] if row["target"] is not None else ex.get("target")
        row["high_water_price"] = row["high_water_price"] if row["high_water_price"] is not None else ex.get("high_water_price")
    sb.table("holdings").upsert(row, on_conflict="ticker").execute()


def update_holding_stop(ticker, stop=None, target=None, high_water_price=None):
    updates = {k: v for k, v in (("stop", stop), ("target", target), ("high_water_price", high_water_price)) if v is not None}
    if not updates:
        return
    _sb().table("holdings").update(updates).eq("ticker", ticker).execute()


def upsert_daily_snapshot(row):
    _sb().table("daily_snapshots").upsert(row, on_conflict="snap_date,ticker").execute()


def insert_lesson(row):
    _sb().table("lessons").insert(row).execute()


def get_lessons(limit=20):
    return _sb().table("lessons").select("*").order("entry_date", desc=True).order("id", desc=True).limit(limit).execute().data


def get_holdings():
    return _sb().table("holdings").select("*").order("ticker").execute().data


def get_open_suggestions():
    today = str(_date.today())
    return _sb().table("suggestions").select("*").gte("valid_until", today).eq("action", "Buy").order("date", desc=True).execute().data


def get_observations(ticker):
    return _sb().table("stock_observations").select("*").eq("ticker", ticker).order("obs_date", desc=True).execute().data


def recent_lessons_rows():
    return _sb().table("suggestion_grades").select("*").order("graded_at", desc=True).limit(50).execute().data


def get_dry_powder(month):
    res = _sb().table("dry_powder").select("*").eq("month", month).execute()
    return res.data[0] if res.data else None


def set_dry_powder(row):
    _sb().table("dry_powder").upsert(row, on_conflict="month").execute()
