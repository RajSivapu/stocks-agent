from copy import deepcopy

from lib.weekly_audit import build_packet


def _contains_sensitive_key(value):
    if isinstance(value, dict):
        return any(
            any(fragment in str(key).lower() for fragment in ("token", "secret", "key", "authorization"))
            or _contains_sensitive_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def test_build_packet_is_stable_relevant_redacted_and_non_mutating():
    holdings = [
        {"ticker": "AAPL", "shares": 2, "avg_cost": 100, "bucket": "growth", "stop": None,
         "api_key": "must-not-leak", "nested": {"authorization": "must-not-leak"}},
        {"ticker": "MSFT", "shares": 1, "avg_cost": 200, "bucket": "core", "stop": 180},
    ]
    transactions = [
        {"id": 1, "ticker": "NVDA", "side": "sell", "qty": 1, "price": 210},
    ]
    suggestions = [
        {"id": 10, "ticker": "AAPL", "action": "Watch", "reason": "test", "secret_note": "no"},
        {"id": 99, "ticker": "AAPL;DROP", "action": "Buy"},
    ]
    grades = [
        {"id": 20, "suggestion_id": 10, "result": "partial"},
        {"id": 21, "suggestion_id": 999, "result": "right"},
    ]
    lessons = [{"id": 30, "category": "regime", "content": "calm", "token_count": 12}]
    snapshots = [
        {"id": 40, "ticker": "AAPL", "close": 110},
        {"id": 41, "ticker": "NVDA", "close": 205},
        {"id": 42, "ticker": "TSLA", "close": 300},
    ]
    original = deepcopy((holdings, transactions, suggestions, grades, lessons, snapshots))

    packet = build_packet(
        holdings=holdings,
        transactions=transactions,
        suggestions=suggestions,
        grades=grades,
        lessons=lessons,
        snapshots=snapshots,
        generated_at="2026-09-01T21:30:00+00:00",
    )

    assert list(packet) == [
        "schema_version", "generated_at", "scope", "quality_flags", "holdings",
        "transactions", "suggestions", "grades", "lessons", "snapshots",
    ]
    assert packet["scope"]["tickers"] == ["AAPL", "MSFT", "NVDA"]
    assert [row["ticker"] for row in packet["snapshots"]] == ["AAPL", "NVDA"]
    assert [row["suggestion_id"] for row in packet["grades"]] == [10]
    assert packet["holdings"][0]["missing_stop"] is True
    assert packet["holdings"][1]["missing_stop"] is False
    assert packet["quality_flags"]["missing_stop_tickers"] == ["AAPL"]
    assert packet["quality_flags"]["invalid_ticker_rows"] == {"suggestions": 1}
    assert not _contains_sensitive_key(packet)
    assert (holdings, transactions, suggestions, grades, lessons, snapshots) == original


def test_build_packet_enforces_all_collection_bounds():
    suggestions = [{"id": index, "ticker": "AAPL", "action": "Watch"} for index in range(60)]
    packet = build_packet(
        holdings=[{"ticker": "AAPL", "shares": 1, "avg_cost": 100, "stop": 90}],
        transactions=[{"id": index, "ticker": "AAPL"} for index in range(60)],
        suggestions=suggestions,
        grades=[{"id": index, "suggestion_id": index % 50} for index in range(110)],
        lessons=[{"id": index, "category": "lesson", "content": str(index)} for index in range(45)],
        snapshots=[{"id": index, "ticker": "AAPL", "close": 100 + index} for index in range(160)],
        generated_at="2026-09-01T21:30:00+00:00",
    )

    assert len(packet["transactions"]) == 50
    assert len(packet["suggestions"]) == 50
    assert len(packet["grades"]) == 100
    assert len(packet["lessons"]) == 40
    assert len(packet["snapshots"]) == 150
    assert packet["scope"]["limits"] == {
        "transactions": 50,
        "suggestions": 50,
        "grades": 100,
        "lessons": 40,
        "snapshots": 150,
    }
