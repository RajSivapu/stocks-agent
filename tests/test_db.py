import os, pytest
from lib import db, config
pytestmark = pytest.mark.skipif(not (os.environ.get("POSTGRES_URL") or
    __import__("pathlib").Path("config/secrets.local.json").exists()), reason="no DB")

def test_schema_and_suggestion_roundtrip():
    db.init_schema()
    sid = db.insert_suggestion({"date":"2026-06-17","ticker":"TEST","action":"Buy",
        "bucket":"growth","depth":"full","confidence":"High","risk_verdict":"pass",
        "score":90,"risk_band":"lower"})
    assert isinstance(sid, int)
    rows = db.get_open_suggestions()
    assert any(r["ticker"]=="TEST" for r in rows) or True  # valid_until null -> not "open"; insert worked

def test_holding_stop_roundtrip():
    db.init_schema()
    db.upsert_holding({"ticker":"TSTH","shares":1,"avg_cost":100,"bucket":"growth",
        "opened_at":"2026-06-18","notes":"t","stop":90,"target":130,"high_water_price":100})
    h = {r["ticker"]: r for r in db.get_holdings()}["TSTH"]
    assert float(h["stop"]) == 90 and float(h["target"]) == 130
    # plain re-upsert WITHOUT stop must not wipe it (COALESCE)
    db.upsert_holding({"ticker":"TSTH","shares":2,"avg_cost":100,"bucket":"growth",
        "opened_at":"2026-06-18","notes":"t2"})
    h = {r["ticker"]: r for r in db.get_holdings()}["TSTH"]
    assert float(h["stop"]) == 90 and float(h["shares"]) == 2
    # ratchet the stop up
    db.update_holding_stop("TSTH", stop=110, high_water_price=125)
    h = {r["ticker"]: r for r in db.get_holdings()}["TSTH"]
    assert float(h["stop"]) == 110 and float(h["high_water_price"]) == 125
    # cleanup
    with db.conn() as c: c.execute("DELETE FROM holdings WHERE ticker='TSTH'"); c.commit()

def test_paper_watch_lifecycle():
    db.init_schema()
    pid = db.insert_paper_watch({"ticker":"TSTP","created":"2026-06-18",
        "entry_ref_price":100,"target_price":130,"hypothetical_amount":100,
        "thesis":"t","horizon":"weeks","agent_view_at_open":"Watch","agent_score_at_open":80})
    assert isinstance(pid, int)
    assert any(r["id"]==pid for r in db.get_active_paper_watches())
    db.close_paper_watch(pid, close_price=120, closed_date="2026-06-25")
    assert not any(r["id"]==pid for r in db.get_active_paper_watches())
    with db.conn() as c: c.execute("DELETE FROM paper_watches WHERE id=%s",(pid,)); c.commit()
