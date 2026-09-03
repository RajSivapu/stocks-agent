# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-03.

This repository is suggestion-only decision support plus portfolio recordkeeping. It has no
brokerage integration and never places, modifies, or cancels a trade. Any future execution project
is separate, paper-first, and outside this codebase.

## Current system

| Capability | Code status | Live status |
|---|---|---|
| Pre-market, intraday, and post-market Claude Routines | Receipt-driven prompts implemented | Live in the restricted `stocks-agent` environment; the 2026-09-03 intraday and post-market Routine transcripts, database chains, and Telegram receipts reconcile |
| Scoped market gateway and least-privilege Routine client | Implemented and tested | Live; Routine has only Supabase URL, scoped gateway secret, and read-only Finnhub key |
| Fresh independent packet on every run | Implemented | The first observed scheduled intraday run used same-run quote/history/news evidence instead of mechanically reusing the morning conclusion |
| Analyst → Checker pass with stale/prior-plan veto | Implemented | Five new intraday Analyst and Checker records were accepted by the gateway on 2026-09-03 |
| Server-refetched quotes + deterministic sizing/risk policy | Implemented and tested | Live with shadow-only policy v3; Yahoo's omitted `marketState` is handled from validated provider trading windows |
| Atomic decision/evidence/publication audit trail | Implemented and rollback-tested | Migrations `20260902`–`20260905` applied and production verifiers passed |
| Deterministic Telegram Buy/Sell/Stop recorder, including delayed trade dates | Implemented, tested, Deno type-checked | Live; owner-confirmed Buy/Sell/Portfolio flows are working |
| Confirmed `/plan`, `/cancelplan`, and read-only `/plans` reminders | Implemented and rollback-tested | Live; owner-confirmed plan flow was exercised end to end and read back |
| Deterministic 5/21/63-session outcome grading | Implemented and rollback-tested | Live; results remain empty until eligible gateway decisions reach their horizons |
| Friday ChatGPT weekly process audit v2 | Packet + skill implemented and tested | Active Fridays 16:30 local on `gpt-5.6-terra`; live packet smoke test passed, first scheduled report pending |
| Watchlist changes as owner-reviewed proposals | Implemented in settings/skill | Live through the revised Routine; never edits/pushes the checkout |
| Owner-only alert v3 | Implemented, reviewed, and rollback-tested | Live in shadow-only mode with gateway v11, Telegram v12, and policy v3; the corrected protected dry-run rendered policy-validated stop/target and `1/1` source-evidence coverage with zero lifecycle, gateway-request, suggestion, publication, or Telegram writes |
| Owner portfolio alternatives review | Gateway-computed history, evidence gate, renderer, and routine contract implemented and tested | Pending protected deployment and a send-free on-demand dry-run; VTI remains the unchanged recurring baseline |

The reliability release is on GitHub `main`. Alert v3 is deployed from
`codex/owner-alert-v3` for shadow verification and is not yet merged. "Live" means the corresponding
external Supabase, Telegram, Claude, or ChatGPT configuration was checked directly; pending
observation is called out separately.

The owner-only line contains the same `renderer.ts` v2 patch as checkpoint `386da6f`, applied to
`main` as `1bdb490`. The additional multi-user/web stack on `codex/stock-agent-reliability` remains a
separate deferred branch and must not be merged wholesale: it conflicts with the current personal,
friend-invitations-disabled product boundary. A future owner-only dashboard starts from a new
security design after alert stability, not by reactivating the deferred multi-tenant application.

## Remaining rollout checks

The fail-closed cutover is deployed. Migrations/verifiers, policy activation, both Edge Functions,
webhook health, secret rotation, Routine isolation, cloud healthcheck, Telegram owner flows, and two
production dry-run lifecycles are complete. Both dry runs reported empty writes/message IDs, and
independent before/after counts across eleven protected tables were unchanged.

The remaining checks are scheduled observation followed by an explicitly approved one-class canary:

1. Inspect the first Friday v2 audit and confirm it remains bounded, segmented, and read-only.
2. After the first scheduled cycle, decide whether synthetic-looking legacy suggestions need a
   separately approved archival cleanup. Do not mix that destructive data cleanup into this
   release.
3. Review a real owner-visible v3 shadow example, then enable only the explicitly approved
   `stop_breach` class as a canary. Do not enable from a synthetic preview.

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

## Personal portfolio research

- On demand and on the first pre-market brief of each month, compare active owner plans and selected
  holdings with at most six evidence-validated alternatives.
- The gateway, not model prose, computes synchronized adjusted-history lump-sum, equal-monthly, and
  max-drawdown figures. History is hypothetical and never represented as a forecast.
- VTI is the current baseline. ITOT/SCHB are like-for-like candidates; VOO is a large-cap tilt;
  VT/VXUS are diversification changes. Individual-stock peers require business-model validation.
- Forward views remain qualitative and are forced to `insufficient` when the alternative fails
  gateway policy or cited evidence is unavailable. The review never changes a holding or plan.

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
