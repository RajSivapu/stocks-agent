# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-02.

This repository is suggestion-only decision support plus portfolio recordkeeping. It has no
brokerage integration and never places, modifies, or cancels a trade. Any future execution project
is separate, paper-first, and outside this codebase.

## Current system

| Capability | Code status | Live status |
|---|---|---|
| Pre-market, intraday, and post-market Claude Routines | Receipt-driven prompts implemented locally | Existing routines remain on the prior workflow until production cutover |
| Scoped market gateway and least-privilege Routine client | Implemented and tested locally | Migration, secret, function deploy, and Routine credential replacement pending |
| Fresh independent packet on every run | Implemented locally | Pending gateway rollout plus dry/live phase checks |
| Analyst → Checker pass with stale/prior-plan veto | Implemented locally | Pending gateway rollout |
| Server-refetched quotes + deterministic sizing/risk policy | Implemented and tested locally | Pending gateway migration/function/policy activation |
| Atomic decision/evidence/publication audit trail | Implemented and rollback-tested locally | Pending production migration |
| Deterministic Telegram Buy/Sell/Stop recorder, including delayed trade dates | Implemented, tested, Deno type-checked | Live; owner-confirmed Buy/Sell/Portfolio flows are working |
| Confirmed `/plan`, `/cancelplan`, and read-only `/plans` reminders | Implemented and rollback-tested locally | Pending owner-plan migration and Telegram function deployment |
| Deterministic 5/21/63-session outcome grading | Implemented and rollback-tested locally | Pending outcome migration and gateway deployment |
| Friday ChatGPT weekly process audit v2 | Packet + skill implemented and tested locally | Existing scheduled task needs the merged v2 packet and first-report review |
| Watchlist changes as owner-reviewed proposals | Implemented in settings/skill | Available after revised Routine rollout |

"Implemented" means committed code and local verification on the feature branch. It does not mean
the external Supabase/Telegram/Claude/ChatGPT configuration has been changed.

## Remaining rollout checks

The original Telegram recorder is live. The decision-safety release remains local until this exact
cutover completes:

1. Capture a Supabase backup/recovery point and review the legacy suggestion preflight mappings.
2. Apply migrations `20260902`, `20260903`, and `20260904` in order, then run both RPC verifiers.
3. Set a new `MARKET_AGENT_SECRET`, deploy both Edge Functions, publish policy version 1, and retain
   only scoped/read-only credentials in the Routine environment.
4. Replace the three saved prompts from `routines/README.md`; run non-notifying healthcheck and
   start/context dry-run checks.
5. Verify one controlled live pre-market, quiet intraday, and post-market run. Confirm request/run,
   evaluation, suggestion, publication, artifact/grade, and delivery receipts agree.
6. Inspect the first Friday v2 audit and confirm it is bounded, segmented, and read-only.

## Next reliability work

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
- Before sharing with friends, redesign holdings/commands around `owner_id`, add per-owner RLS and bot
  onboarding, separate secrets/configuration, and threat-model tenant isolation. The current release
  is intentionally single-owner.

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
