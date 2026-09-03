# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-03.

This repository is suggestion-only decision support plus portfolio recordkeeping. It has no
brokerage integration and never places, modifies, or cancels a trade. Any future execution project
is separate, paper-first, and outside this codebase.

## Complete core product candidate

The provider-neutral, multi-user candidate is implemented locally on
`codex/stock-agent-reliability`; it is **not deployed to staging or production**. It includes the
owner-scoped ledger/projection model, RLS and machine-role boundaries, provider V2 contract, Claude
Routine adapter, DST-aware scheduling, server-owned evidence/policy/publication flow, seven-screen web
product, OTP/consent/account lifecycle, Telegram pairing, encrypted-recovery tooling, protected
deployment workflows, browser acceptance, the guarded owner-cutover controller, and a compact,
server-context-backed Telegram renderer v2. The complete candidate remains undeployed, but renderer
v2 alone is live on the legacy production gateway from `main` commit `1bdb490`.
The canonical schema is now generated from the same `supabase/migrations/` directory used by deployment,
and disposable tests prove both a from-empty replay and the existing-owner upgrade reach that catalog.

No owner-cutover or soak receipt exists. The remaining work needs real external infrastructure and
evidence: credential rotation, custom SMTP/phone-mail OTP, private staging and production Supabase,
Cloudflare static hosting and private R2, a real encrypted backup/restore, live two-owner isolation,
Claude API-trigger handshake, protected production deployment/rollback, and a complete owner market
cycle. **Friend invitations remain disabled** until those gates pass and one trusted-friend cycle is
reviewed.

## Legacy production snapshot

The following is the 2026-09-02 single-owner production snapshot. It was not re-verified while the
new candidate was built and must not be read as evidence for the candidate.

| Capability | Code status | Live status |
|---|---|---|
| Pre-market, intraday, and post-market Claude Routines | Receipt-driven prompts implemented | Live on `main` in the restricted `stocks-agent` environment; 2026-09-03 pre-market receipts passed, intraday/post-market observations remain |
| Scoped market gateway and least-privilege Routine client | Implemented and tested | Live; Routine has only Supabase URL, scoped gateway secret, and read-only Finnhub key; transcript-level environment proof was not available for the observed run |
| Fresh independent packet on every run | Implemented | Full production dry run completed with zero writes; first scheduled pre-market run produced four fresh evaluations |
| Analyst → Checker pass with stale/prior-plan veto | Implemented | Scheduled pre-market receipts contain four Analyst/Checker-backed policy evaluations |
| Server-refetched quotes + deterministic sizing/risk policy | Implemented and tested | Live with policy v1; Yahoo's omitted `marketState` is handled from validated provider trading windows |
| Atomic decision/evidence/publication audit trail | Implemented and rollback-tested | Migrations `20260902`–`20260904` applied and production verifiers passed |
| Deterministic Telegram Buy/Sell/Stop recorder, including delayed trade dates | Implemented, tested, Deno type-checked | Live; owner-confirmed Buy/Sell/Portfolio flows are working |
| Confirmed `/plan`, `/cancelplan`, and read-only `/plans` reminders | Implemented and rollback-tested | Live; owner-confirmed plan flow was exercised end to end and read back |
| Deterministic 5/21/63-session outcome grading | Implemented and rollback-tested | Live; results remain empty until eligible gateway decisions reach their horizons |
| Compact Telegram publication renderer v2 | Implemented and integration-tested | Deployed alone to the legacy gateway from `main` commit `1bdb490`; a production dry-run rendered the grouped portfolio/market/zones/risks/watch/read-more preview with empty write counts, no Telegram IDs, and unchanged protected-table counts; the next scheduled phase remains the first live publication receipt |
| Friday ChatGPT weekly process audit v2 | Packet + skill implemented and tested | Manual production packet passed on 2026-09-03; Friday 16:30 task remains to verify scheduler/delivery only |
| Watchlist changes as owner-reviewed proposals | Implemented in settings/skill | Live through the revised Routine; never edits/pushes the checkout |

All code through this legacy release is on GitHub `main`. "Live" means the corresponding external
Supabase, Telegram, Claude, or ChatGPT configuration was checked directly; pending observation is
called out separately.

## Complete-product release sequence

1. Pass Gate 0 with rotated market-data credentials and real external capability evidence.
2. Deploy the candidate to isolated staging; prove phone OTP, two-owner isolation, Claude handshake,
   scheduler behavior, encrypted R2 recovery, and rollback.
3. Produce the fail-closed Gate A-E acceptance evidence and use `scripts/cutover_owner.py` to move the
   current owner with all old mutation paths paused.
4. Complete the pre-market/intraday/post-market/maintenance/backup/alert owner soak. Any mismatch
   pauses all paths and invokes application rollback without destructive down-migration.
5. Configure custom SMTP and onboard one trusted friend only after Gate F is current. Review that
   entire cycle before enabling another invitation.

## Legacy decision-safety observations

The legacy decision-safety cutover is deployed. Migrations/verifiers, policy activation, both Edge Functions,
webhook health, secret rotation, Routine isolation, cloud healthcheck, Telegram owner flows, and two
production dry-run lifecycles are complete. Both dry runs reported empty writes/message IDs, and
independent before/after counts across eleven protected tables were unchanged.

The remaining legacy checks are observation rather than implementation:

1. Inspect the quiet intraday and post-market receipts. Confirm request/run, evaluation, suggestion,
   publication, artifact/grade, and delivery claims agree. The 2026-09-03 pre-market receipt check
   passed, subject to the documented Claude-transcript evidence gap.
2. Inspect the first Friday scheduled v2 audit and confirm scheduler delivery remains bounded,
   segmented, and read-only. The audit packet itself has already been run successfully.
3. After the first scheduled cycle, decide whether synthetic-looking legacy suggestions need a
   separately approved archival cleanup. Do not mix that destructive data cleanup into this
   release.

## Next reliability work

- Inspect renderer v2's first scheduled publication receipt. The code review, narrow legacy deploy,
  and exact production dry-run HTML inspection are complete; do not trigger a duplicate live run for
  visual testing.
- Observe how owner plan records appear in real briefs before deciding whether a separate proactive
  due-reminder job is useful. Any future reminder remains non-trading and never assumes a fill.
- Review policy/outcome samples after enough complete 5/21/63-session grades exist; do not change
  thresholds automatically or infer improvement from a small sample.
- Add retention/cleanup policy for old `telegram_updates` and expired `portfolio_commands` after
  observing real volume.
- Add a deploy-time integration test fixture for the Edge Function once a safe isolated Supabase test
  project exists.
- Review the static NYSE holiday calendar each December and add the next year's dates before January.
- After two weeks, inspect `analysis_runs` for data-source failures, stale veto frequency, notification
  noise, and Claude allowance usage; adjust cadence only from evidence.
- The multi-user redesign is implemented in the local candidate, but sharing remains prohibited until
  its live isolation, recovery, provider, SMTP, cutover, and owner-soak gates pass.

## Deferred research features

- Deeper monthly accuracy reporting after enough graded calls exist.
- An isolated, read-only strategy-validation lab using benchmark comparison, walk-forward tests,
  bootstrap/Monte Carlo analysis, and explicit survivorship-bias warnings. It must not share an
  environment with Telegram/Supabase secrets and must not have a live broker connector.
- Optional trade-journal habit analysis from an owner-supplied sanitized export. No broker login or
  write access is required or permitted.
- Social sentiment as context only, never a standalone signal.
- Congressional/13F digest.
- On-demand valuation models with explicit assumptions and no false precision.
- A genuinely separate-model manual second opinion for high-stakes decisions. The in-Routine Checker
  is a structured second pass by the same model and is not represented as independent validation.

## Evaluated external ideas

Detailed evidence and links are in
`docs/research/2026-09-02-external-stock-agent-ideas-review.md`.

| Idea | Decision | Useful part to retain | Reconsider only when |
|---|---|---|---|
| Puter user-pays AI | Defer; do not add to the personal agent | Possible per-user AI cost isolation for a future friend-facing web UI | Multi-user identity/RLS is complete, privacy and vendor behavior are reviewed, and Puter has no access to service-role or portfolio-write credentials |
| Viral autonomous Grok/Polymarket agent | Reject for real money | At most, use public claims as examples of why audited histories and risk-adjusted evaluation matter | A separate paper-only experiment is explicitly requested; never for this repository's real portfolio |
| Rare-earth magnets / MP Materials / Neo Performance Materials reel | No architecture change and no automatic watchlist addition | Demonstrates the existing need to verify policy, company execution, valuation, and timing separately | The owner explicitly requests fresh equity research on a named company or theme |
| NoFx | Do not integrate or install into the portfolio agent | Hard limits outside the model, safe-mode behavior, throttling, and complete decision auditing | A separate isolated paper-research project is justified after a security and license review; no exchange credentials |
| HKUDS Vibe-Trading | Defer wholesale integration | Benchmarking, walk-forward validation, Monte Carlo/bootstrap checks, and trade-journal analysis | The lightweight agent has enough history to justify a separate validation lab and the chosen version passes a fresh security review |
| Telegram reel inbox | Explicitly not planned | None; reels shared in conversation are review inputs, not portfolio data | Only if the owner later asks for this as a distinct product feature |

## Unchanging guardrails

- No brokerage credentials or order endpoints in this repository.
- No autonomous real-money execution.
- External projects and social-media ideas are never installed, trusted, or converted into signals
  solely because they are popular or claim high returns.
- Missing/stale/conflicting evidence cannot produce a new actionable conclusion.
- Telegram records only owner-reported events after explicit Confirm.
- Weekly ChatGPT audit is read-only and makes no fresh trade recommendation.
- Every owner still makes and places every trade personally.
