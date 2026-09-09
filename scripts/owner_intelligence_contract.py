"""Exact Python mirror of the owner intelligence V2 dashboard contract."""

from __future__ import annotations

from calendar import monthrange
import math
import re
from typing import Mapping


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
BOUNDED_TIMESTAMP_PATTERN = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
JS_TRIM_PATTERN = re.compile(
    r"^[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+"
    r"|[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$"
)
TOP_LEVEL_KEYS = {
    "intelligence_version", "run_id", "data_as_of", "themes", "companies", "evidence",
    "source_health", "coverage", "reference", "scope", "backlog", "omissions", "boundaries",
}


def _object(value: object, keys: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} must be an exact object")
    return value


def _array(value: object, maximum: int, name: str) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded array")
    return value


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _string(value: object, maximum: int, name: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or _utf16_length(value) > maximum
        or (not empty and not JS_TRIM_PATTERN.sub("", value))
    ):
        raise ValueError(f"{name} must be a bounded string")
    return value


def _strings(value: object, maximum: int, item_maximum: int, name: str) -> list[str]:
    result = [_string(item, item_maximum, name) for item in _array(value, maximum, name)]
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicates")
    return result


def _integer(value: object, minimum: int, maximum: int, name: str) -> int | float:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    finite = isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))
    integral = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if (
        not numeric
        or not finite
        or not integral
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be a bounded integer")
    return value


def _boolean(value: object, expected: bool, name: str) -> bool:
    if type(value) is not bool or value is not expected:
        raise ValueError(f"{name} must be {str(expected).lower()}")
    return value


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or UUID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a UUID")
    return value


def _timestamp(value: object, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _utf16_length(value) > 40:
        raise ValueError(f"{name} must be an ISO timestamp")
    match = BOUNDED_TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"{name} must be an ISO timestamp")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    offset = match.group(8)
    if (
        year == 0
        or not 1 <= month <= 12
        or not 1 <= day <= monthrange(year, month)[1]
        or hour > 23
        or minute > 59
        or second > 59
        or (
            offset != "Z"
            and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59)
        )
    ):
        raise ValueError(f"{name} must be an ISO timestamp")
    return value


def _enum(value: object, allowed: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} is invalid")
    return value


def validate_owner_intelligence_v2(value: object) -> Mapping[str, object]:
    """Reject anything the deployed TypeScript parser would reject."""
    row = _object(value, TOP_LEVEL_KEYS, "intelligence")
    if row["intelligence_version"] != 2:
        raise ValueError("unsupported intelligence version")
    if row["run_id"] is not None:
        _uuid(row["run_id"], "intelligence.run_id")
    _timestamp(row["data_as_of"], "intelligence.data_as_of", nullable=True)

    for index, item in enumerate(_array(row["themes"], 25, "intelligence.themes")):
        name = f"intelligence.themes[{index}]"
        theme = _object(item, {
            "theme_id", "episode_id", "revision_id", "revision", "mechanism", "subject",
            "jurisdiction", "state", "first_seen", "last_seen", "next_review_at", "expires_at",
            "adverse_evidence_count", "missing_questions", "invalidation_conditions",
        }, name)
        _string(theme["theme_id"], 80, "theme_id")
        _uuid(theme["episode_id"], "episode_id")
        _uuid(theme["revision_id"], "revision_id")
        _integer(theme["revision"], 1, 10_000, "revision")
        _string(theme["mechanism"], 240, "mechanism")
        _string(theme["subject"], 240, "subject")
        _string(theme["jurisdiction"], 80, "jurisdiction")
        _enum(theme["state"], {"active", "expired", "closed"}, "state")
        for field in ("first_seen", "last_seen", "next_review_at", "expires_at"):
            _timestamp(theme[field], field)
        _integer(theme["adverse_evidence_count"], 0, 64, "adverse_evidence_count")
        _strings(theme["missing_questions"], 24, 500, "missing_questions")
        _strings(theme["invalidation_conditions"], 24, 500, "invalidation_conditions")

    companies = []
    for index, item in enumerate(_array(row["companies"], 12, "intelligence.companies")):
        name = f"intelligence.companies[{index}]"
        company = _object(item, {
            "company_id", "name", "ticker", "outside_watchlist", "relationship_paths",
            "evidence_ids", "missing_inputs",
        }, name)
        _string(company["company_id"], 128, "company_id")
        _string(company["name"], 240, "name")
        if company["ticker"] is not None:
            _string(company["ticker"], 24, "ticker")
        if company["outside_watchlist"] is not None and not isinstance(company["outside_watchlist"], bool):
            raise ValueError("invalid outside_watchlist")
        _strings(company["relationship_paths"], 4, 500, "relationship_paths")
        evidence_ids = _strings(company["evidence_ids"], 8, 64, "evidence_ids")
        _strings(company["missing_inputs"], 16, 500, "missing_inputs")
        companies.append(evidence_ids)

    evidence_ids = set()
    for index, item in enumerate(_array(row["evidence"], 96, "intelligence.evidence")):
        name = f"intelligence.evidence[{index}]"
        evidence = _object(item, {"evidence_id", "label", "url", "passage", "role", "retrieved_at"}, name)
        evidence_ids.add(_uuid(evidence["evidence_id"], "evidence_id"))
        _string(evidence["label"], 256, "label")
        if evidence["url"] is not None:
            _string(evidence["url"], 2048, "url")
        _string(evidence["passage"], 2000, "passage", empty=True)
        _enum(evidence["role"], {"supporting", "opposing"}, "role")
        _timestamp(evidence["retrieved_at"], "retrieved_at")
    if any(any(evidence_id not in evidence_ids for evidence_id in company) for company in companies):
        raise ValueError("intelligence contains dangling evidence reference")

    for index, item in enumerate(_array(row["source_health"], 50, "intelligence.source_health")):
        name = f"intelligence.source_health[{index}]"
        source = _object(item, {"provider", "status", "retrieved_at", "accepted_count", "dropped_count"}, name)
        _string(source["provider"], 80, "provider")
        _timestamp(source["retrieved_at"], "retrieved_at", nullable=True)
        _integer(source["accepted_count"], 0, 10_000, "accepted_count")
        _integer(0 if source["dropped_count"] is None else source["dropped_count"], 0, 10_000, "dropped_count")

    coverage = _object(row["coverage"], {"mode", "complete_market_coverage"}, "intelligence.coverage")
    if coverage["mode"] != "bounded":
        raise ValueError("intelligence coverage boundary mismatch")
    _boolean(coverage["complete_market_coverage"], False, "coverage.complete_market_coverage")

    reference = row["reference"]
    if not isinstance(reference, dict):
        raise ValueError("intelligence.reference must be an object")
    allowed_reference = {"state", "manifest_id", "as_of", "age_seconds"}
    if "state" not in reference or not set(reference).issubset(allowed_reference):
        raise ValueError("intelligence.reference has unexpected fields")
    _enum(reference["state"], {"healthy", "reference_stale", "reference_unavailable", "unavailable"}, "reference.state")
    if "manifest_id" in reference:
        _uuid(reference["manifest_id"], "reference.manifest_id")
    if "as_of" in reference:
        _timestamp(reference["as_of"], "reference.as_of")
    if "age_seconds" in reference:
        _integer(reference["age_seconds"], 0, 9_007_199_254_740_991, "reference.age_seconds")

    scope = _object(row["scope"], {"research_only", "market_wide"}, "intelligence.scope")
    _boolean(scope["research_only"], True, "scope.research_only")
    _boolean(scope["market_wide"], True, "scope.market_wide")
    backlog = _object(row["backlog"], {"available", "returned", "deferred", "byte_truncated"}, "intelligence.backlog")
    _integer(backlog["available"], 0, 1_000_000, "backlog.available")
    _integer(backlog["returned"], 0, 25, "backlog.returned")
    _integer(backlog["deferred"], 0, 1_000_000, "backlog.deferred")
    if not isinstance(backlog["byte_truncated"], bool):
        raise ValueError("backlog.byte_truncated must be boolean")
    _strings(row["omissions"], 50, 1000, "omissions")
    boundaries = _object(
        row["boundaries"],
        {"research_only", "execution_disabled", "valuation_unavailable"},
        "intelligence.boundaries",
    )
    _boolean(boundaries["research_only"], True, "boundaries.research_only")
    _boolean(boundaries["execution_disabled"], True, "boundaries.execution_disabled")
    _boolean(boundaries["valuation_unavailable"], True, "boundaries.valuation_unavailable")
    return row
