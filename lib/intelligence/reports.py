"""Deterministic immutable owner research reports."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal, Mapping


ReportKind = Literal["morning", "urgent", "weekly", "monthly", "theme", "on-demand", "intraday"]
_HASH = frozenset("0123456789abcdef")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_hash(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in _HASH for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hash")


def _sorted_unique(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    canonical: list[str] = []
    for value in values:
        try:
            parsed = str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"{name} must contain canonical UUIDs") from None
        if parsed != value:
            raise ValueError(f"{name} must contain canonical UUIDs")
        canonical.append(parsed)
    return tuple(sorted(set(canonical)))


@dataclass(frozen=True, slots=True)
class ReportInput:
    packet_id: str
    packet_hash: str
    market_date: date
    kind: ReportKind
    title: str
    summary: str
    full_markdown: str
    source_ids: tuple[str, ...]
    policy_decision_ids: tuple[str, ...]
    comparison_ids: tuple[str, ...]
    actionable_risk: bool
    material_thesis_change: bool
    intraday_triggered: bool
    research_packet: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class MarketReport:
    report_id: str
    idempotency_key: str
    packet_id: str
    packet_hash: str
    market_date: date
    kind: ReportKind
    title: str
    summary: str
    full_markdown: str
    source_ids: tuple[str, ...]
    policy_decision_ids: tuple[str, ...]
    comparison_ids: tuple[str, ...]
    actionable_risk: bool
    material_thesis_change: bool
    intraday_triggered: bool
    content_hash: str
    rendered_hash: str

    def report_body(self) -> dict[str, object]:
        return {
            "actionable_risk": self.actionable_risk,
            "comparison_ids": list(self.comparison_ids),
            "full_markdown": self.full_markdown,
            "intraday_triggered": self.intraday_triggered,
            "material_thesis_change": self.material_thesis_change,
            "policy_decision_ids": list(self.policy_decision_ids),
            "source_ids": list(self.source_ids),
            "suggestion_only": True,
            "summary": self.summary,
            "title": self.title,
        }

    def to_gateway_payload(self) -> dict[str, object]:
        return {
            "id": self.report_id,
            "idempotency_key": self.idempotency_key,
            "packet_id": self.packet_id,
            "market_date": self.market_date.isoformat(),
            "kind": self.kind,
            "report": self.report_body(),
            "report_hash": self.content_hash,
            "rendered_text": self.full_markdown,
            "rendered_hash": self.rendered_hash,
        }


def report_idempotency_key(
    kind: str, market_date: date, packet_hash: str, report_hash: str
) -> str:
    _validate_hash(packet_hash, "packet_hash")
    _validate_hash(report_hash, "report_hash")
    return _sha256(f"v2:{kind}:{market_date}:{packet_hash}:{report_hash}".encode())


def report_id_from_key(key: str) -> str:
    _validate_hash(key, "idempotency_key")
    value = list(key[:32])
    value[12] = "5"
    value[16] = "8"
    return str(uuid.UUID("".join(value)))


def _research_catalog_markdown(packet: Mapping[str, object] | None) -> str:
    if packet is None:
        return ""
    if packet.get("contract_version") != 2:
        return ""
    candidates = packet.get("research_candidates")
    actions = packet.get("action_candidates")
    coverage = packet.get("coverage")
    if (
        not isinstance(candidates, list) or len(candidates) > 12
        or not isinstance(actions, list) or len(actions) > 12
        or not isinstance(coverage, Mapping)
    ):
        raise ValueError("v2 research report packet is invalid")
    action_keys = {
        value.get("candidate_key") for value in actions if isinstance(value, Mapping)
    }
    coverage_text = json.dumps(
        coverage, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    if len(coverage_text.encode()) > 32_768:
        raise ValueError("v2 research report coverage is too large")
    next_review = coverage.get("next_review_at")
    if not isinstance(next_review, str) or not next_review.strip():
        next_review = "unavailable"
    lines = ["## Research catalog", "", f"Coverage: {coverage_text}"]
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("v2 research report candidate is invalid")
        candidate_key = candidate.get("candidate_key")
        ticker = candidate.get("ticker")
        state = candidate.get("research_state")
        suitability = candidate.get("suitability")
        evidence = candidate.get("evidence")
        if (
            not isinstance(candidate_key, str) or not candidate_key
            or ticker is not None and not isinstance(ticker, str)
            or not isinstance(state, str) or not state
            or not isinstance(suitability, Mapping)
            or not isinstance(evidence, list) or len(evidence) > 8
        ):
            raise ValueError("v2 research report candidate is invalid")
        suitability_state = suitability.get("state")
        missing = suitability.get("missing_reasons")
        vetoes = suitability.get("veto_reasons")
        limitations = candidate.get("limitations")
        adverse = candidate.get("adverse_paths")
        if (
            suitability_state not in {"unknown", "eligible", "vetoed"}
            or not all(isinstance(values, list) for values in (missing, vetoes, limitations, adverse))
        ):
            raise ValueError("v2 research report suitability is invalid")
        opposing = []
        for reference in evidence:
            if not isinstance(reference, Mapping):
                raise ValueError("v2 research report evidence is invalid")
            if reference.get("role") == "opposing":
                item_id = reference.get("item_id")
                if not isinstance(item_id, str):
                    raise ValueError("v2 research report evidence is invalid")
                opposing.append(item_id)
        label = "ACTION LANE" if candidate_key in action_keys else "RESEARCH ONLY"
        identity = ticker or candidate_key
        missing_text = ", ".join(str(value) for value in missing) or "none"
        opposing_text = ", ".join(opposing) or "none retained"
        invalidation = sorted({
            str(value) for values in (adverse, limitations, vetoes) for value in values
        })
        lines.extend([
            "", f"### {identity} — {label}", f"Research state: {state}",
            f"Suitability: {suitability_state} ({missing_text})",
            f"Opposing evidence: {opposing_text}",
            f"Invalidation: {', '.join(invalidation) or 'none recorded'}",
            f"Next review: {next_review}",
        ])
    rendered = "\n".join(lines)
    if len(rendered.encode()) > 14_000:
        raise ValueError("v2 research report is too large")
    return rendered


def _is_v2_research_only(packet: Mapping[str, object] | None) -> bool:
    if packet is None or packet.get("contract_version") != 2:
        return False
    candidates = packet.get("research_candidates")
    actions = packet.get("action_candidates")
    return isinstance(candidates, list) and bool(candidates) and actions == []


def build_report(value: ReportInput) -> MarketReport:
    if value.kind not in {"morning", "urgent", "weekly", "monthly", "theme", "on-demand", "intraday"}:
        raise ValueError("unsupported report kind")
    _validate_hash(value.packet_hash, "packet_hash")
    try:
        packet_id = str(uuid.UUID(value.packet_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("packet_id must be a canonical UUID") from None
    if packet_id != value.packet_id:
        raise ValueError("packet_id must be a canonical UUID")
    if (
        not value.title.strip()
        or len(value.title.encode()) > 200
        or len(value.summary.encode()) > 1_000
    ):
        raise ValueError("report text must be bounded")
    if not value.full_markdown.strip() or len(value.full_markdown.encode()) > 14_000:
        raise ValueError("report text must be bounded")
    if value.kind == "urgent" and not (value.actionable_risk or value.material_thesis_change):
        raise ValueError("urgent report requires actionable risk or material thesis change")
    if value.kind == "intraday" and not value.intraday_triggered:
        raise ValueError("intraday report requires a trigger")

    source_ids = _sorted_unique(value.source_ids, "source_ids")
    policy_ids = _sorted_unique(value.policy_decision_ids, "policy_decision_ids")
    comparison_ids = _sorted_unique(value.comparison_ids, "comparison_ids")
    if comparison_ids:
        raise ValueError("comparison_ids require a durable comparison ledger")
    if not source_ids or (not policy_ids and not _is_v2_research_only(value.research_packet)):
        raise ValueError("report requires source and policy decision receipts")
    markdown = value.full_markdown.rstrip()
    research_catalog = _research_catalog_markdown(value.research_packet)
    if research_catalog:
        markdown = f"{markdown}\n\n{research_catalog}"
    if "suggestion only" not in markdown.lower():
        markdown += "\n\nSuggestion only; no order was placed."
    if len(markdown.encode()) > 14_000:
        raise ValueError("report text must be bounded")
    body = {
        "actionable_risk": value.actionable_risk,
        "comparison_ids": list(comparison_ids),
        "full_markdown": markdown,
        "intraday_triggered": value.intraday_triggered,
        "material_thesis_change": value.material_thesis_change,
        "policy_decision_ids": list(policy_ids),
        "source_ids": list(source_ids),
        "suggestion_only": True,
        "summary": value.summary.strip(),
        "title": value.title.strip(),
    }
    canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    content_hash = _sha256(canonical)
    key = report_idempotency_key(
        value.kind, value.market_date, value.packet_hash, content_hash
    )
    return MarketReport(
        report_id=report_id_from_key(key),
        idempotency_key=key,
        packet_id=packet_id,
        packet_hash=value.packet_hash,
        market_date=value.market_date,
        kind=value.kind,
        title=body["title"],  # type: ignore[arg-type]
        summary=body["summary"],  # type: ignore[arg-type]
        full_markdown=markdown,
        source_ids=source_ids,
        policy_decision_ids=policy_ids,
        comparison_ids=comparison_ids,
        actionable_risk=value.actionable_risk,
        material_thesis_change=value.material_thesis_change,
        intraday_triggered=value.intraday_triggered,
        content_hash=content_hash,
        rendered_hash=_sha256(markdown.encode()),
    )


__all__ = ["MarketReport", "ReportInput", "ReportKind", "build_report", "report_id_from_key", "report_idempotency_key"]
