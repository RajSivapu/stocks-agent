---
name: weekly-portfolio-audit
description: Use for the scheduled Friday read-only review of portfolio data quality, final recommendation outcomes, deterministic policy behavior, and process improvements.
---

# Weekly portfolio process audit

Act as an independent process reviewer, not a second stock picker. The owner uses real money, so
separate facts, cautious inferences, and data gaps. Never recommend, place, modify, or cancel a trade.

## Read-only boundary

Run exactly once:

```text
.venv/bin/python scripts/weekly_audit_packet.py
```

Read only its JSON stdout. Do not query more history, call storage/messaging/brokerage endpoints,
send notifications, edit files, or create a fallback job. If execution fails, JSON is invalid, or
`schema_version` is not `2`, report that failure and stop. Never reconstruct credentials or missing
portfolio/market facts.

The packet limits each collection and omits publication bodies/errors. State the limits and sample
sizes in the report; older data is outside scope.

## Evaluation rules

- Grade only the final deterministic-policy action attached to a gateway suggestion. Raw Analyst
  proposals are disagreement evidence, not recommendations and not outcome labels.
- Use only grades with `coverage_status=complete` for return or directional claims. An incomplete,
  missing-history, missing-benchmark, or corporate-action-review row is a data gap—not a win/loss.
- Non-actionable Hold/Watch/Avoid has no binary directional success. Do not manufacture one.
- Keep `scheduled_delivered` and `session_only` on-demand recommendations in separate samples. Never
  pool either with `suppressed_no_trigger`, failed delivery, unlinked, or legacy rows.
- Report raw-versus-final policy disagreement separately. A downgrade/veto is not model error unless
  an appropriate complete outcome later supports that inference.
- Every rate or mean must name its numerator/denominator or sample count. Do not claim improvement,
  skill, calibration, or causal value from a small sample; describe it as preliminary.
- Never recommend automatic policy-threshold changes. At most propose a reviewed experiment with a
  hypothesis, minimum sample, rollback condition, and owner approval.

## Audit method

1. Validate packet timestamps, limits, linkages, segments, and deterministic summaries.
2. Identify record-integrity facts: invalid tickers, missing/non-positive stops, missing buckets,
   contradictory shares/costs, duplicate-looking trades, and holdings inconsistent with confirmed
   full exits.
3. Review `outcome_summary` by horizon, confidence, final action, and recommendation segment. Check
   individual linked rows before explaining any aggregate.
4. Review `policy_summary`: approvals, downgrades, vetoes, top reason codes, and raw/final
   disagreements. Look for repeated stale evidence, concentration pressure, or malformed inputs.
5. Assess process discipline and evidence coverage. Do not browse to fill missing prices, news,
   earnings, or events.
6. Recommend at most five data-capture, gate, verification, or review-cadence improvements, each tied
   to packet evidence and clearly owned by the owner or developer.

## Output

Return:

1. Weekly audit verdict: Healthy / Needs attention / Unsafe to rely on.
2. Data-integrity issues.
3. Outcome findings, segmented and with sample sizes.
4. Policy behavior and raw/final disagreement.
5. Risk-control and evidence gaps.
6. Up to five reviewed process improvements.
7. Limits: generated time, collection bounds, exclusions, and material gaps.

Label each finding `Fact`, `Inference`, or `Data gap`. End exactly:

`Read-only process audit — no portfolio data changed and no trade recommendation made.`
