import json
import os
from pathlib import Path

import pytest
from datetime import date
from uuid import uuid4
from lib import db
from supabase import create_client
from tests.integration_guard import require_isolated_supabase_test_project

TEST_SECRETS_PATH = Path("config/test-secrets.local.json")


def configured_test_url() -> str:
    return os.environ["SUPABASE_URL"]


def configured_test_service_key() -> str:
    try:
        value = json.loads(TEST_SECRETS_PATH.read_text())["supabase_service_role_key"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("isolated test service-role credentials are required") from error
    if not isinstance(value, str) or not value:
        raise RuntimeError("isolated test service-role credentials are required")
    return value


@pytest.fixture
def isolated_db(monkeypatch):
    require_isolated_supabase_test_project(os.environ, TEST_SECRETS_PATH)
    client = create_client(configured_test_url(), configured_test_service_key())
    monkeypatch.setattr(db, "_sb", lambda: client)
    cleanups = []
    try:
        yield client, cleanups
    finally:
        for cleanup in reversed(cleanups):
            cleanup()


class _RecordingQuery:
    def __init__(self, calls, table):
        self.calls = calls
        self.table = table

    def select(self, columns):
        self.calls.append((self.table, "select", columns))
        return self

    def order(self, column, desc=False):
        self.calls.append((self.table, "order", column, desc))
        return self

    def limit(self, value):
        self.calls.append((self.table, "limit", value))
        return self

    def update(self, payload):
        self.calls.append((self.table, "update", payload))
        return self

    def delete(self):
        self.calls.append((self.table, "delete"))
        return self

    def eq(self, column, value):
        self.calls.append((self.table, "eq", column, value))
        return self

    def execute(self):
        self.calls.append((self.table, "execute"))
        return type("Response", (), {"data": []})()


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append((name, "table"))
        return _RecordingQuery(self.calls, name)

    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        return _RecordingQuery(self.calls, "rpc-result")


def test_legacy_suggestion_import_uses_only_named_rpc(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "_sb", lambda: client)
    row = {"date": "2026-06-17", "ticker": "TEST", "action": "buy"}

    db.import_legacy_suggestion(row)

    assert ("rpc", "import_legacy_suggestion", {"p_row": row}) in client.calls
    assert not any(call[1] == "table" and call[0] == "suggestions" for call in client.calls)


@pytest.mark.db_integration
def test_holding_stop_roundtrip(isolated_db):
    client, cleanups = isolated_db
    ticker = f"T{uuid4().hex[:8].upper()}"
    cleanups.append(lambda: client.table("holdings").delete().eq("ticker", ticker).execute())
    db.init_schema()
    db.upsert_holding({"ticker": ticker, "shares": 1, "avg_cost": 100, "bucket": "growth",
        "opened_at": "2026-06-18", "notes": "t", "stop": 90, "target": 130, "high_water_price": 100})
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert float(h["stop"]) == 90 and float(h["target"]) == 130
    # plain re-upsert WITHOUT stop must not wipe it (COALESCE logic in upsert_holding)
    db.upsert_holding({"ticker": ticker, "shares": 2, "avg_cost": 100, "bucket": "growth",
        "opened_at": "2026-06-18", "notes": "t2"})
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert float(h["stop"]) == 90 and float(h["shares"]) == 2
    # ratchet the stop up
    db.update_holding_stop(ticker, stop=110, high_water_price=125)
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert float(h["stop"]) == 110 and float(h["high_water_price"]) == 125
    # stop-alert de-dup flag: starts unset, can be set and cleared (edge-triggered cooldown)
    db.set_stop_alert_active(ticker, True)
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert h["stop_alert_active"] is True
    db.set_stop_alert_active(ticker, False)
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert h["stop_alert_active"] is False
    # approaching-stop / approaching-target / target-hit flags: same roundtrip
    db.set_stop_near_alert_active(ticker, True)
    db.set_target_near_alert_active(ticker, True)
    db.set_target_alert_active(ticker, True)
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert h["stop_near_alert_active"] is True and h["target_near_alert_active"] is True and h["target_alert_active"] is True
    # owner hold-override: sets an expiry date and (optionally) a reason in notes
    db.set_hold_override(ticker, "2026-07-31", reason="owner: holding through July, ignore routine stop pushes")
    h = {r["ticker"]: r for r in db.get_holdings()}[ticker]
    assert h["hold_override_until"] == "2026-07-31"
    assert "holding through July" in h["notes"]


@pytest.mark.db_integration
def test_paper_watch_lifecycle(isolated_db):
    client, cleanups = isolated_db
    ticker = f"T{uuid4().hex[:8].upper()}"
    cleanups.append(lambda: client.table("paper_watches").delete().eq("ticker", ticker).execute())
    db.init_schema()
    pid = db.insert_paper_watch({"ticker": ticker, "created": "2026-06-18",
        "entry_ref_price": 100, "target_price": 130, "hypothetical_amount": 100,
        "thesis": "t", "horizon": "weeks", "agent_view_at_open": "Watch", "agent_score_at_open": 80})
    assert isinstance(pid, int)
    assert any(r["id"] == pid for r in db.get_active_paper_watches())
    active = [r for r in db.get_active_paper_watches() if r["id"] == pid][0]
    assert float(active["entry_ref_price"]) == 100
    assert active["agent_view_at_open"] == "Watch"
    db.close_paper_watch(pid, close_price=120, closed_date="2026-06-25")
    closed = client.table("paper_watches").select("*").eq("id", pid).execute().data[0]
    assert float(closed["close_price"]) == 120
    assert not any(r["id"] == pid for r in db.get_active_paper_watches())


@pytest.mark.db_integration
def test_lessons_roundtrip(isolated_db):
    client, cleanups = isolated_db
    db.init_schema()
    content = f"test regime line {uuid4()}"
    cleanups.append(lambda: client.table("lessons").delete().eq("content", content).execute())
    db.insert_lesson({"entry_date": str(date.today()), "category": "regime", "content": content})
    rows = db.get_lessons(limit=50)
    match = [r for r in rows if r["content"] == content]
    assert len(match) == 1 and match[0]["category"] == "regime"


@pytest.mark.db_integration
def test_analysis_run_lifecycle_roundtrip(isolated_db):
    client, cleanups = isolated_db
    run_ids = []
    cleanups.append(
        lambda: client.table("analysis_runs").delete().eq("id", run_ids[0]).execute() if run_ids else None,
    )
    run_id = db.start_analysis_run("test", started_at="2026-09-01T15:00:00+00:00")
    run_ids.append(run_id)
    running = client.table("analysis_runs").select("*").eq("id", run_id).execute().data[0]
    assert running["kind"] == "test"
    assert running["status"] == "running"
    assert running["finished_at"] is None

    db.finish_analysis_run(
        run_id,
        status="completed",
        data_as_of="2026-09-01T15:04:00+00:00",
        source_status={"quotes": "fresh", "news": "partial"},
        symbols=["AAPL", "MSFT"],
        write_counts={"suggestions": 1, "observations": 2},
        telegram_message_ids=[12345],
        summary="test run complete",
    )
    completed = client.table("analysis_runs").select("*").eq("id", run_id).execute().data[0]
    assert completed["status"] == "completed"
    assert completed["finished_at"] is not None
    assert completed["source_status"] == {"quotes": "fresh", "news": "partial"}
    assert completed["symbols"] == ["AAPL", "MSFT"]
    assert completed["write_counts"] == {"suggestions": 1, "observations": 2}
    assert completed["telegram_message_ids"] == [12345]


@pytest.mark.parametrize(("function_name", "table", "order_column", "limit"), [
    ("get_recent_transactions", "transactions", "ts", 17),
    ("get_recent_suggestions", "suggestions", "ts", 18),
    ("get_recent_grades", "suggestion_grades", "graded_at", 19),
    ("get_recent_snapshots", "daily_snapshots", "snap_date", 20),
])
def test_recent_queries_apply_requested_bound(monkeypatch, function_name, table, order_column, limit):
    client = _RecordingClient()
    monkeypatch.setattr(db, "_sb", lambda: client)

    assert getattr(db, function_name)(limit=limit) == []
    assert (table, "select", "*") in client.calls
    assert (table, "order", order_column, True) in client.calls
    assert (table, "limit", limit) in client.calls


@pytest.mark.parametrize("limit", [0, 501, True, 1.5])
def test_recent_queries_reject_invalid_bounds(limit):
    with pytest.raises(ValueError):
        db.get_recent_transactions(limit=limit)


def test_weekly_audit_queries_exclude_heavy_or_sensitive_columns(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "_sb", lambda: client)

    db.get_recent_decision_evaluations(limit=21)
    db.get_recent_market_publications(limit=22)

    selects = [call[2] for call in client.calls if call[1] == "select"]
    assert any("policy_status" in columns and "evidence" not in columns and "analyst" not in columns
               for columns in selects)
    assert any("telegram_message_ids" in columns and "rendered_body" not in columns and "error" not in columns
               for columns in selects)


def test_finish_analysis_run_bounds_free_text(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "_sb", lambda: client)

    db.finish_analysis_run("run-id", status="failed", summary="s" * 2001, error="e" * 1001)

    payload = next(call[2] for call in client.calls if call[1] == "update")
    assert len(payload["summary"]) == 2000
    assert len(payload["error"]) == 1000


def test_public_reconciliation_helpers_build_scoped_queries(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "_sb", lambda: client)

    assert db.get_latest_suggestion("AAPL") is None
    assert db.get_latest_buy_levels("AAPL") is None
    db.delete_holding("AAPL")

    assert ("suggestions", "select", "stop,target") in client.calls
    assert ("suggestions", "select", "*") in client.calls
    assert ("suggestions", "eq", "ticker", "AAPL") in client.calls
    assert ("suggestions", "eq", "action", "buy") in client.calls
    assert ("suggestions", "limit", 1) in client.calls
    assert ("holdings", "delete") in client.calls
    assert ("holdings", "eq", "ticker", "AAPL") in client.calls
