# Personal Stock Agent V1 — Implementation Checklist

Last updated: 2026-09-06

## What we are building

An owner-only personal stock agent that uses zero-incremental-cost data sources, produces evidence-backed suggestions, never executes brokerage trades, and fails closed when cash, price, evidence, delivery, or release state is uncertain.

## Current status

- [x] Design and implementation plan approved
- [x] Core V1 implementation completed
- [x] Initial Astra audit findings F1–F19 implemented
- [x] Final consolidated local gate passed: 1,197 checks passed, 4 skipped, 4 credentialed database tests intentionally deselected
- [x] Final whole-repository GPT-6 Astra review completed
- [x] Final Astra fix wave complete at the safe local boundary — **all code findings closed; native owner-operated Sites publication remains a production gate**
- [x] Final Track C focused code review approved with no open Critical or Important findings
- [x] Consolidated final-candidate gate: **1,197 checks passed, 4 skipped, 4 credentialed database tests intentionally deselected**
- [x] Final GPT-6 Astra re-review approved at exact code commit `883d521`
- [x] Astra remediation merged to protected `main`; exact-main CI passed at `c9e3140`
- [x] Database ledger read back as current; gateway v33, dashboard API v4, and Telegram v20 deployed with runtime-byte parity
- [x] Private owner Site v5 published from exact `main` merge `ba3ebc1`; reviewed bundle parity and owner/anonymous API canaries passed
- [x] Live free-tier Auth read back and browser updated to accept its signed email link as well as six-digit codes
- [x] Receipt-bound production schema reconciliation completed exactly once in run `34039011879`
- [x] Protected isolated restore and both cleanup paths completed in run `34042155368`
- [ ] Protected-workflow and scheduled-chain receipts

## Completed implementation areas

- [x] Credentialed database tests are opt-in and deployment-safe
- [x] Free-tier signed email link/six-digit code contract and true inactivity timeout
- [x] Executable-price validation and portfolio-wide risk reservations
- [x] Evidence-authoritative reports and SQL idempotency
- [x] Decimal-safe and backdated transaction accounting
- [x] Durable report delivery and command acknowledgements
- [x] Provider evidence identity, discovery, deduplication, and time handling
- [x] Persistent request checkpoints, quota accounting, ranking inputs, and crash recovery
- [x] Scheduled-run lifecycle, history, suppression, and report origins
- [x] Protected release evidence, migration reconciliation, and recovery orchestration
- [x] Outcome semantics, least-privilege weekly audit, locked dependencies, market calendar, and quote identity
- [x] F1–F19 status and rollout documentation

## Final Astra review findings

- [x] 1. Persist only canonical policy-derived report prose, including suppressed reports
- [x] 2. Use fresh reconciled cash with ledger invalidation; never spend legacy monthly budgets as cash
- [x] 3. Make deployment upgrade-aware and restore exact prior component state — **native recovery requires proven candidate/partial ownership and stable existing function IDs; independent Track C review approved**
- [x] 4. Preserve already-applied migration bytes and move changes into additive migrations — **audited bytes restored; actual native-ledger PostgreSQL upgrade/retry passed**
- [ ] 5. Deploy, verify, and recover every changed component, including Telegram and the owner web site — **owner-operated deployment and runtime parity are complete; protected-workflow and isolated-restore receipts remain open**
- [x] 6. Persist provider attempts before transport and reconcile crash uncertainty without duplicate calls
- [x] 7. Allow durable no-trigger completion for quiet scheduled intraday runs
- [x] 8. Propagate the authoritative `start_run` duplicate flag into release evidence
- [x] 9. Use one lossless canonical hash representation for events and rankings
- [x] 10. Never delete the source recovery backup when verification fails
- [x] 11. Export and actually restore the complete report, delivery, acknowledgement, and release-ledger closure

## What is happening now

- [x] Finish findings 3–5 at the local safety boundary — **3 and 4 complete; owner-operated function/Site publication closes the runnable-app portion of 5**
- [x] Run focused release/migration/rollback tests
- [x] Complete independent Track C review with no open Critical or Important findings
- [x] Run one consolidated repository gate after the fixes — **1,197 passed**
- [x] Run one scoped GPT-6 Astra re-review of the 11 findings — **approved**
- [x] Update this checklist and the formal status/rollout records

## Production gates — intentionally not claimed yet

These require the protected production path and must not be replaced by local evidence.

- [x] Confirm live Auth is signup-disabled with 900-second JWT, six-digit/600-second OTP settings, and a signed-link template
- [x] Support both the free-tier signed link and optional six-digit code in the browser
- [x] Push/merge the exact reviewed remediation through protected `main`
- [x] Pass exact-main CI
- [x] Apply the receipt-bound production schema reconciliation exactly once — **run `34039011879`**
- [ ] Run the protected deployment workflow
- [x] Verify owner-operated deployed byte/version parity for gateway, dashboard API, Telegram function, and owner web site
- [x] Publish the signed-link-compatible web build as Site v5; deployment `appgdep_6a9cd0def8f0819187aa7d30d99a7ada` succeeded
- [x] Complete an owner email sign-in canary on that new Site build — **owner confirmed the signed email link opened the portfolio dashboard on 2026-09-05**
- [x] Perform the protected restore drill and retain its receipt — **run `34042155368` restored and
  verified 26 record sets, preserved identical production roots, applied no migrations on retry,
  deleted the temporary project, and passed both cleanup receipts**
- [x] Retain a formal non-owner login denial receipt — **temporary confirmed non-owner received HTTP 403 `owner_only`, no portfolio data was returned, the temporary user was deleted, and Auth inventory returned to exactly one owner**
- [ ] Observe fresh scheduled morning/intraday/weekly receipts without triggering duplicate live runs
- [ ] Close V1-C2 through V1-C6 only when their production receipts exist

## Permanent safety rules

- [x] Suggestion-only; no brokerage execution authority
- [x] Owner-only access
- [x] Zero incremental-cost architecture
- [x] Fail closed on missing or stale authoritative inputs
- [x] No duplicate live scheduled runs for inspection
- [x] Local tests are never described as production deployment proof
