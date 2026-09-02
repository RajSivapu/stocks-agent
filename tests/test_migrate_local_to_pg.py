import json

import pytest

from scripts import migrate_local_to_pg


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Buy", "buy"),
        ("Watch", "watch"),
        ("Trim", "reduce"),
        ("Exit", "sell"),
        ("Add/DCA", "add"),
        ("Watch/Add on pullback", "watch"),
    ],
)
def test_normalize_legacy_action(raw, expected):
    assert migrate_local_to_pg.normalize_action(raw) == expected


def test_migration_uses_legacy_rpc_and_normalizes_rows(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "suggestions-log.jsonl").write_text(
        json.dumps({
            "date": "2026-06-17",
            "ticker": "TEST",
            "action": "Trim",
            "confidence": "Medium-High",
            "score_growth": 9,
        }) + "\n"
    )
    calls = []

    class FakeDb:
        def init_schema(self): calls.append(("init",))
        def import_legacy_suggestion(self, row): calls.append(("suggestion", row))
        def upsert_holding(self, row): calls.append(("holding", row))
        def upsert_radar(self, row): calls.append(("radar", row))

    counts = migrate_local_to_pg.migrate(tmp_path, FakeDb())

    assert counts == {"suggestions": 1, "holdings": 0, "radar": 0}
    imported = calls[1][1]
    assert imported["action"] == "reduce"
    assert imported["confidence"] == "medium"
    assert "score_growth" not in imported


def test_migration_stops_immediately_when_legacy_rpc_rejects(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "suggestions-log.jsonl").write_text(
        json.dumps({"date": "2026-06-17", "ticker": "TEST", "action": "Buy"}) + "\n"
    )
    (tmp_path / "config" / "portfolio.json").write_text(
        json.dumps({"holdings": [{"ticker": "AAPL", "shares": 1, "avg_cost": 1}]})
    )
    calls = []

    class RejectingDb:
        def init_schema(self): calls.append("init")
        def import_legacy_suggestion(self, _row):
            calls.append("suggestion")
            raise RuntimeError("rejected")
        def upsert_holding(self, _row): calls.append("holding")
        def upsert_radar(self, _row): calls.append("radar")

    with pytest.raises(RuntimeError, match="rejected"):
        migrate_local_to_pg.migrate(tmp_path, RejectingDb())
    assert calls == ["init", "suggestion"]
