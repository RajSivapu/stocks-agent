"""One-time import of existing local history into Postgres.

Sources: data/suggestions-log.jsonl, config/portfolio.json, data/radar.json.
Radar upserts are idempotent (ON CONFLICT DO NOTHING). Suggestions are append-only
inserts, so run this ONCE (re-running would duplicate suggestion rows).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import json
from lib import db
ROOT = pathlib.Path(__file__).resolve().parents[1]

db.init_schema()

# --- suggestions ---
SUG_FIELDS = ["date", "ticker", "action", "bucket", "depth", "stop", "target", "confidence",
              "bull", "bear", "decisive_factor", "risk_verdict", "invalidation_level", "reason",
              "score", "score_growth", "score_health", "score_valuation", "risk_band",
              "score_inputs", "score_partial", "price_at_suggestion"]
p = ROOT / "data" / "suggestions-log.jsonl"; n = 0
if p.exists():
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        db.insert_suggestion({k: r.get(k) for k in SUG_FIELDS if k in r}); n += 1
print("imported suggestions:", n)

# --- holdings ---
pf = ROOT / "config" / "portfolio.json"; h_n = 0
if pf.exists():
    for h in json.loads(pf.read_text()).get("holdings", []):
        db.upsert_holding({"ticker": h["ticker"], "shares": h.get("shares", 0),
                           "avg_cost": h.get("avg_cost", 0), "bucket": h.get("bucket"),
                           "opened_at": None, "notes": None}); h_n += 1
print("imported holdings:", h_n)

# --- radar ---
rd = ROOT / "data" / "radar.json"; r_n = 0
if rd.exists():
    for c in json.loads(rd.read_text()).get("candidates", []):
        db.upsert_radar({
            "ticker": c["ticker"], "added": c.get("added"), "last_seen": c.get("last_seen"),
            "days_relevant": c.get("days_relevant"), "reason": c.get("reason"),
            "bucket_guess": c.get("bucket_guess"), "promoted": c.get("promoted", False),
        })
        r_n += 1
print("imported radar candidates:", r_n)
print("migration done")
