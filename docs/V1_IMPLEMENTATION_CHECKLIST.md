# Personal Stock Agent V1 — Implementation Checklist

Last updated: 2026-09-08

## What we are building

An owner-only personal stock agent that uses zero-incremental-cost data sources, produces evidence-backed suggestions, never executes brokerage trades, and fails closed when cash, price, evidence, delivery, or release state is uncertain.

## Current status

- [x] Design and implementation plan approved
- [x] Core V1 safety-remediation implementation completed
- [x] Initial Astra audit findings F1–F19 implemented
- [x] Final consolidated local gate passed: 1,197 checks passed, 4 skipped, 4 credentialed database tests intentionally deselected
- [x] Final whole-repository GPT-6 Astra review completed
- [x] Final Astra fix wave complete at the safe local boundary — **all code findings closed; native owner-operated Sites publication completed**
- [x] Final Track C focused code review approved with no open Critical or Important findings
- [x] Consolidated final-candidate gate: **1,197 checks passed, 4 skipped, 4 credentialed database tests intentionally deselected**
- [x] Final GPT-6 Astra re-review approved at exact code commit `883d521`
- [x] Astra remediation merged to protected `main`; exact-main CI passed at `c9e3140`
- [x] Database ledger read back as current; gateway v33, dashboard API v4, and Telegram v20 deployed with runtime-byte parity
- [x] Private owner Site v9 published from exact UI `main` merge `e2f9d74`; email-and-password login, signed-link setup/recovery, reviewed live-asset parity, and owner-only access readback passed, while the backend attestation remains bound to `b3f7d70`
- [x] Live free-tier Auth read back with signup disabled, exact Site/redirect URLs, and `ConfirmationURL` magic-link and recovery templates; the browser uses email-and-password by default with signed-link setup/recovery and a magic-link fallback
- [x] Receipt-bound production schema reconciliation completed exactly once in run `34039011879`
- [x] Protected isolated restore and both cleanup paths completed in run `34042155368`
- [x] Protected one-time existing-runtime attestation `34055419086`
- [x] GPT-6 Astra-reviewed market-wide thematic discovery design and implementation plan approved
- [x] Market-wide Tasks 1–10 implemented and independently reviewed through `18386b5`; latest full
  local gate passed Python 1,469, Node 71, Deno 332, package tests 7 + 53, and Playwright 24
- [x] Release-blocking V1-C3 verifier separates required capability success from operational receipt
  integrity and accepts an empty result only with parsed, persisted, receipt-backed success
- [x] Canonical NYSE calendar synchronized through 2028 with fail-closed behavior outside coverage
- [x] Task 11 release-candidate gate passed: Python 1,480, Node 71, Deno 333, package tests 7 + 53,
  Playwright 24, plus typecheck, lint, license, build, and bundle checks
- [x] Final release-trust gate passed: **1,997 checks** — Python 1,509, Node 71, Deno 333,
  package tests 7 + 53, and Playwright 24; 3 Python and 1 Playwright tests skipped, with 4
  credentialed database tests intentionally deselected; typecheck, lint, license, build, and bundle
  checks also passed
- [x] September 8 release-hardening gate passed: **2,030 checks** — Python 1,541, Node 71,
  Deno 334, package tests 7 + 53, and Playwright 24; 3 Python and 1 Playwright tests skipped, with
  4 credentialed database tests intentionally deselected; typecheck, lint, license, production
  build, and bundle checks also passed
- [x] Final exact-code gate at `f7e8236`: **2,032 checks** — Python 1,543, Node 71, Deno 334,
  package tests 7 + 53, and Playwright 24; 3 Python and 1 Playwright tests skipped, with 4
  credentialed database tests intentionally deselected; typecheck, lint, license, production build,
  and bundle checks passed
- [x] Sol attack replay and independent flagship review returned CLEAN on exact code `f7e8236`;
  planned-window, GDELT-inflation, and non-GDELT plan-inflation attacks reject, and all four phase
  capacities match the producer
- [x] Honest empty morning/post-market reports and receipt-bound quiet intraday `no_trigger`
  outcomes close without fabricated source, policy, report, or Telegram identifiers
- [x] Successful optional official sources can support research candidates while required baseline
  sources still fail closed and optional failures remain visible
- [x] Static build and Supabase CLI subprocesses receive strict environment allowlists; durable
  preparation evidence exists before fallible planning/capture; the local Site package comparator binds the
  exact candidate package to the protected build without claiming connector provenance
- [x] PR #35's first protected CI run correctly failed because `pglast` was missing from the committed
  test lock; `b789e0e` declares and hash-locks version 8.4, and a clean Python 3.14 environment passed
  all 72 formerly uncollectable SQL tests plus the 96-test focused V1 verifier
- [x] Solo-owner release authorization at `248366e` keeps independent GitHub approvals when present
  and otherwise requires an exact-head, successful-PR-CI-bound owner comment after CI and before
  merge; the workflow re-reads and records every authorization identity before secrets or checkout
- [ ] Next existing scheduled-chain receipt
- [ ] Complete protected backend release, separate native Sites publication and readback, and normal
  scheduled V1-C3 capability proof

## September 8 closeout sequence

The implementation is complete locally. The remaining work is operational and must occur in this
order on one exact reviewed candidate:

1. **Complete locally:** Sol and the independent flagship reviewer returned CLEAN on exact runtime
   code `f7e8236`; the release-candidate descendants contain status records and the test-only locked
   dependency correction at `b789e0e` and the CI-bound solo-owner release authorization at
   `248366e`. The corrected final PR head must be reviewed again.
2. Push the reviewed head, pass exact-head CI, merge it to protected `main`, and pass exact-main CI.
3. Run the manual protected backend release and verify the immutable migration, function,
   runtime-role, managed-secret, canary, artifact, and recovery receipts.
4. Compare the packaged Site with the protected build, then publish that package from the same
   candidate with the owner-scoped native Sites connector. Directly re-read owner-only access, the
   exact saved version and successful deployment, compare provider archive metadata with the local
   package, read every served file through the authenticated Site, and confirm the previous successful
   production version remains a provider-retained rollback target. The rollback is retained, not exercised.
5. Wait for the next existing normal scheduled run. Do not create another run for evidence. Accept
   either the complete report/publication chain or the exact receipt-backed quiet intraday
   `no_trigger` chain.
6. Close the operational and V1-C3 capability receipts, then close this checklist and the V1 goal.

## Completed implementation areas

- [x] Credentialed database tests are opt-in and deployment-safe
- [x] Free-tier email-and-password browser flow, signed-link setup/recovery and fallback, configuration drift checks, and true inactivity timeout
- [x] Executable-price validation and portfolio-wide risk reservations
- [x] Evidence-authoritative reports and SQL idempotency
- [x] Decimal-safe and backdated transaction accounting
- [x] Durable report delivery and command acknowledgements
- [x] Provider evidence identity, discovery, deduplication, and time handling
- [x] Persistent request checkpoints, quota accounting, ranking inputs, and crash recovery
- [x] Scheduled-run lifecycle, history, suppression, and report origins
- [x] Protected release evidence, migration reconciliation, and recovery orchestration
- [x] Outcome semantics, least-privilege weekly audit, locked dependencies, market calendar, and quote identity
- [x] Simplified three-tab dashboard, advanced evidence disclosure, keyboard skip navigation, and theme-aware control contrast
- [x] F1–F19 status and rollout documentation

## Final Astra review findings

- [x] 1. Persist only canonical policy-derived report prose, including suppressed reports
- [x] 2. Use fresh reconciled cash with ledger invalidation; never spend legacy monthly budgets as cash
- [x] 3. Make deployment upgrade-aware and restore exact prior component state — **native recovery requires proven candidate/partial ownership and stable existing function IDs; independent Track C review approved**
- [x] 4. Preserve already-applied migration bytes and move changes into additive migrations — **audited bytes restored; actual native-ledger PostgreSQL upgrade/retry passed**
- [x] 5. Deploy, verify, and recover every changed component, including Telegram and the owner web site — **owner-operated deployment, runtime parity, isolated restore, and protected existing-runtime attestation are complete**
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
- [x] Publish the signed-link-only login correction as owner-only Site v8 and retain Site v7 as rollback
- [x] Publish the password-primary login and recovery flow as owner-only Site v9 and retain Site v8 as rollback

## Approved work after the September 8 morning evidence

- [ ] Preserve the normal September 8 morning receipt as evidence for the currently deployed chain;
  do not treat it as proof of market-wide discovery and do not dispatch a duplicate run
- [ ] Reconcile the later September 8 intraday/post-market receipts and September 11 Friday receipt
  through the existing verification heartbeat
- [x] Implement the approved capability-aware source planner, dated security reference, official
  source cursors, ticker-independent event/entity resolution, bounded role/theme reverse discovery,
  value-chain graph, primary exposure facts, broad screens, theme memory, and bounded next-run
  research nominations; the normal collector persists canonical V2 episode revisions and consumes
  eligible due nominations within the three-task research-only cap
- [x] Keep research eligibility, portfolio suitability, and action authorization as separate states;
  missing suitability may preserve research but cannot authorize an action
- [x] Pass the priority-theme, held-out-sector, adversarial, restart, quota, recovery, byte-limit, and
  honestly-empty-run acceptance gates
- [x] Synchronize the official published NYSE 2026–2028 holidays and early closes across Python,
  gateway session logic, and dashboard freshness; fail closed after maintained coverage
- [x] Complete exact-code Sol and flagship review at `f7e8236`
- [ ] Complete exact-head and exact-main CI, protected backend release, native Sites
  publication/readback, backend recovery capture, and normal scheduled capability receipts
- [ ] Close V1-C2 through V1-C6 and the active V1 goal only after each checkpoint has its own evidence
- [ ] Review Alert V3 separately in shadow/canary mode after V1 closes; do not arm a new schedule as
  part of market-wide discovery

## Production gates

These require the protected production path and must not be replaced by local evidence.

The September 8 currently-deployed operational evidence, the new protected backend receipt, the
new direct native Sites observation, and the new V1-C3 scheduled capability receipt are separate artifacts.
Existing evidence cannot be relabeled as candidate capability proof. Market-wide migrations are the immutable additive chain from
`20261005_market_wide_discovery.sql` through
`20261014_honest_empty_report_persistence.sql`; `20261004` remains the separate reconciliation
baseline.

- [x] Confirm live Auth is signup-disabled with 900-second JWT, six-digit/600-second OTP settings, and `ConfirmationURL` magic-link and recovery templates
- [x] Match the browser to the live free-tier templates: email-and-password by default, signed-link setup/recovery, and signed magic-link fallback; require both link templates in protected configuration verification
- [x] Push/merge the exact reviewed remediation through protected `main`
- [x] Pass exact-main CI
- [x] Apply the receipt-bound production schema reconciliation exactly once — **run `34039011879`**
- [x] Run the protected one-time existing-runtime attestation before routine scheduled data advances — **run `34055419086`; artifact `9995809768`; receipt SHA-256 `88bedc1a0488e11e2a0c75bc981087f5eeacc45ec8c9d42dbf5c937104155baf`**
- [x] Verify owner-operated deployed byte/version parity for gateway, dashboard API, and Telegram function at the protected backend boundary; verify the later Site-only UI release separately
- [x] Publish the signed-link-compatible web build as Site v5; deployment `appgdep_6a9cd0def8f0819187aa7d30d99a7ada` succeeded
- [x] Publish the simplified and accessibility-reviewed UI as owner-only Site v7 from exact main `a4c8031`; deployment `appgdep_6a9df7eee2cc819193fefc13aa39d8bb` succeeded and `docs/receipts/2026-09-06-native-site-v7.json` binds the source, build, live scripts, access, and rollback
- [x] Publish the link-only owner Auth correction as owner-only Site v8 from exact main `b433a5b`; deployment `appgdep_6a9dff357ee88191bf08d54af6f4240f` succeeded and `docs/receipts/2026-09-06-native-site-v8.json` binds the source, build, live scripts, access, and v7 rollback
- [x] Publish password-primary owner Auth with signed-link setup/recovery as owner-only Site v9 from exact main `e2f9d74`; deployment `appgdep_6a9e15085f048191a80497d9e75fd253` succeeded and `docs/receipts/2026-09-06-native-site-v9.json` binds the source, build, Auth readback, live assets, Sol review, access, and v8 rollback
- [ ] Release the exact market-wide candidate backend through the protected GitHub workflow and verify its immutable component/recovery artifact
- [ ] Compare the exact package with the protected build, publish it as the private owner Site, and close `owner_site` only from fresh direct connector access/version/deployment observations plus authenticated live-file parity; retain the prior successful version as a provider rollback target
- [x] Complete an owner email sign-in canary on signed-link Site v5 — **owner confirmed the signed email link opened the portfolio dashboard on 2026-09-05; Site v9 preserves the same callback as setup/recovery and fallback**
- [x] Perform the protected restore drill and retain its receipt — **run `34042155368` restored and
  verified 26 record sets, preserved identical production roots, applied no migrations on retry,
  deleted the temporary project, and passed both cleanup receipts**
- [x] Retain a formal non-owner login denial receipt — **temporary confirmed non-owner received HTTP 403 `owner_only`, no portfolio data was returned, the temporary user was deleted, and Auth inventory returned to exactly one owner**
- [ ] Observe fresh scheduled morning/intraday/weekly receipts without triggering duplicate live runs
- [ ] Complete and prove the approved V1-C3 market-wide discovery capability on the protected path
- [ ] Close V1-C2 through V1-C6 only when both their capability gates and production receipts exist

## Permanent safety rules

- [x] Suggestion-only; no brokerage execution authority
- [x] Owner-only access
- [x] Zero incremental-cost architecture
- [x] Fail closed on missing or stale authoritative inputs
- [x] No duplicate live scheduled runs for inspection
- [x] Local tests are never described as production deployment proof
