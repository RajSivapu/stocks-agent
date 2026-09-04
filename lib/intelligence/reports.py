"""Deterministic immutable owner research reports."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal


ReportKind = Literal["morning", "urgent", "weekly", "monthly", "theme", "on-demand", "intraday"]
_REPORT_NAMESPACE = uuid.UUID("5489a117-f79a-4ca8-98cd-792e81628472")
_HASH = frozenset("0123456789abcdef")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_hash(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in _HASH for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hash")


def _sorted_unique(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value or len(value) > 100 for value in values):
        raise ValueError(f"{name} must contain bounded identifiers")
    return tuple(sorted(set(values)))


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


def report_idempotency_key(kind: str, market_date: date, packet_hash: str) -> str:
    _validate_hash(packet_hash, "packet_hash")
    return _sha256(f"v1:{kind}:{market_date}:{packet_hash}".encode())


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
    if not value.title.strip() or len(value.title) > 200 or len(value.summary) > 1_000:
        raise ValueError("report text must be bounded")
    if not value.full_markdown.strip() or len(value.full_markdown) > 14_000:
        raise ValueError("report text must be bounded")
    if value.kind == "urgent" and not (value.actionable_risk or value.material_thesis_change):
        raise ValueError("urgent report requires actionable risk or material thesis change")
    if value.kind == "intraday" and not value.intraday_triggered:
        raise ValueError("intraday report requires a trigger")

    source_ids = _sorted_unique(value.source_ids, "source_ids")
    policy_ids = _sorted_unique(value.policy_decision_ids, "policy_decision_ids")
    comparison_ids = _sorted_unique(value.comparison_ids, "comparison_ids")
    markdown = value.full_markdown.rstrip()
    if "suggestion only" not in markdown.lower():
        markdown += "\n\nSuggestion only; no order was placed."
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
    key = report_idempotency_key(value.kind, value.market_date, value.packet_hash)
    return MarketReport(
        report_id=str(uuid.uuid5(_REPORT_NAMESPACE, key)),
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
        content_hash=_sha256(canonical),
        rendered_hash=_sha256(markdown.encode()),
    )


__all__ = ["MarketReport", "ReportInput", "ReportKind", "build_report", "report_idempotency_key"]
