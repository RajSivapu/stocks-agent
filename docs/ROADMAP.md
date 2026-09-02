# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-02.

This repository is suggestion-only decision support plus portfolio recordkeeping. It has no
brokerage integration and never places, modifies, or cancels a trade. Any future execution project
is separate, paper-first, and outside this codebase.

## Current system

| Capability | Code status | Live status |
|---|---|---|
| Pre-market, intraday, and post-market Claude Routines | Implemented | Existing routines live; revised prompts still need to be pasted after this branch is merged |
| Fresh independent packet on every run | Implemented | Pending revised Routine rollout + three manual dry/live checks |
| Analyst → Checker pass with stale/prior-plan veto | Implemented | Pending revised Routine rollout |
| Quote exchange timestamps + 20-minute action gate | Implemented and tested | Available after updated repo is used by Routines |
| `analysis_runs` audit trail | Implemented and migrated | Routine writes remain pending revised Routine rollout |
| Deterministic Telegram Buy/Sell/Stop recorder, including delayed trade dates | Implemented, tested, Deno type-checked | Live; owner-confirmed Buy/Sell/Portfolio flows are working |
| Atomic Confirm/Cancel RPCs | Implemented and verified | Live in the Telegram recorder |
| Friday ChatGPT weekly process audit | Packet + skill implemented and tested | Active Fridays at 16:30 CT; first scheduled report still needs review |
| Watchlist changes as owner-reviewed proposals | Implemented in settings/skill | Available after revised Routine rollout |

"Implemented" means committed code and local verification on the feature branch. It does not mean
the external Supabase/Telegram/Claude/ChatGPT configuration has been changed.

## Remaining rollout checks

The Supabase migration, credential rotation, Edge Function deployment, webhook registration, and
live portfolio-command checks are complete. Remaining checks:

1. Confirm the three Claude Routine prompts match `routines/README.md`, then manually verify
   pre-market, intraday, and post-market behavior.
2. Confirm each Routine creates and completes an `analysis_runs` row with accurate source, write,
   and Telegram-send metadata.
3. Inspect the first Friday 16:30 America/Chicago ChatGPT audit report and confirm it stayed read-only.

## Next reliability work

- Add deterministic recurring-investment plans and Telegram reminders. Scheduling and reminders use
  Supabase Cron/Edge Functions with no model call and never assume that a scheduled purchase filled.
  An actual fill still enters through the existing preview-and-Confirm Buy workflow.
- Add a deterministic portfolio-risk checker outside the language-model prompt. Before a new Buy can
  be presented as actionable, calculate projected bucket size, single-position concentration, and
  stop-distance risk from current holdings; fail closed when required values are missing. This is a
  suggestion veto only and never becomes a brokerage control or execution feature.
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
