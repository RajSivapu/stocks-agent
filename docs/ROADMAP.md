# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-06.

This repository is owner-only, suggestion-only decision support plus portfolio recordkeeping. It
has no brokerage credentials or order endpoints and never places, modifies, or cancels a trade.
The Personal Stock Agent V1 remediation adds no paid provider, trial, premium infrastructure, or
metered runtime model API.

`PROJECT_STATUS.md` is the canonical release checkpoint. This roadmap describes the implementation
and rollout order. `docs/HANDOFF.md` is ignored and is not a source of truth.

## Current release status

The owner dashboard remains live; V1 trusted use remains **no-go** pending the protected one-time
existing-runtime attestation and existing scheduled evidence. Approved production schema reconciliation
`34039011879` succeeded exactly once. Managed isolated restore `34042155368` succeeded on main
`bd1cee2317a8689b8ac5a55fb38b853e7320bbfb`. Current main `eb240bf` passed exact-main CI
`34052341671`.
Restore artifacts were downloaded and validated once against GitHub archive digests and all four
embedded hashes: 26 verified record sets, identical production roots, no migrations applied on
retry, temporary project deleted, and both cleanup receipts successful with no retained project.
See `PROJECT_STATUS.md` for archive hashes. Do not rerun reconciliation or restore.

Telegram is the primary timely decision surface. The owner-only web app is a compact portfolio,
history, reconciliation, and audit-evidence surface—not a continuously live trading terminal.

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
- [ ] Protected one-time existing-runtime attestation receipt.
- [ ] Next existing scheduled receipt, without a duplicate run.

Final fix wave Track C: immutable audited migrations are restored and the additive tail is now
`20261001_immutable_history_closure.sql`. Local upgrade, encrypted recovery, four-artifact verifier,
and workflow tests are covered, including an actual disposable PostgreSQL upgrade and restore.
Recovery now includes policy comparisons and their decision-evaluation dependencies.

The protected main/recovery entrypoints share the component engine and the concrete
`scripts.configured_native_release_adapter.py` PostgreSQL/Supabase implementation. It captures and
restores runtime role attributes/verifier/memberships/settings, recoverable secret values/digests,
and exact downloaded bytes/configuration for all three Edge functions. Unchanged readback is a
recovery no-op; restored Edge versions are newly allocated and recorded alongside prior identities.
The unchanged `.openai/hosting.json` remains the sole zero-cost Site target. Its callable CI management
transport remains unavailable, so the mutation workflow correctly blocks before mutation. GPT-6 Astra
accepted that fail-closed boundary. Owner-operated native Site publication now supplies a fresh,
bounded platform receipt to the approved split-trust attestation; no caller-authored substitute is
accepted. Protected GitHub readback independently verifies the unchanged Supabase database, Auth,
managed-secret bindings, deployed Edge bytes, and API behavior. The final focused review,
consolidated 1,197-test gate, GPT-6 Astra scoped re-review, historical CI, owner-operated Edge
deployment readback, and private Site publication are complete. Historical database readback does
not establish the current recovery schema.

| Layer | Local candidate | Production status |
|---|---|---|
| Test and owner-auth containment | Implemented and task-reviewed | Live Auth read back, signed-link-compatible Site v5 deployed, owner email-click confirmed, and formal non-owner denial passed |
| Decision, evidence, and publication authority | Implemented and task-reviewed | Function deployment read back; Schema reconciled; protected runtime-attestation and scheduled receipts pending |
| Portfolio accounting and delivery integrity | Implemented and task-reviewed | Owner/anonymous/non-owner API canaries passed; original delivery receipt pending |
| Provider, cache, quota, ranking, and lifecycle | Implemented and task-reviewed | Live free-provider health and one existing scheduled chain pending |
| Outcomes, read privilege, dependency lock, and market sessions | Implemented and task-reviewed | Runtime is live; production observation pending |
| Candidate-bound release and recovery | Implemented and GPT-6 Astra review-clean; local disposable restore path covered | Schema reconciliation and isolated restore complete; protected runtime-attestation receipt pending |

The final consolidated local-gate code candidate was
`883d521728b1b3c2700a78dab1d65208105d7a2f`, based on the GPT-6 Astra audit of `origin/main` at
`432d647ef911ff63da427097f02a852e18038b62`. Later main `774584e` passed exact-head CI
`34013930731` and exact-main CI `34014003786`; these historical results are superseded by exact-main
CI `34052341671` and the recovery receipts above.

## Remediation workstreams

### 1. Containment

Implemented locally:

- F8: credentialed database tests require explicit opt-in and an exact allowlisted isolated
  Supabase project; the ordinary suite and deployment-local verification cannot inherit the opt-in.
- F18: token refresh cannot extend the 30-minute activity deadline.
- Owner email mismatch: frontend, provisioning, and verifier accept a six-digit token template or
  Supabase's free-tier signed confirmation link while retaining session-only storage.

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

- Retain the protected existing-runtime attestation receipt for the reconciled migration/function state.
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

- Retain the protected runtime-attestation receipt for the live restricted runtime and observe production
  outcomes without changing policy automatically.
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
- Retain the protected one-time existing-runtime attestation receipt before routine data advances.
- Observe the next existing scheduled receipt without a duplicate run.

## Ordered gates to trusted owner use

1. Independent whole-branch review with no unresolved Critical or Important finding. **Complete.**
2. Exact-main CI and current-main/merged-review binding. **Complete** for main `eb240bf` (`34052341671`).
3. Historical owner-operated gateway/API/Site deployment, runtime parity, and owner/anonymous
   canaries. **Complete for the prior runtime; signed-link-compatible Site v5 is live, while the
   production schema reconciliation is now complete.**
4. Live Auth configuration and owner email-link/code canary. **Complete; the owner confirmed the
   signed email link opened the live portfolio dashboard.**
5. Approved schema reconciliation and protected isolated restore with recovery receipts.
   **Complete** (`34039011879`, `34042155368`); do not rerun.
6. Protected existing-runtime attestation receipt and next existing scheduled intelligence/report/publication
   receipt chain, without a duplicate run.

Until all six gates pass, V1-C2 through V1-C6 remain reopened and the system stays in limited
owner-only research/shadow use. Positions, cash, prices, and calculations must be independently
verified before the owner acts.

## Exact remaining handoff boundary

Frontend and all Edge function sources are unchanged from their verified publications through
current main `eb240bf`; retain
Site v5 and the existing owner-only access. No publication is needed for the recovery changes.
Five verified release variables plus the existing Supabase service/publishable keys and owner
identity are now configured in the protected environment without credential rotation.
`PROJECT_STATUS.md` lists the original mutation-workflow inputs that remain unavailable. The
mutation workflow stays blocked and remains the required route for any future changed component.
For the unchanged V1 runtime, the approved split-trust workflow combines a fresh owner-scoped Sites
receipt with protected GitHub readback and emits a bounded immutable attestation without production
or scheduled-run mutation. It intentionally seals full reconciliation-root equality once, before
normal scheduled data growth. The restricted scheduled-evidence reader is still unavailable;
observe an existing persisted chain when available and never dispatch a duplicate. The live app
remains limited owner research/shadow use until protected attestation and scheduled gates pass.

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
