from copy import deepcopy

from lib.weekly_audit import build_packet
import scripts.weekly_audit_packet as weekly_packet
from scripts.weekly_audit_packet import read_weekly_audit_inputs, weekly_audit_database_url


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
        {"id": 43, "ticker": "^VIX", "close": 15},
        {"id": 44, "ticker": "^TNX", "close": 4.2},
        {"id": 45, "ticker": "^IRX", "close": 3.8},
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
        "transactions", "suggestions", "grades", "evaluations", "publications",
        "outcome_summary", "policy_summary", "lessons", "snapshots",
    ]
    assert packet["schema_version"] == 2
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
        "evaluations": 50,
        "publications": 50,
    }


def test_packet_links_gateway_policy_and_outcomes_without_publication_bodies():
    suggestions = [
        {"id": 10, "ticker": "AAPL", "action": "watch", "confidence": "medium",
         "decision_source": "gateway", "evaluation_id": "eval-1", "run_id": "run-1"},
        {"id": 11, "ticker": "MSFT", "action": "buy", "confidence": "high",
         "decision_source": "legacy", "evaluation_id": "eval-legacy", "run_id": None},
    ]
    grades = [
        {"id": 20, "suggestion_id": 10, "horizon_days": 5, "coverage_status": "complete",
         "direction_success": True, "excess_return_pct": "2.5", "final_action": "watch"},
        {"id": 21, "suggestion_id": 10, "horizon_days": 21, "coverage_status": "incomplete",
         "direction_success": None, "excess_return_pct": None, "final_action": "watch"},
        {"id": 22, "suggestion_id": 11, "horizon_days": 5, "coverage_status": "complete",
         "direction_success": True, "excess_return_pct": "99", "final_action": "buy"},
    ]
    evaluations = [
        {"id": "eval-1", "run_id": "run-1", "raw_action": "buy", "final_action": "watch",
         "policy_status": "downgraded", "reason_codes": ["POSITION_LIMIT", "STALE_QUOTE"]},
        {"id": "eval-unlinked", "run_id": "run-x", "raw_action": "buy", "final_action": "buy",
         "policy_status": "approved", "reason_codes": []},
    ]
    publications = [
        {"id": "pub-1", "run_id": "run-1", "phase": "on-demand", "status": "suppressed",
         "kind": "brief", "rendered_body": "private rendered body", "error": "raw failure"},
        {"id": "pub-x", "run_id": "run-x", "phase": "pre-market", "status": "delivered"},
    ]

    packet = build_packet(
        holdings=[], transactions=[], suggestions=suggestions, grades=grades,
        evaluations=evaluations, publications=publications, lessons=[], snapshots=[],
        generated_at="2026-09-01T21:30:00+00:00",
    )

    assert [row["id"] for row in packet["evaluations"]] == ["eval-1"]
    assert [row["id"] for row in packet["publications"]] == ["pub-1"]
    assert "rendered_body" not in packet["publications"][0]
    assert "error" not in packet["publications"][0]
    assert packet["suggestions"][0]["delivery_segment"] == "session_only"
    assert packet["outcome_summary"] == {
        "complete_by_horizon": {"5": 1, "21": 0, "63": 0},
        "direction_success_by_confidence": {"medium": {"successful": 1, "total": 1}},
        "mean_excess_return_by_action": {"watch": "2.5"},
        "coverage_gaps": [{"coverage_status": "incomplete", "horizon_days": 21, "count": 1}],
        "methodology": {
            "return_window": "decision_session_close",
            "entry_hit": "market_touch_not_owner_fill",
            "actual_fill_status": "unavailable_without_explicit_trade_link",
        },
    }
    assert packet["policy_summary"] == {
        "approved": 0,
        "downgraded": 1,
        "vetoed": 0,
        "raw_final_disagreements": 1,
        "top_reason_codes": [
            {"code": "POSITION_LIMIT", "count": 1},
            {"code": "STALE_QUOTE", "count": 1},
        ],
    }


def test_packet_bounds_linked_evaluations_and_publications():
    suggestions = [
        {"id": index, "ticker": "AAPL", "action": "watch", "confidence": "low",
         "decision_source": "gateway", "evaluation_id": f"eval-{index}", "run_id": f"run-{index}"}
        for index in range(60)
    ]
    packet = build_packet(
        holdings=[], transactions=[], suggestions=suggestions, grades=[], lessons=[], snapshots=[],
        evaluations=[{"id": f"eval-{index}", "run_id": f"run-{index}"} for index in range(60)],
        publications=[{"id": f"pub-{index}", "run_id": f"run-{index}"} for index in range(60)],
        generated_at="2026-09-01T21:30:00+00:00",
    )
    assert len(packet["evaluations"]) == 50
    assert len(packet["publications"]) == 50


def test_veto_only_week_retains_the_evaluation_and_policy_summary():
    packet = build_packet(
        holdings=[], transactions=[], suggestions=[], grades=[], lessons=[], snapshots=[],
        evaluations=[{
            "id": "eval-veto", "run_id": "run-veto", "raw_action": "buy",
            "final_action": None, "policy_status": "vetoed",
            "reason_codes": ["PORTFOLIO_VALUE_INCOMPLETE"],
        }],
        publications=[{
            "id": "pub-veto", "run_id": "run-veto", "phase": "on-demand",
            "status": "suppressed", "kind": "brief",
        }],
        generated_at="2026-09-04T21:30:00+00:00",
    )

    assert [row["id"] for row in packet["evaluations"]] == ["eval-veto"]
    assert [row["id"] for row in packet["publications"]] == ["pub-veto"]
    assert packet["policy_summary"]["vetoed"] == 1
    assert packet["policy_summary"]["raw_final_disagreements"] == 1
    assert packet["policy_summary"]["top_reason_codes"] == [
        {"code": "PORTFOLIO_VALUE_INCOMPLETE", "count": 1}
    ]
    assert packet["outcome_summary"]["methodology"] == {
        "return_window": "decision_session_close",
        "entry_hit": "market_touch_not_owner_fill",
        "actual_fill_status": "unavailable_without_explicit_trade_link",
    }


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ReadOnlyConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        normalized = " ".join(str(statement).split())
        self.statements.append(normalized)
        if normalized == "SET TRANSACTION READ ONLY":
            return _Result([])
        return _Result([])


def test_weekly_audit_reader_uses_one_read_only_transaction_and_selects_only():
    connection = _ReadOnlyConnection()
    inputs = read_weekly_audit_inputs(connection)

    assert set(inputs) == {
        "holdings", "transactions", "suggestions", "grades", "lessons",
        "snapshots", "evaluations", "publications",
    }
    assert connection.statements[0] == "SET TRANSACTION READ ONLY"
    assert len(connection.statements) == 9
    assert all(statement.startswith("SELECT ") for statement in connection.statements[1:])


def test_weekly_audit_database_url_accepts_only_the_scoped_runtime_role():
    project_ref = "abcdefghijklmnopqrst"
    scoped = (
        "postgresql://stock_agent_dashboard_runtime.abcdefghijklmnopqrst:password-longer-than-24"
        "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    assert weekly_audit_database_url({"DASHBOARD_DATABASE_URL": scoped}, project_ref) == scoped

    for unsafe in (
        scoped.replace("stock_agent_dashboard_runtime", "postgres"),
        scoped.replace("abcdefghijklmnopqrst:password", "zzzzzzzzzzzzzzzzzzzz:password"),
        scoped.replace("pooler.supabase.com", "example.com"),
    ):
        try:
            weekly_audit_database_url({"DASHBOARD_DATABASE_URL": unsafe}, project_ref)
        except ValueError:
            pass
        else:
            raise AssertionError("weekly audit accepted a non-scoped database role")


def test_weekly_audit_connection_verifies_the_server_certificate_and_hostname(monkeypatch):
    project_ref = "abcdefghijklmnopqrst"
    scoped = (
        "postgresql://stock_agent_dashboard_runtime.abcdefghijklmnopqrst:password-longer-than-24"
        "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    class Connection(_ReadOnlyConnection):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    connection = Connection()
    observed = {}

    def connect(conninfo, **kwargs):
        observed.update(conninfo=conninfo, **kwargs)
        return connection

    monkeypatch.setenv("SUPABASE_PROJECT_REF", project_ref)
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", scoped)
    monkeypatch.setattr(weekly_packet.psycopg, "connect", connect)

    assert weekly_packet.main() == 0
    assert observed["conninfo"] == scoped
    assert observed["sslmode"] == "verify-full"
