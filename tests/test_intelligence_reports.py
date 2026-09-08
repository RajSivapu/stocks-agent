import hashlib
import json
import sys
from dataclasses import replace
from datetime import date

import pytest

from lib.intelligence.reports import ReportInput, build_report, report_id_from_key, report_idempotency_key
from scripts.build_market_report import main as build_market_report_main

SOURCE_A = "00000000-0000-4000-8000-000000000001"
SOURCE_B = "00000000-0000-4000-8000-000000000002"
POLICY_A = "00000000-0000-4000-8000-000000000003"
POLICY_B = "00000000-0000-4000-8000-000000000004"


def report_input(**overrides):
    value = ReportInput(
        packet_id="00000000-0000-4000-8000-000000000020",
        packet_hash="a" * 64,
        market_date=date(2026, 9, 4),
        kind="weekly",
        title="Weekly owner research",
        summary="Evidence changed; review the cited research.",
        full_markdown="# Weekly owner research\n\nEvidence-backed detail.",
        source_ids=(SOURCE_B, SOURCE_A),
        policy_decision_ids=(POLICY_B, POLICY_A),
        comparison_ids=(),
        actionable_risk=False,
        material_thesis_change=False,
        intraday_triggered=True,
    )
    return replace(value, **overrides)


def test_report_hash_is_deterministic_and_sources_are_sorted():
    first = build_report(report_input(source_ids=(SOURCE_B, SOURCE_A)))
    second = build_report(report_input(source_ids=(SOURCE_A, SOURCE_B)))
    assert first.content_hash == second.content_hash
    assert first.source_ids == (SOURCE_A, SOURCE_B)
    assert first.policy_decision_ids == (POLICY_A, POLICY_B)
    assert first.comparison_ids == ()
    assert first.report_id == second.report_id


def test_report_idempotency_key_is_exact_lowercase_sha256():
    key = report_idempotency_key("weekly", date(2026, 9, 4), "a" * 64, "b" * 64)
    assert key == "56d5aaa198e7e33b9870c32253c62dc76ccdf82d7ffa2332c0e666cc3c2ae56a"
    assert len(key) == 64 and key == key.lower()
    assert report_id_from_key(key) == "56d5aaa1-98e7-533b-8870-c32253c62dc7"


def test_report_identity_binds_the_canonical_report_hash():
    first = build_report(report_input(summary="First canonical report."))
    second = build_report(report_input(summary="Second canonical report."))
    assert first.content_hash != second.content_hash
    assert first.idempotency_key != second.idempotency_key
    assert first.report_id != second.report_id


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
    with pytest.raises(ValueError, match="requires source"):
        build_report(report_input(source_ids=()))


def test_v2_research_report_stores_distinct_suitability_opposition_and_review_state():
    packet = {
        "contract_version": 2,
        "action_candidates": [],
        "coverage": {"complete_market_coverage": False, "mode": "bounded"},
        "research_candidates": [{
            "candidate_key": "sec:TEST", "ticker": "TEST",
            "research_state": "analysis_ready",
            "suitability": {
                "state": "unknown", "missing_reasons": ["valuation_missing"],
                "veto_reasons": [],
            },
            "adverse_paths": ["demand-downside"],
            "limitations": ["portfolio_overlap_missing"],
            "evidence": [{"item_id": SOURCE_A, "role": "supporting"}, {
                "item_id": SOURCE_B, "role": "opposing",
            }],
        }],
    }

    report = build_report(report_input(research_packet=packet))

    assert "RESEARCH ONLY" in report.full_markdown
    assert "Suitability: unknown (valuation_missing)" in report.full_markdown
    assert "Opposing evidence: 00000000-0000-4000-8000-000000000002" in report.full_markdown
    assert "Invalidation: demand-downside, portfolio_overlap_missing" in report.full_markdown
    assert 'Coverage: {"complete_market_coverage":false,"mode":"bounded"}' in report.full_markdown
    assert "Next review: unavailable" in report.full_markdown


def test_v2_research_only_report_requires_real_sources_but_no_fake_policy_decision():
    packet = {
        "contract_version": 2,
        "action_candidates": [],
        "coverage": {"complete_market_coverage": False, "mode": "bounded"},
        "research_candidates": [{
            "candidate_key": "unresolved:magnet-supplier",
            "ticker": None,
            "research_state": "unresolved",
            "suitability": {
                "state": "unknown", "missing_reasons": ["security_identity_unresolved"],
                "veto_reasons": [],
            },
            "adverse_paths": [],
            "limitations": ["security_identity_unresolved"],
            "evidence": [{"item_id": SOURCE_A, "role": "supporting"}],
        }],
    }

    report = build_report(report_input(
        source_ids=(SOURCE_A,), policy_decision_ids=(), research_packet=packet,
    ))

    assert report.policy_decision_ids == ()
    assert "unresolved:magnet-supplier — RESEARCH ONLY" in report.full_markdown
    assert "Suitability: unknown (security_identity_unresolved)" in report.full_markdown

    action_packet = {
        **packet,
        "action_candidates": [{"candidate_key": "unresolved:magnet-supplier"}],
    }
    with pytest.raises(ValueError, match="policy decision"):
        build_report(report_input(
            source_ids=(SOURCE_A,), policy_decision_ids=(), research_packet=action_packet,
        ))


def test_report_text_bounds_are_utf8_bytes_not_code_points():
    with pytest.raises(ValueError, match="bounded"):
        build_report(report_input(full_markdown="磁" * 4_667))


def test_report_cli_accepts_terminal_v2_research_receipt_without_fake_evaluation(
    tmp_path, monkeypatch, capsys,
):
    run_id = "00000000-0000-4000-8000-000000000021"
    packet = {
        "contract_version": 2,
        "run_id": run_id,
        "action_candidates": [],
        "coverage": {"complete_market_coverage": False, "mode": "bounded"},
        "research_candidates": [{
            "candidate_key": "unresolved:magnet-supplier",
            "ticker": None,
            "research_state": "unresolved",
            "suitability": {
                "state": "unknown", "missing_reasons": ["security_identity_unresolved"],
                "veto_reasons": [],
            },
            "adverse_paths": [], "limitations": ["security_identity_unresolved"],
            "evidence": [{"item_id": SOURCE_A, "role": "supporting"}],
        }],
    }
    packet_hash = hashlib.sha256(json.dumps(
        packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    payload = {
        "collection_receipt": {
            "completion_id": "00000000-0000-4000-8000-000000000022",
            "run_id": run_id, "packet_id": "00000000-0000-4000-8000-000000000020",
            "packet_hash": packet_hash, "packet": packet,
        },
        "evaluation_receipt": {
            "ok": True, "run_id": run_id, "policy_decision_ids": [],
            "source_ids": [SOURCE_A],
            "intelligence_packet": {
                "id": "00000000-0000-4000-8000-000000000020",
                "content_hash": packet_hash,
            },
        },
        "comparison_receipts": [],
        "content": {
            "market_date": "2026-09-04", "kind": "weekly",
            "title": "Weekly owner research", "summary": "Research requires more evidence.",
            "full_markdown": "# Weekly owner research\n\nNo action is eligible.",
        },
    }
    input_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", ["build_market_report.py", str(input_path)])

    assert build_market_report_main() == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["report"]["policy_decision_ids"] == []
    assert "unresolved:magnet-supplier — RESEARCH ONLY" in rendered["rendered_text"]
