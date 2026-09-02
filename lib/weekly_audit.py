"""Build a compact, redacted, read-only packet for the weekly process audit."""
from datetime import date, datetime
from decimal import Decimal
import re


LIMITS = {
    "transactions": 50,
    "suggestions": 50,
    "grades": 100,
    "lessons": 40,
    "snapshots": 150,
    "evaluations": 50,
    "publications": 50,
}
_SENSITIVE_KEY_FRAGMENTS = ("token", "secret", "key", "authorization")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")
_SNAPSHOT_MACRO_TICKERS = frozenset({"^IRX", "^TNX", "^VIX"})
_EVALUATION_FIELDS = {
    "id", "request_id", "run_id", "candidate_id", "policy_version", "raw_action",
    "final_action", "policy_status", "reason_codes", "created_at",
}


def _safe(value):
    if isinstance(value, dict):
        return {
            str(key): _safe(child)
            for key, child in value.items()
            if not any(fragment in str(key).lower() for fragment in _SENSITIVE_KEY_FRAGMENTS)
        }
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _ticker(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if len(normalized) <= 12 and _TICKER.fullmatch(normalized) else None


def _ticker_rows(rows, name, invalid_counts, limit=None, allowed_tickers=frozenset()):
    clean = []
    invalid = 0
    for original in rows:
        if not isinstance(original, dict):
            invalid += 1
            continue
        ticker = _ticker(original.get("ticker"))
        if ticker is None and isinstance(original.get("ticker"), str):
            candidate = original["ticker"].strip().upper()
            ticker = candidate if candidate in allowed_tickers else None
        if not ticker:
            invalid += 1
            continue
        row = _safe(original)
        row["ticker"] = ticker
        clean.append(row)
        if limit is not None and len(clean) >= limit:
            break
    if invalid:
        invalid_counts[name] = invalid
    return clean


def _missing_stop(holding):
    try:
        return holding.get("stop") is None or float(holding["stop"]) <= 0
    except (TypeError, ValueError):
        return True


def _segment(suggestion, publications_by_run):
    if suggestion.get("decision_source") != "gateway":
        return "legacy"
    publication = publications_by_run.get(suggestion.get("run_id"))
    if not publication:
        return "unlinked"
    if publication.get("phase") == "on-demand" and publication.get("status") == "suppressed":
        return "session_only"
    if publication.get("status") == "delivered":
        return "scheduled_delivered"
    if publication.get("status") == "suppressed":
        return "suppressed_no_trigger"
    return "scheduled_not_delivered"


def _mean(values):
    if not values:
        return None
    value = sum(values, Decimal("0")) / Decimal(len(values))
    rendered = format(value.quantize(Decimal("0.0001")), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _summaries(suggestions, grades, evaluations):
    suggestion_by_id = {row.get("id"): row for row in suggestions}
    eligible_segments = {"scheduled_delivered", "session_only"}
    complete = {"5": 0, "21": 0, "63": 0}
    confidence = {}
    excess_by_action = {}
    gaps = {}
    for grade in grades:
        suggestion = suggestion_by_id.get(grade.get("suggestion_id"))
        if not suggestion or suggestion.get("decision_source") != "gateway" or \
                suggestion.get("delivery_segment") not in eligible_segments:
            continue
        status = grade.get("coverage_status")
        horizon = grade.get("horizon_days")
        if status == "complete" and str(horizon) in complete:
            complete[str(horizon)] += 1
            outcome = grade.get("direction_success")
            level = suggestion.get("confidence")
            if isinstance(outcome, bool) and isinstance(level, str):
                row = confidence.setdefault(level, {"successful": 0, "total": 0})
                row["total"] += 1
                row["successful"] += int(outcome)
            try:
                excess = Decimal(str(grade["excess_return_pct"]))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                pass
            else:
                action = grade.get("final_action")
                if excess.is_finite() and isinstance(action, str):
                    excess_by_action.setdefault(action, []).append(excess)
        elif status != "complete":
            key = (str(status), horizon)
            gaps[key] = gaps.get(key, 0) + 1

    policy = {"approved": 0, "downgraded": 0, "vetoed": 0}
    disagreements = 0
    reasons = {}
    eligible_evaluations = {row.get("evaluation_id") for row in suggestions
                            if row.get("decision_source") == "gateway"
                            and row.get("delivery_segment") in eligible_segments}
    for evaluation in evaluations:
        if evaluation.get("id") not in eligible_evaluations:
            continue
        status = evaluation.get("policy_status")
        if status in policy:
            policy[status] += 1
        if evaluation.get("raw_action") != evaluation.get("final_action"):
            disagreements += 1
        for code in evaluation.get("reason_codes") or []:
            if isinstance(code, str):
                reasons[code] = reasons.get(code, 0) + 1
    policy["raw_final_disagreements"] = disagreements
    policy["top_reason_codes"] = [
        {"code": code, "count": count}
        for code, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]
    return {
        "complete_by_horizon": complete,
        "direction_success_by_confidence": dict(sorted(confidence.items())),
        "mean_excess_return_by_action": {
            action: _mean(values) for action, values in sorted(excess_by_action.items())
        },
        "coverage_gaps": [
            {"coverage_status": status, "horizon_days": horizon, "count": count}
            for (status, horizon), count in sorted(gaps.items(), key=lambda item: (str(item[0][0]), item[0][1] or 0))
        ],
    }, policy


def build_packet(*, holdings, transactions, suggestions, grades, lessons, snapshots, generated_at,
                 evaluations=(), publications=()):
    """Return a bounded packet without changing any caller-owned input objects."""
    invalid_counts = {}
    clean_holdings = _ticker_rows(holdings, "holdings", invalid_counts)
    for holding in clean_holdings:
        holding["missing_stop"] = _missing_stop(holding)

    clean_transactions = _ticker_rows(
        transactions, "transactions", invalid_counts, LIMITS["transactions"]
    )
    clean_suggestions = _ticker_rows(
        suggestions, "suggestions", invalid_counts, LIMITS["suggestions"]
    )

    evaluation_ids = {row.get("evaluation_id") for row in clean_suggestions if row.get("evaluation_id")}
    clean_evaluations = [
        _safe({key: value for key, value in row.items() if key in _EVALUATION_FIELDS})
        for row in evaluations
        if isinstance(row, dict) and row.get("id") in evaluation_ids
    ][:LIMITS["evaluations"]]
    run_ids = {row.get("run_id") for row in clean_suggestions if row.get("run_id")}
    publication_fields = {
        "id", "idempotency_key", "run_id", "market_date", "phase", "kind", "template_version",
        "status", "telegram_message_ids", "attempt_count", "created_at", "updated_at",
    }
    clean_publications = [
        _safe({key: value for key, value in row.items() if key in publication_fields})
        for row in publications
        if isinstance(row, dict) and row.get("run_id") in run_ids
    ][:LIMITS["publications"]]
    publications_by_run = {row.get("run_id"): row for row in clean_publications}
    for suggestion in clean_suggestions:
        suggestion["delivery_segment"] = _segment(suggestion, publications_by_run)

    suggestion_ids = {row.get("id") for row in clean_suggestions if row.get("id") is not None}
    clean_grades = [
        _safe(row) for row in grades
        if isinstance(row, dict) and row.get("suggestion_id") in suggestion_ids
    ][:LIMITS["grades"]]
    clean_lessons = [_safe(row) for row in lessons if isinstance(row, dict)][:LIMITS["lessons"]]

    relevant_tickers = sorted({
        row["ticker"] for rows in (clean_holdings, clean_transactions, clean_suggestions) for row in rows
    })
    clean_snapshots = [
        row for row in _ticker_rows(
            snapshots, "snapshots", invalid_counts,
            allowed_tickers=_SNAPSHOT_MACRO_TICKERS,
        )
        if row["ticker"] in relevant_tickers
    ][:LIMITS["snapshots"]]

    missing_stops = sorted(row["ticker"] for row in clean_holdings if row["missing_stop"])
    unbucketed = sorted(row["ticker"] for row in clean_holdings if not row.get("bucket"))
    generated = generated_at.isoformat() if isinstance(generated_at, (datetime, date)) else str(generated_at)
    outcome_summary, policy_summary = _summaries(
        clean_suggestions, clean_grades, clean_evaluations
    )

    return {
        "schema_version": 2,
        "generated_at": generated,
        "scope": {"tickers": relevant_tickers, "limits": dict(LIMITS)},
        "quality_flags": {
            "missing_stop_tickers": missing_stops,
            "unbucketed_holding_tickers": unbucketed,
            "invalid_ticker_rows": invalid_counts,
        },
        "holdings": clean_holdings,
        "transactions": clean_transactions,
        "suggestions": clean_suggestions,
        "grades": clean_grades,
        "evaluations": clean_evaluations,
        "publications": clean_publications,
        "outcome_summary": outcome_summary,
        "policy_summary": policy_summary,
        "lessons": clean_lessons,
        "snapshots": clean_snapshots,
    }
