#!/usr/bin/env python3
"""Build one deterministic, bounded record_report gateway payload."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.intelligence.reports import ReportInput, build_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        if len(raw) > 65_536:
            raise ValueError("input exceeds bound")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "packet_receipt", "policy_receipts", "comparison_receipts", "content"
        }:
            raise ValueError("exact report input required")
        packet = value["packet_receipt"]
        content = value["content"]
        policies = value["policy_receipts"]
        if not isinstance(packet, dict) or packet.get("status") != "completed":
            raise ValueError("completed packet receipt required")
        if not isinstance(content, dict) or not isinstance(policies, list) or len(policies) > 50:
            raise ValueError("bounded accepted policy receipts required")
        if any(not isinstance(row, dict) or row.get("status") != "accepted" or
               row.get("packet_id") != packet.get("packet_id") for row in policies):
            raise ValueError("bounded accepted policy receipts required")
        if value["comparison_receipts"] != []:
            raise ValueError("comparison ledger unavailable")
        report = build_report(ReportInput(
            packet_id=packet["packet_id"], packet_hash=packet["packet_hash"],
            market_date=date.fromisoformat(packet["market_date"]), kind=content["kind"],
            title=content["title"], summary=content["summary"],
            full_markdown=content["full_markdown"],
            source_ids=tuple(packet.get("source_ids", [])),
            policy_decision_ids=tuple(row["id"] for row in policies), comparison_ids=(),
            actionable_risk=content.get("actionable_risk", False),
            material_thesis_change=content.get("material_thesis_change", False),
            intraday_triggered=content.get("intraday_triggered", False),
        ))
        print(json.dumps(report.to_gateway_payload(), sort_keys=True, separators=(",", ":")))
        return 0
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        print('{"error":"INVALID_REPORT_INPUT","ok":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
