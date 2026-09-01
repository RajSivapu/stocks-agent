from supabase import create_client, Client
from lib import config
from datetime import date as _date, datetime as _datetime, timezone as _timezone
from uuid import uuid4


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


def start_analysis_run(kind, started_at=None):
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("analysis run kind must be a non-empty string")
    run_id = str(uuid4())
    row = {"id": run_id, "kind": kind.strip(), "status": "running"}
    if started_at is not None:
        row["started_at"] = str(started_at)
    _sb().table("analysis_runs").insert(row).execute()
    return run_id


def finish_analysis_run(run_id, *, status, data_as_of=None, source_status=None, symbols=None,
                        write_counts=None, telegram_message_ids=None, summary=None, error=None):
    if not run_id:
        raise ValueError("run_id is required")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("status must be a non-empty string")
    updates = {
        "status": status.strip(),
        "finished_at": _datetime.now(_timezone.utc).isoformat(),
    }
    optional = {
        "data_as_of": str(data_as_of) if data_as_of is not None else None,
        "source_status": source_status,
        "symbols": symbols,
        "write_counts": write_counts,
        "telegram_message_ids": telegram_message_ids,
        "summary": str(summary)[:2000] if summary is not None else None,
        "error": str(error)[:1000] if error is not None else None,
    }
    updates.update({key: value for key, value in optional.items() if value is not None})
    _sb().table("analysis_runs").update(updates).eq("id", str(run_id)).execute()


def _bounded_recent(table, order_column, limit):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")
    return _sb().table(table).select("*").order(order_column, desc=True).limit(limit).execute().data


def get_recent_transactions(limit=50):
    return _bounded_recent("transactions", "ts", limit)


def get_recent_suggestions(limit=50):
    return _bounded_recent("suggestions", "ts", limit)


def get_recent_grades(limit=100):
    return _bounded_recent("suggestion_grades", "graded_at", limit)


def get_recent_snapshots(limit=100):
    return _bounded_recent("daily_snapshots", "snap_date", limit)


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


def _set_holding_flag(ticker, field, active):
    _sb().table("holdings").update({field: active}).eq("ticker", ticker).execute()


def set_stop_alert_active(ticker, active):
    _set_holding_flag(ticker, "stop_alert_active", active)


def set_stop_near_alert_active(ticker, active):
    _set_holding_flag(ticker, "stop_near_alert_active", active)


def set_target_near_alert_active(ticker, active):
    _set_holding_flag(ticker, "target_near_alert_active", active)


def set_target_alert_active(ticker, active):
    _set_holding_flag(ticker, "target_alert_active", active)


def set_hold_override(ticker, until_date, reason=None):
    updates = {"hold_override_until": str(until_date)}
    if reason:
        updates["notes"] = reason
    _sb().table("holdings").update(updates).eq("ticker", ticker).execute()


def upsert_daily_snapshot(row):
    _sb().table("daily_snapshots").upsert(row, on_conflict="snap_date,ticker").execute()


def insert_lesson(row):
    _sb().table("lessons").insert(row).execute()


def get_lessons(limit=20):
    return _sb().table("lessons").select("*").order("entry_date", desc=True).order("id", desc=True).limit(limit).execute().data


def get_holdings():
    return _sb().table("holdings").select("*").order("ticker").execute().data


def get_latest_suggestion(ticker):
    rows = (_sb().table("suggestions").select("*")
            .eq("ticker", str(ticker).upper())
            .order("date", desc=True).limit(1).execute().data)
    return rows[0] if rows else None


def get_latest_buy_levels(ticker):
    rows = (_sb().table("suggestions").select("stop,target")
            .eq("ticker", str(ticker).upper()).eq("action", "Buy")
            .order("date", desc=True).limit(1).execute().data)
    return rows[0] if rows else None


def delete_holding(ticker):
    _sb().table("holdings").delete().eq("ticker", str(ticker).upper()).execute()


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


def get_radar():
    return _sb().table("radar").select("*").execute().data


def upsert_radar(row):
    _sb().table("radar").upsert(row, on_conflict="ticker").execute()


def delete_radar(ticker):
    _sb().table("radar").delete().eq("ticker", ticker).execute()
