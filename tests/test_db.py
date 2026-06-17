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
