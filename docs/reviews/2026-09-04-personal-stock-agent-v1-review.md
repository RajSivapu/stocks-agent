# Personal Stock Agent V1 — Exact-Head Review Record

Status: **`a5d4296` rejected with blocking findings; replacement candidate pending exact-head re-review**.

## Candidate identity

- Prior reviewed SHA: `a5d4296595ab5f41a7120953ba117638888dccf6`.
- Replacement candidate SHA: the fix-round checkpoint produced from this record; pending re-review.
- Exact-head CI run URL: pending.
- Local full-suite receipt: passed on 2026-09-04 — 364 Python, 63 Node, 218 Deno, 6 dashboard
  contract, 31 web-unit, and 17 Playwright tests passed; 19 Python and one opt-in live Playwright
  check skipped. Typecheck, lint, license, production build, and bundle scan also passed.
- Reviewer identity and review timestamp: pending.

The reviewer must inspect the exact candidate SHA recorded above. A review of a parent commit, dirty
tree, generated patch without source context, or later amended commit does not satisfy this gate.

## Required adversarial review

The final verdict must explicitly cover:

- approved-provider terms, zero-cost budgets, and quota races;
- prompt injection and untrusted-source containment;
- evidence hashes, append-only persistence, idempotency, and receipt integrity;
- exposure qualification and Analyst, Checker, and deterministic-policy bypass attempts;
- owner financial-data leakage, bounded redaction, and unsafe-link or rendered-content handling;
- dashboard JWT ownership, exact-origin CORS, direct-column database privileges, and GET-only routes;
- report duplication, Telegram acceptance semantics, and owner-view claim boundaries;
- both V1 migrations, the changed gateway and dashboard API, immutable static assets, and deployed
  source parity; and
- initial-deployment rollback of the new dashboard secrets, function, and runtime login.

## Prior findings and verdict

- Critical findings: scheduled collection generated an unrelated intelligence run UUID instead of
  binding the `start_run` analysis UUID.
- Important findings: no scheduled `record_report` caller; unverifiable report identifiers and
  caller-controlled idempotency; no real gateway restoration during failed initial deployment; and
  shallow C6 receipt/source verification.
- Suggestions: pending.
- Verdict: blocked; do not deploy `a5d4296`.
- Reviewed candidate SHA: `a5d4296595ab5f41a7120953ba117638888dccf6`.

## Replacement candidate

All five blocking boundaries are implemented locally. The replacement remains ineligible for
deployment until its exact SHA passes CI and an independent reviewer explicitly approves that same
SHA. No prior review receipt transfers to the replacement candidate.

No empty field in this record is evidence of review. Any Critical or Important finding requires a
new exact candidate, full verification, and repeat independent review.
