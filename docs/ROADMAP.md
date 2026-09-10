# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-10.

This repository is owner-only, suggestion-only decision support plus portfolio recordkeeping. It
has no brokerage credentials or order endpoints and never places, modifies, or cancels a trade.
The Personal Stock Agent V1 remediation adds no paid provider, trial, premium infrastructure, or
metered runtime model API.

`PROJECT_STATUS.md` is the canonical release checkpoint. This roadmap describes the implementation
and rollout order. `docs/HANDOFF.md` is ignored and is not a source of truth.

## Current release status

The provider/runtime-corrected market-wide candidate is deployed from protected main `df58d2b`.
PR #80 exact-head CI `34527241925`, exact-main CI `34528322660`, protected backend release
`34528678500`, and production deployment `6380879827` passed. Owner-only Site v10 remains current:
the protected September 10 build has the same build hash and all 15 asset hashes as the published
Site receipt, so no duplicate Site publication was needed. V1 trusted use remains **no-go** pending
the Finnhub key rotation and the first normal post-release scheduled operational and V1-C3
capability receipt, including the original Telegram delivery receipt or explicit persisted
suppression.
Approved production schema reconciliation
`34039011879` succeeded exactly once. Managed isolated restore `34042155368` succeeded on main
`bd1cee2317a8689b8ac5a55fb38b853e7320bbfb`. The unchanged backend remains attested at main
`b3f7d70` by protected one-time existing-runtime attestation `34055419086`; that historical boundary
is superseded for current code by the protected release receipt at `df58d2b`.
Restore artifacts were downloaded and validated once against GitHub archive digests and all four
embedded hashes: 26 verified record sets, identical production roots, no migrations applied on
retry, temporary project deleted, and both cleanup receipts successful with no retained project.
See `PROJECT_STATUS.md` for archive hashes. Do not rerun reconciliation or restore.

Telegram is the primary timely decision surface. The owner-only web app is a compact portfolio,
history, reconciliation, and audit-evidence surface—not a continuously live trading terminal.

The approved market-wide implementation is complete, review-clean, and deployed. Tasks 1–10 closed at
`18386b5c76c7a7aa8b3eeda870b12d3aba2399e1`. It adds the capability planner, dated SEC reference,
official-source collection and cursors, ticker-free event/entity resolution, bounded primary
exposure enrichment, explicit screen feasibility, separate research/suitability/action lanes, theme
memory and research nominations, a release-blocking capability verifier, and one canonical NYSE
calendar through 2028. The final exact candidate passed 2,367 explicit checks, with only documented
skips and credentialed test deselections, followed by exact PR/main CI and the protected release. See
`docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md` for the implementation boundary and
the September 9 receipts for production proof.

The protected GitHub path released the database and all three Edge functions, followed by native
owner-scoped Sites publication of the exact same candidate. Its immutable discovery migration tail
starts at `20261005_market_wide_discovery.sql` and ends at
`20261024_scheduled_same_day_retry.sql`; `20261004` remains the separate historical
reconciliation baseline. Protected backend proof and native Sites proof now exist as separate
September 9 receipts. Normal scheduled operational proof and V1-C3 capability proof remain distinct
and pending.

- [x] PR #10 fixed the Management API `User-Agent` and moved the production reference binding to a
  masked environment secret; exact-main `4003437` CI passed.
- [x] Managed isolated restore run `34012010196` reached the production snapshot and failed closed
  before temporary-project creation on the missing
  `public.portfolio_command_acknowledgements` relation. Same-job and independent cleanup receipts
  both report `deleted: true` and `retained: null`; read-only post-run inventory found exactly one
  healthy production project and no temporary restore projects.
- [x] PR #11 merged main `774584e`; exact-head CI `34013930731` and exact-main CI `34014003786`
  passed. Release is manual-only and secret-backed, with failed-release recovery trust, run-attempt,
  encrypted-journal, and durable-lease ordering hardened. No automatic release or recovery ran
  after merge.
- [x] PR #17 merged main `8497635`; exact-main CI `34028945226` and receipt-bound read-only inventory
  run `34029103876` passed without production mutation.
- [x] Approved receipt-bound production schema reconciliation (`34039011879`).
- [x] Managed isolated restore and both cleanup paths (`34042155368`).
- [x] Protected one-time existing-runtime attestation receipt (`34055419086`).
- [x] Simplified owner dashboard and UI/UX audit follow-up merged through PRs #28 and #29; Site v7
  deployment `appgdep_6a9df7eee2cc819193fefc13aa39d8bb` succeeded with one allowed owner, no
  groups, zero external visitors, matching live script hashes, and Site v6 retained as rollback.
- [x] Signed-link-only browser correction merged through PR #31; Site v8 deployment
  `appgdep_6a9dff357ee88191bf08d54af6f4240f` succeeded with the same owner-only access, matching live
  script hashes, and Site v7 retained as rollback.
- [x] Password-primary login and signed-link recovery merged through PR #33; Site v9 deployment
  `appgdep_6a9e15085f048191a80497d9e75fd253` succeeded with one allowed owner, no groups, zero external
  visitors, matching application assets, and Site v8 retained as rollback.
- [x] PR #59 exact-head CI `34364309798` and exact-main CI `34364808638` passed. Protected backend
  release `34365299574` and deployment `6352398867` succeeded; owner-only Site v10 then published
  the same main SHA with live-file parity and Site v9 retained as rollback.
- [x] The owner-requested September 10 pre-market attempt 2 finalized as a persisted
  `not_actionable` suppression. Zero usable evidence meant Telegram was correctly not called; this
  pre-fix run does not prove the corrected runtime.
- [x] PR #80 corrected the Claude source allowlist/SEC contact and Federal Register, Yahoo, EIA RSS,
  and dashboard runtime behavior. Protected release `34528678500` passed from main `df58d2b`.
- [ ] Rotate the exposed Finnhub API key, then retain the next normal post-release scheduled receipt
  without a duplicate run.

Final fix wave Track C: immutable audited migrations are restored and the additive tail is now
`20261001_immutable_history_closure.sql`. Local upgrade, encrypted recovery, backend-component
verification, the separate native Site package comparator, and workflow tests are covered,
including an actual disposable PostgreSQL upgrade and restore.
Recovery now includes policy comparisons and their decision-evaluation dependencies.

The protected main/recovery entrypoints share the component engine and the concrete
`scripts.configured_native_release_adapter.py` PostgreSQL/Supabase implementation. It captures and
restores runtime role attributes/verifier/memberships/settings, recoverable secret values/digests,
and exact downloaded bytes/configuration for all three Edge functions. Unchanged readback is a
recovery no-op; restored Edge versions are newly allocated and recorded alongside prior identities.
The unchanged `.openai/hosting.json` remains the sole zero-cost Site target. GitHub Actions has no
native Sites connector and receives no Sites write credential, so the protected mutation workflow
now releases only the backend. Owner-operated native Site publication is verified from fresh direct connector reads for the exact
same candidate. `scripts/verify_native_site_release.py` compares the local package to the protected
build and always leaves `owner_site` pending; copied JSON cannot establish connector provenance. The
later Site-only UI release uses the same native owner-scoped path with exact source, verified build,
owner-only access, deployment, authenticated live-script parity, and a provider-retained rollback target; it changes no
backend component. Protected GitHub readback independently verified the unchanged Supabase database, Auth,
managed-secret bindings, deployed Edge bytes, and API behavior in run `34055419086`. The final focused review,
consolidated 1,197-test gate, GPT-6 Astra scoped re-review, historical CI, owner-operated Edge
deployment readback, and private Site publication are complete. Historical database readback does
not establish the current recovery schema.

| Layer | Local candidate | Production status |
|---|---|---|
| Test and owner-auth containment | Implemented and task-reviewed | Live Auth read back, owner-only Site v9 deployed with password-primary login and signed-link recovery, owner email-click confirmed, and formal non-owner denial passed |
| Decision, evidence, and publication authority | Implemented and task-reviewed | Function deployment, schema, and protected runtime attestation complete; scheduled receipts pending |
| Portfolio accounting and delivery integrity | Implemented and task-reviewed | Owner/anonymous/non-owner API canaries passed; original delivery receipt pending |
| Provider, cache, quota, ranking, and lifecycle | Implemented and task-reviewed | Corrected provider probes accepted GDELT, DOE, Finnhub, Federal Register, Yahoo, Defense, and EIA RSS evidence; Finnhub rotation and one normal scheduled chain remain |
| Outcomes, read privilege, dependency lock, and market sessions | Implemented and task-reviewed | Runtime is live; production observation pending |
| Candidate-bound release and recovery | Implemented and GPT-6 Astra review-clean; local disposable restore path covered | Schema reconciliation, isolated restore, and protected runtime attestation complete |

The final consolidated local-gate code candidate was
`883d521728b1b3c2700a78dab1d65208105d7a2f`, based on the GPT-6 Astra audit of `origin/main` at
`432d647ef911ff63da427097f02a852e18038b62`. Later main `774584e` passed exact-head CI
`34013930731` and exact-main CI `34014003786`; these historical results are superseded by UI-main CI
`34073314211`, the backend attestation, and the recovery receipts above.

## Remediation workstreams

### 1. Containment

Implemented locally:

- F8: credentialed database tests require explicit opt-in and an exact allowlisted isolated
  Supabase project; the ordinary suite and deployment-local verification cannot inherit the opt-in.
- F18: token refresh cannot extend the 30-minute activity deadline.
- Owner email mismatch: provisioning and the protected verifier require both the magic-link and
  recovery templates to use Supabase's signed `ConfirmationURL`. The owner browser uses
  email-and-password by default, with a signed setup/recovery link and a signed magic-link fallback;
  it retains session-only storage and exposes no numeric-code field.

Production receipt:

- On 2026-09-05 the owner confirmed the signed email link opened the portfolio dashboard. A bounded
  temporary non-owner canary received HTTP 403 `owner_only` with no portfolio data and was deleted;
  Auth inventory returned to exactly one owner.

### 2. Decision and publication authority

Implemented locally:

- F1: an executable entry must be inside the approved zone and below target; sizing/risk use the
  verified executable price.
- F2: persisted final policy, not caller prose, owns actionable report content and delivery.
- F3: stored evidence timestamps, types, relationships, candidate membership, and contradictions
  determine eligibility.
- F4: the complete proposed plan reserves cash, allocation, position, and stop exposure in ranked
  order; sale-like actions cannot exceed available shares.

Still required:

- Protected existing-runtime attestation `34055419086` retained for the reconciled migration/function state.
- Reconcile one post-deployment scheduled packet, evaluation, report, and publication chain.

### 3. Money and delivery integrity

Implemented locally:

- F5: fixed-point accounting keeps valid fractional cost basis and makes invalid profit unavailable.
- F10: database commits and external Telegram acknowledgement/publication have separate durable
  pending, delivered, failed, and uncertain states with original-receipt recovery.
- F11: unsafe out-of-order transactions are rejected without corrupting holdings, cost, or realized
  profit; exact retry receipts are retained.

Still required:

- Schema reconciliation is complete; preserve its truthful reconciliation-only ledger and do not
  synthesize historical migration receipts.
- Verify production owner flows and the next existing scheduled original Telegram ID or explicit
  persisted suppression. Do not trigger a duplicate run.

### 4. Intelligence and lifecycle

Implemented locally:

- F6: declared adapters use provider-native request shapes and produce discoverable normalized
  evidence or attributable bounded failures.
- F7: item identity is separate from request provenance; corrections, conflicts, and distinct time
  semantics survive normalization and deduplication.
- F9: scheduled slots, phase-specific terminal stages, pending history, suppression, and overdue
  failures are enforced.
- F13: every outbound attempt consumes persisted quota; durable checkpoints/cache lineage support
  restart without hiding prior cost or ambiguity.
- F14: ranking and approved comparison/learning paths use typed, server-owned portfolio, liquidity,
  overlap, recency, and evidence strength; unknown inputs remain unavailable.

Still required:

- Run protected provider/database integration on the exact reviewed candidate.
- Observe a complete existing pre-market/intraday/post-market chain and surface every unavailable
  source or quota state without buying a replacement.

### 5. Outcomes and runtime controls

Implemented locally:

- F15: loss streaks are recommendation-scoped and bounded after eligibility; benchmark exposure is
  aligned, excursions use session highs/lows, fills are not assumed, and veto-only weeks remain.
- F17: weekly audit uses a restricted read-only TLS-verified role and Python installs are complete,
  hash-locked, and binary-only.
- F19: maintained calendar coverage includes 2026 early closes and fails closed outside coverage;
  quote symbol, USD currency, halt, spread, and liquidity determine actionability.

Still required:

- Protected runtime-attestation receipt `34055419086` retained for the live restricted runtime;
  observe production outcomes without changing policy automatically.
- Extend the reviewed market calendar before its maintained coverage expires.

### 6. Release and recovery

Implemented locally:

- F12: one candidate SHA binds current-main ancestry, merged review, exact-head CI, immutable source
  and artifact hashes, deployment identity, database stages, and original delivery/suppression.
- F16: the release captures durable rollback state; exports are authenticated and encrypted; restore
  verification checks exact datasets, roles, schema, reports, commands, and external receipts in an
  isolated target. A disposable local runtime exercises the production recovery state machine.

Still required:

- Exact-head CI `34013930731` and exact-main CI `34014003786` passed for main `774584e`.
- Schema reconciliation and isolated restore are complete (`34039011879`, `34042155368`); do not rerun.
- Protected one-time existing-runtime attestation `34055419086` completed before routine data advances.
- Observe the next existing scheduled receipt without a duplicate run.

## Ordered gates to trusted owner use

1. Independent whole-branch review with no unresolved Critical or Important finding. **Complete.**
2. Exact-main CI and current-main/merged-review binding. **Complete** for corrected main `df58d2b`
   (`34528322660`) and PR #80 exact-head CI `34527241925`.
3. Owner-operated gateway/API/Site deployment, runtime parity, and owner/anonymous canaries.
   **Complete for current main through protected release `34528678500`; owner-only Site v10 remains
   current because the protected frontend package is byte-identical.**
4. Live Auth configuration and owner email-link canary. **Complete; the owner confirmed the signed
   email link opened the live portfolio dashboard, and Site v9 adds password-primary login with the
   same signed-link callback reserved for setup, recovery, and fallback.**
5. Approved schema reconciliation and protected isolated restore with recovery receipts.
   **Complete** (`34039011879`, `34042155368`); do not rerun.
6. Protected existing-runtime attestation receipt **complete** (`34055419086`); current corrected
   backend and unchanged Site release receipts are also complete. Finnhub credential rotation and the
   next normal post-release intelligence/report/publication chain remain. The scheduled chain must
   run without a duplicate and supply both operational and V1-C3 capability evidence.

Until the six current-runtime gates and the approved V1-C3 market-wide discovery gates pass, V1-C2
through V1-C6 remain reopened and the system stays in limited owner-only research/shadow use.
Positions, cash, prices, and calculations must be independently verified before the owner acts.

## Exact remaining handoff boundary

The Edge functions and database migration ledger now use corrected main `df58d2b` as the protected
backend boundary. Retain Site v10 owner-only access and Site v9 as rollback. Receipts
`docs/receipts/2026-09-10-protected-provider-reliability-release.json` and
`docs/receipts/2026-09-09-native-site-v10.json` bind the backend and unchanged Site separately.
The September 10 pre-market attempt ran before this correction and cannot prove it. Rotate the
exposed Finnhub key, then observe the next persisted normal chain and never dispatch attempt 3 or a
standalone Telegram test. The live app remains limited owner research/shadow use until that chain
supplies the current operational, discovery-capability, and Telegram delivery-or-suppression
receipts.

## Final V1 closeout after protected release

September 8 receipts belong to the earlier runtime and cannot prove the current candidate. The
September 10 pre-market attempt 2 is a valid terminal suppression for the pre-fix runtime, but cannot
prove PR #80. Do not backfill either boundary with attempt 3 or a standalone Telegram run. The next
normal post-release phase must retain and reconcile its original evidence.

Market-wide thematic discovery remains original V1-C3 scope. Two GPT-6 Astra reviews identified the
gap between configured themes and actual runtime capability. The implemented boundary is recorded in
`docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md`; superseded working plans remain in
Git history. The implementation now has this status:

1. **Complete locally:** versioned capability registry, fair stage planner, durable cursors, and
   dated U.S.-listed security reference.
2. **Complete locally:** ticker-free events, entity/security resolution, adverse-aware value-chain
   paths, bounded role/theme reverse discovery for previously unseen issuers, and bounded primary
   exposure evidence.
3. **Complete locally:** broad-screen feasibility states. Transports that do not pass zero-cost and
   retention review remain disabled or unsupported with zero requests.
4. **Complete locally:** separate research eligibility, portfolio suitability, and action
   authorization, plus bounded theme history and next-run research nominations.
5. **Complete:** thematic, held-out-sector, adversarial, restart, quota, recovery, capacity,
   and 2026–2028 NYSE calendar acceptance. Exact-code Sol and flagship reviews are CLEAN at
   `f7e8236`. PR #35's first protected CI exposed a missing test-only `pglast` lock entry; `b789e0e`
   corrected the hash-locked requirements and passed a clean-environment reproduction. `248366e`
   adds the exact-head, successful-PR-CI-bound owner authorization required by this single-owner
   repository while retaining separate GitHub review support. PR #59 exact-head CI `34364309798`,
   exact-main CI `34364808638`, and independent Sol/Astra review all passed.
6. **Complete production release:** the original backend/Site release and the later PR #80 protected
   backend release/readback `34528678500` passed. Site v10 remains current because the September 10
   protected build hash and all asset hashes match its September 9 receipt.
7. **Pending security closeout:** rotate the exposed Finnhub API key and update the Claude routine.
8. **Pending natural schedule:** reconcile one normal post-release operational and capability chain,
   including its original Telegram delivery or explicit suppression, before V1-C2 through V1-C6 and
   the final checklist close.

Alert V3 remains a later, separately reviewed shadow/canary rollout. The discovery work does not arm
new alerts, increase schedule frequency, add brokerage authority, or add paid and metered providers.

## Deferred work

- Deeper strategy validation only after enough complete, correct, production-observed outcomes
  exist. Keep it isolated, read-only, and secret-free.
- Optional sanitized trade-journal analysis with no broker login.
- Social sentiment, congressional/13F digests, and valuation models only as later research context.
- Multi-user/friend access only after a separate owner-ID, RLS, secret-isolation, onboarding, and
  tenant threat model.
- Any brokerage or autonomous execution project remains outside this repository.

## Unchanging guardrails

- No brokerage credentials, order endpoints, or autonomous real-money execution.
- Every owner trade remains a manual decision and manual action.
- Missing, stale, contradictory, quota-blocked, or unverifiable evidence cannot become actionable.
- Telegram records only owner-reported events after explicit confirmation.
- Weekly audit and learning remain advisory and cannot silently change thresholds or authority.
- External popularity or claimed returns never authorizes a source, provider, or strategy.
- Local tests, green CI, a stored report, or a quiet bot are not deployment or scheduled-receipt
  proof.
