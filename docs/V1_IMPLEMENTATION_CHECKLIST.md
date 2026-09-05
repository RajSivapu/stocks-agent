# Personal Stock Agent V1 — Implementation Checklist

Last updated: 2026-09-05

## What we are building

An owner-only personal stock agent that uses zero-incremental-cost data sources, produces evidence-backed suggestions, never executes brokerage trades, and fails closed when cash, price, evidence, delivery, or release state is uncertain.

## Current status

- [x] Design and implementation plan approved
- [x] Core V1 implementation completed
- [x] Initial Astra audit findings F1–F19 implemented
- [x] Consolidated local gate passed: 1,006 checks passed, 4 skipped, 4 credentialed database tests intentionally deselected
- [x] Final whole-repository GPT-6 Astra review completed
- [ ] Final Astra fix wave complete — **10 of 11 findings locally implemented; 5 remains open at the Sites transport/publication conditional**
- [ ] Final focused review and consolidated gate
- [ ] Final GPT-6 Astra re-review
- [ ] Protected production rollout and live receipts

## Completed implementation areas

- [x] Credentialed database tests are opt-in and deployment-safe
- [x] Six-digit email OTP contract and true inactivity timeout
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
- [x] 3. Make deployment upgrade-aware and restore exact prior component state — **native recovery requires proven candidate/partial ownership and stable existing function IDs; focused tests complete, independent fix review pending**
- [x] 4. Preserve already-applied migration bytes and move changes into additive migrations — **audited bytes restored; actual native-ledger PostgreSQL upgrade/retry passed**
- [ ] 5. Deploy, verify, and recover every changed component, including Telegram and the owner web site — **all three native Edge paths implemented; reviewed Sites transport and immutable four-artifact publication/receipt integration remain open and preflight blocks before mutation**
- [x] 6. Persist provider attempts before transport and reconcile crash uncertainty without duplicate calls
- [x] 7. Allow durable no-trigger completion for quiet scheduled intraday runs
- [x] 8. Propagate the authoritative `start_run` duplicate flag into release evidence
- [x] 9. Use one lossless canonical hash representation for events and rankings
- [x] 10. Never delete the source recovery backup when verification fails
- [x] 11. Export and actually restore the complete report, delivery, acknowledgement, and release-ledger closure

## What is happening now

- [ ] Finish findings 3–5 in one release-integration pass
- [x] Run focused release/migration/rollback tests
- [ ] Run one consolidated repository gate after the fixes
- [ ] Run one scoped GPT-6 Astra re-review of the 11 findings
- [x] Update this checklist and the formal status/rollout records

## Production gates — intentionally not claimed yet

These require the protected production path and must not be replaced by local evidence.

- [ ] Confirm the live Supabase email template emits a six-digit OTP
- [ ] Push/merge the exact reviewed commit through protected `main`
- [ ] Pass exact-head CI
- [ ] Run the protected deployment workflow
- [ ] Verify deployed byte/version parity for gateway, dashboard API, Telegram function, and owner web site
- [ ] Perform the protected restore drill and retain its receipt
- [ ] Observe fresh scheduled morning/intraday/weekly receipts without triggering duplicate live runs
- [ ] Close V1-C2 through V1-C6 only when their production receipts exist

## Permanent safety rules

- [x] Suggestion-only; no brokerage execution authority
- [x] Owner-only access
- [x] Zero incremental-cost architecture
- [x] Fail closed on missing or stale authoritative inputs
- [x] No duplicate live scheduled runs for inspection
- [x] Local tests are never described as production deployment proof
