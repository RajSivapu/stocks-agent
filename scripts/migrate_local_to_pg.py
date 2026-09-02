"""Local-admin-only one-time import of historical JSON records into Postgres.

The suggestion path is intentionally limited to the service-role-only
``import_legacy_suggestion`` RPC, which creates its audit evaluation atomically.
Run this once from a trusted local checkout; it is not a cloud workflow entrypoint.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import db


ROOT = pathlib.Path(__file__).resolve().parents[1]
SUGGESTION_FIELDS = {
    "date", "ticker", "action", "bucket", "depth", "entry_zone_low", "entry_zone_high",
    "valid_until", "stop", "target", "confidence", "bull", "bear", "decisive_factor",
    "risk_verdict", "invalidation_level", "reason", "score", "price_at_suggestion",
    "evidence_as_of", "invalidation_price",
}


def normalize_action(raw: object) -> str:
    value = str(raw or "").strip().lower()
    exact = {"buy": "buy", "watch": "watch", "trim": "reduce", "exit": "sell"}
    if value in exact:
        return exact[value]
    if value in {"add", "add slowly", "add/dca", "dca", "dca/add slowly"}:
        return "add"
    if value == "hold/wait":
        return "hold"
    if value == "study" or value.startswith("watch -") or value.startswith("watch/add"):
        return "watch"
    if value in {"hold", "reduce", "sell", "avoid"}:
        return value
    raise ValueError("unsupported legacy suggestion action")


def normalize_confidence(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in {"low", "medium", "high"}:
        return value
    if value in {"low-medium", "medium-high"}:
        return "medium"
    raise ValueError("unsupported legacy suggestion confidence")


def _suggestion(raw: dict) -> dict:
    row = {key: raw.get(key) for key in SUGGESTION_FIELDS if key in raw}
    row["ticker"] = str(row.get("ticker", "")).strip().upper()
    row["action"] = normalize_action(row.get("action"))
    if "bucket" in row and row["bucket"] is not None:
        row["bucket"] = str(row["bucket"]).strip().lower()
    if "confidence" in row:
        row["confidence"] = normalize_confidence(row["confidence"])
    return row


def migrate(root: pathlib.Path = ROOT, db_module=db) -> dict[str, int]:
    """Import all supported local records, stopping on the first rejected write."""
    db_module.init_schema()
    counts = {"suggestions": 0, "holdings": 0, "radar": 0}

    suggestions = root / "data" / "suggestions-log.jsonl"
    if suggestions.exists():
        for line in suggestions.read_text().splitlines():
            if not line.strip():
                continue
            db_module.import_legacy_suggestion(_suggestion(json.loads(line)))
            counts["suggestions"] += 1

    portfolio = root / "config" / "portfolio.json"
    if portfolio.exists():
        for holding in json.loads(portfolio.read_text()).get("holdings", []):
            db_module.upsert_holding({
                "ticker": holding["ticker"],
                "shares": holding.get("shares", 0),
                "avg_cost": holding.get("avg_cost", 0),
                "bucket": holding.get("bucket"),
                "opened_at": None,
                "notes": None,
            })
            counts["holdings"] += 1

    radar = root / "data" / "radar.json"
    if radar.exists():
        for candidate in json.loads(radar.read_text()).get("candidates", []):
            db_module.upsert_radar({
                "ticker": candidate["ticker"],
                "added": candidate.get("added"),
                "last_seen": candidate.get("last_seen"),
                "days_relevant": candidate.get("days_relevant"),
                "reason": candidate.get("reason"),
                "bucket_guess": candidate.get("bucket_guess"),
                "promoted": candidate.get("promoted", False),
            })
            counts["radar"] += 1
    return counts


def main() -> int:
    counts = migrate()
    print(json.dumps(counts, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
