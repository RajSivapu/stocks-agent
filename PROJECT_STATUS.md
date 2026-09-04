# Personal Stock Agent Project Status

Last updated: 2026-09-04
Canonical release: Personal Stock Agent V1
Source baseline: `167ce06` on `codex/owner-alert-v3`
Current state: V1-C5 complete locally; V1-C6 release infrastructure implemented locally and protected external gates pending

This file is the version-controlled source of truth for the Personal Stock Agent V1 rollout.
`docs/ROADMAP.md` retains historical capability and deployment detail. Notion may later mirror this
status but is not authoritative and must never contain credentials or private financial data.

## Release Definition

Deliver one complete owner-only, suggestion-only Personal Stock Agent V1 through V1-C1 to V1-C6.
The release includes zero-cost intelligence ingestion, market-wide discovery, personal portfolio
comparison, append-only reports, the private owner dashboard, bounded Telegram delivery, learning
evaluation, independent review, protected deployment, and receipt-backed production verification.

The proposed written design is
`docs/superpowers/specs/2026-09-04-zero-cost-personal-stock-agent-v1-design.md`. Missing approved
scope is not renamed V1.1.

## Done

- Established the existing reliable suggestion-only gateway, portfolio recordkeeping, scheduled
  routines, deterministic policy, receipts, owner alert v3 shadow path, and Long-Term Companion.
- Built and verified the owner-only read-only dashboard foundation with one-owner auth, a
  least-privilege direct-SELECT role, safe view contracts, responsive light/dark UI, and protected
  deployment tooling.
- Reserved the private one-account Site; the dashboard is not claimed live.
- Approved the zero-cost V1 provider and product architecture in owner chat.
- Wrote the consolidated V1 specification and this canonical checkpoint document.
- Owner approved the written specification on 2026-09-04.
- Wrote the single checkpointed implementation plan at
  `docs/superpowers/plans/2026-09-04-zero-cost-personal-stock-agent-v1-implementation.md`.

### V1-C1 — Release control

- [x] Consolidated architecture written.
- [x] Root canonical status created.
- [x] Owner approved the written specification on 2026-09-04.
- [x] Detailed implementation plan written after approval.

V1-C1 is complete.

## In Progress

### V1-C2 — Free intelligence ingestion

- [x] Release policy and configuration contract locked with the approved provider allowlist,
  zero-cost authority boundaries, phase budgets, and bounded evidence-packet limits.
- [x] Implemented and focused-tested bounded HTTPS transport, immutable quota reservations, all
  approved free-source adapters, timestamp-preserving cache behavior, normalization,
  deduplication, hashes, coverage limitations, and receipt-backed atomic gateway persistence.

V1-C2 is complete based on fixture-backed provider, transport/quota, normalization/deduplication,
gateway contract, migration-verifier, and security-invariant evidence. Live provider health and
production migration remain protected V1-C6 rollout gates and are not implied by this checkpoint.

### V1-C3 — Market-discovery brain

- [x] Deterministic seed taxonomy, exposure-gated relationships, fixed-point ranking, and bounded
  evidence-packet construction are implemented and focused-tested.
- [x] Receipt-backed phase orchestration and scheduled routine integration are implemented and
  focused-tested with one start, one atomic record, explicit partial failures, and write-free dry
  fixtures.
- [x] Scheduled Analyst and Checker records are bound to the exact immutable evidence packet before
  policy evaluation, with canonical hash verification, candidate-scoped evidence membership,
  exposure gating, distinct receipt linkage, copied/missing Checker vetoes, and dry-run-only inline
  fixtures.

V1-C3 is complete based on the exact Task 8 focused gate: 159 Deno gateway tests and 26 Python
security-invariant tests passed on 2026-09-04. The bounded fix verification also passed 55 focused
migration/security checks and the rollback-only local PostgreSQL verifier with 5 gateway-only RPCs,
0 public execute grants, and 0 remaining verifier rows. This local evidence does not claim a
production migration, provider/model call, database write, Telegram delivery, or deployment.

### V1-C4 — Personal comparison brain

- [x] Immutable evidence-valued comparisons use current gateway-shaped holdings and active owner
  plans as runtime anchors, expose explicit missing-data limitations and conditional cited
  bear/base/bull scenarios, and provide no portfolio mutation surface.
- [x] Gateway comparison policy preserves VTI role ownership, rejects substitutes as companions,
  requires a current baseline, permits overlap or concentration evidence to veto, and adds
  synchronized adjusted-return correlation without weakening existing complete-window thresholds,
  drawdowns, or normalized one-year contribution replay.

V1-C4 is complete based on the exact Task 9 focused gate: 7 Python comparison tests and 43 Deno
alternatives, Companion, and policy tests passed on 2026-09-04. This fixture-backed local evidence
does not claim a portfolio or plan mutation, live provider/model call, database write, Telegram
delivery, production migration, or deployment.

### V1-C5 — Reports, dashboard, and delivery

- [x] Immutable reports, quiet Telegram delivery, owner-only report/intelligence read contracts, and
  the five-surface owner dashboard are implemented locally.
- [x] Deterministic 5/21/63-session outcome learning, coverage-scoped missed-event observations,
  explicit source-failure/noise records, and owner-review-only proposals are implemented locally.

Task 10 immutable reports and quiet Telegram delivery is complete locally. Immutable canonical
report hashes, deterministic SHA-256 idempotency, sorted source references, concise morning and
summary/link periodic delivery, urgent/no-trigger gates, and unknown-outcome-safe replay are focused
verified. The report migration remains local-only and no Telegram message was sent.

Task 11 added bounded receipt-derived Intelligence, Reports, report-detail, Portfolio, Ideas, and
System read contracts plus authenticated owner-only GET routes and least-privilege direct-SELECT
access. Task 12 now presents exactly five primary surfaces: Portfolio, Ideas, Intelligence, Reports,
and System / Receipts. Portfolio absorbs Today and Companion context; System absorbs run and alert
receipt summaries; immutable report versions retain subordinate deep links and exact publication
timelines. External text is rendered as text, unsafe links remain non-clickable, source coverage is
explicitly bounded/partial, and no exhaustive-news, delivery, write, send, or deployment claim is
inferred.

The Task 12 web gate passed locally on 2026-09-04: 31 unit tests, TypeScript typecheck, ESLint,
production build, dashboard bundle security/size verification, and 17 Playwright browser checks
passed; the opt-in live read-only canary was skipped. The browser checks covered both themes,
keyboard access, axe accessibility, 300–1440 CSS pixel widths including 320, five primary routes,
report deep links, hostile stored content, and stale/owner-denied/expired states.

Task 13 adds frozen immutable learning observations linked to the original run and policy version.
Only complete benchmark-backed 5/21/63-session outcomes are eligible; insufficient samples remain
observations, and later evidence counts as a missed event only when authoritative, discovered after
the run, absent from the original ranking, and inside the run's declared source/time coverage.
Source failures and false-positive/noise rates remain explicit, historical results are labeled as
non-predictive, and proposed changes are owner-review records with no apply/update authority. The
gateway exposes only `record_learning` backed by the existing append-only
`record_market_learning` RPC; dry runs write and send nothing. The owner dashboard mapping exposes
only bounded/redacted observation metadata and never executable proposal content.

The exact Task 13 focused gate passed locally on 2026-09-04: 60 Python learning/gateway/security
tests and 60 Deno outcome/contract/handler tests passed. No provider or model call, database write,
policy/weight/provider activation, holdings/plan mutation, Telegram delivery, migration, or
deployment occurred.

V1-C5 is awaiting only the consolidated full suite and exact-head independent review. Nothing in
this checkpoint claims a live API, production migration, Telegram send, Site deployment, or owner
view receipt.

### V1-C6 — Independent review and protected rollout

- [x] Fail-closed V1 receipt verifier and fixed local/CI release-gate wiring implemented.
- [x] Protected scripts cover the two V1 migrations, only the changed gateway/dashboard functions,
  reviewed-SHA source/static receipts, five dashboard surfaces, anonymous/non-owner denial, and
  cleanup of newly introduced dashboard secrets/function/runtime login on initial failure.
- [ ] Existing dashboard exact-head independent verdict is pending.
- [ ] Consolidated V1 exact-head review, protected deployment, and receipt verification are pending.

Task 14 release infrastructure is local-only. Independent review blocked `a5d4296` on five release
boundaries; the local fix-round candidate closes exact scheduled run identity, scheduled report
calling and report-chain validation, real prior-gateway restoration, and typed 15-gate/source-chain
verification. The canonical review and rollout records remain pending templates and make no
production claim. V1-C6 remains incomplete until exact-head CI, repeat independent
review, dry-run, both migration/function/static deployments, source parity, owner and denial
canaries, rollback evidence, and the next existing scheduled receipts are all present.

The consolidated local gate passed on 2026-09-04: 364 Python, 63 Node, 218 Deno, 6 dashboard
contract, 31 web-unit, and 17 Playwright tests passed (699 total); 19 Python and one opt-in live
Playwright check skipped. Typecheck, lint, license, production build, and bundle scan also passed.
The first run exposed migration/schema drift in the learning authority rejection clause; the fixed
`20260907` migration now mirrors the already-reviewed schema defense and the complete gate passed.

## Next

1. Commit the review-fix checkpoint and obtain repeat exact-head independent review.
2. Run the protected dry-run/deployment only after exact-head CI and review pass.
3. Preserve scheduled production capacity and inspect only scheduled receipts at their established
   times.

## Owner Action

No action is required while local implementation and review proceed. Owner action will be listed
only when a protected external gate cannot be completed safely without it.

No provider purchase, paid trial, card signup, brokerage credential, friend invitation, or duplicate
live run is requested.

## Decisions

- One complete V1; no V1.1 deferral for approved scope.
- Zero incremental dollars and no metered runtime model API.
- Approved sources: GDELT, existing free Alpha Vantage, existing free Finnhub, Yahoo, and free
  official sources. Reddit/social is hypothesis discovery only.
- Massive, Benzinga, Alpaca, and all paid/commercial providers are excluded.
- Deterministic collection, caching, deduplication, mapping, ranking, and policy surround bounded
  Claude Analyst/Checker packets.
- Owner-only, friend invitations disabled, no brokerage, and suggestion-only.
- Dashboard primary surfaces: Portfolio, Ideas, Intelligence, Reports, and System / Receipts using
  the paired Midnight Navy/Warm Gold and Warm Pearl visual system.
- Detailed periodic/theme reports are stored once; Telegram receives a short summary and private
  authenticated link.
- Learning cannot silently change policy or authority.
- `PROJECT_STATUS.md` is canonical; Notion is optional as a later mirror only.
- The owner selected subagent-driven development on 2026-09-04 and delegated routine technical
  decisions while preserving all security, production, and receipt gates.

## Blockers

- Production dashboard deployment remains blocked by its exact-head independent review gate and
  must not deploy the superseded thin dashboard.
- Any unavailable free-provider entitlement, quota, or official-source access must fail closed and
  be surfaced; it does not authorize a paid replacement.

## Production Receipts

- Existing production foundation and scheduled receipt history are summarized in `docs/ROADMAP.md`.
- Owner alert v3 remains shadow-only; its receipt record is
  `docs/rollouts/2026-09-03-owner-alert-v3-shadow.md`.
- Owner dashboard protected rollout is not live; pending gates and empty production receipt fields
  are recorded in `docs/rollouts/2026-09-03-owner-dashboard-web-v1.md`.
- Consolidated V1 review and rollout gates remain empty and fail-closed in
  `docs/reviews/2026-09-04-personal-stock-agent-v1-review.md` and
  `docs/rollouts/2026-09-04-personal-stock-agent-v1.md`.
- The Sep 4 intraday, post-market, and Friday audit checks remain scheduled. No duplicate live run
  will be triggered to inspect them.
- This documentation checkpoint creates no database write, report publication, Telegram send, Site
  version, function deployment, or production receipt.

## Last Commit

The first Task 14 checkpoint was `a5d4296` (`feat: add fail-closed v1 release gates`) and was blocked
by independent review. The review-fix checkpoint becomes the new candidate for exact-head CI and
repeat independent review; it is not a C6 completion or production receipt.
