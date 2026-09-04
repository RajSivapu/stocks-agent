# Personal Stock Agent Project Status

Last updated: 2026-09-04
Canonical release: Personal Stock Agent V1
Source baseline: `c5886f5` on `codex/owner-alert-v3`
Current state: documentation checkpoint; written-spec approval required before planning or code

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

## In Progress

### V1-C1 — Release control

- [x] Consolidated architecture written.
- [x] Root canonical status created.
- [ ] Owner approves the written specification.
- [ ] Detailed implementation plan is written only after that approval.

### V1-C2 — Free intelligence ingestion

- [ ] Not started; blocked by the written-spec approval gate.

### V1-C3 — Market-discovery brain

- [ ] Not started; depends on C2 contracts and receipts.

### V1-C4 — Personal comparison brain

- [ ] Existing alternatives and Companion logic form a foundation; complete V1 integration is not
  started and depends on C3 candidate records.

### V1-C5 — Reports, dashboard, and delivery

- [ ] Existing dashboard and Telegram v3 foundations are implemented but are not the complete V1
  scope and are not claimed live.

### V1-C6 — Independent review and protected rollout

- [ ] Existing dashboard exact-head independent verdict is pending.
- [ ] Consolidated V1 exact-head review, protected deployment, and receipt verification are pending.

## Next

1. Owner reviews and approves or revises the written specification.
2. Invoke the writing-plans workflow and produce one implementation plan decomposed by V1-C1 through
   V1-C6.
3. Implement checkpoint by checkpoint with tests, internal review, and status updates.
4. Preserve scheduled production capacity and inspect only scheduled receipts at their established
   times.

## Owner Action

Review
`docs/superpowers/specs/2026-09-04-zero-cost-personal-stock-agent-v1-design.md` and explicitly approve
it or request changes. No implementation plan or product code will start before written-spec
approval.

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

## Blockers

- Written-spec approval blocks implementation planning and code.
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
- The Sep 4 intraday, post-market, and Friday audit checks remain scheduled. No duplicate live run
  will be triggered to inspect them.
- This documentation checkpoint creates no database write, report publication, Telegram send, Site
  version, function deployment, or production receipt.

## Last Commit

The source baseline reviewed for this documentation checkpoint is `c5886f5` (`docs: normalize
dashboard design formatting`). The documentation checkpoint commit is the current Git `HEAD` after
this file and the written specification are committed.
