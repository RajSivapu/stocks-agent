from dataclasses import replace
from datetime import date

import pytest

from lib.intelligence.reports import ReportInput, build_report, report_idempotency_key


def report_input(**overrides):
    value = ReportInput(
        packet_id="00000000-0000-4000-8000-000000000020",
        packet_hash="a" * 64,
        market_date=date(2026, 9, 4),
        kind="weekly",
        title="Weekly owner research",
        summary="Evidence changed; review the cited research.",
        full_markdown="# Weekly owner research\n\nEvidence-backed detail.",
        source_ids=("b", "a"),
        policy_decision_ids=("policy-b", "policy-a"),
        comparison_ids=("comparison-b", "comparison-a"),
        actionable_risk=False,
        material_thesis_change=False,
        intraday_triggered=True,
    )
    return replace(value, **overrides)


def test_report_hash_is_deterministic_and_sources_are_sorted():
    first = build_report(report_input(source_ids=("b", "a")))
    second = build_report(report_input(source_ids=("a", "b")))
    assert first.content_hash == second.content_hash
    assert first.source_ids == ("a", "b")
    assert first.policy_decision_ids == ("policy-a", "policy-b")
    assert first.comparison_ids == ("comparison-a", "comparison-b")
    assert first.report_id == second.report_id


def test_report_idempotency_key_is_exact_lowercase_sha256():
    key = report_idempotency_key("weekly", date(2026, 9, 4), "a" * 64)
    assert key == "8104d9d6f504c9d84a98d812dc17d5d8043d25897d037b9a2a0f72c739b92ab5"
    assert len(key) == 64 and key == key.lower()


def test_report_is_immutable_bounded_and_suggestion_only():
    report = build_report(report_input())
    with pytest.raises(AttributeError):
        report.summary = "changed"  # type: ignore[misc]
    assert "suggestion only" in report.full_markdown.lower()
    with pytest.raises(ValueError, match="bounded"):
        build_report(report_input(full_markdown="x" * 14_001))


def test_urgent_and_intraday_reports_fail_closed():
    with pytest.raises(ValueError, match="actionable"):
        build_report(report_input(kind="urgent", intraday_triggered=True))
    with pytest.raises(ValueError, match="trigger"):
        build_report(report_input(kind="intraday", intraday_triggered=False))
