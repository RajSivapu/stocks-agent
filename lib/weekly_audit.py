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
}
_SENSITIVE_KEY_FRAGMENTS = ("token", "secret", "key", "authorization")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")


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


def _ticker_rows(rows, name, invalid_counts, limit=None):
    clean = []
    invalid = 0
    for original in rows:
        if not isinstance(original, dict):
            invalid += 1
            continue
        ticker = _ticker(original.get("ticker"))
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


def build_packet(*, holdings, transactions, suggestions, grades, lessons, snapshots, generated_at):
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
        row for row in _ticker_rows(snapshots, "snapshots", invalid_counts)
        if row["ticker"] in relevant_tickers
    ][:LIMITS["snapshots"]]

    missing_stops = sorted(row["ticker"] for row in clean_holdings if row["missing_stop"])
    unbucketed = sorted(row["ticker"] for row in clean_holdings if not row.get("bucket"))
    generated = generated_at.isoformat() if isinstance(generated_at, (datetime, date)) else str(generated_at)

    return {
        "schema_version": 1,
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
        "lessons": clean_lessons,
        "snapshots": clean_snapshots,
    }
