# Stocks Agent — Roadmap and Deployment Status

Last updated: 2026-09-01.

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
| `analysis_runs` audit trail | Implemented and migration ready | Pending Supabase migration |
| Deterministic Telegram Buy/Sell/Stop recorder | Implemented, tested, Deno type-checked | Pending credential rotation, migration, function deploy, and webhook registration |
| Atomic Confirm/Cancel RPCs | Implemented with disposable verification script | Pending Supabase migration + live TSTTG verification |
| Friday ChatGPT weekly process audit | Packet + skill implemented and tested | Pending local Codex scheduled-task creation |
| Watchlist changes as owner-reviewed proposals | Implemented in settings/skill | Available after revised Routine rollout |

"Implemented" means committed code and local verification on the feature branch. It does not mean
the external Supabase/Telegram/Claude/ChatGPT configuration has been changed.

## Go-live sequence

1. Rotate the exposed Telegram bot token, Finnhub key, and Alpha Vantage key.
2. Apply `sql/migrations/20260901_reliable_stock_agent.sql` in Supabase.
3. Run `.venv/bin/python scripts/verify_portfolio_command_rpc.py`; confirm all disposable TSTTG rows
   are removed.
4. Set the four Telegram Edge Function secrets, deploy `telegram-portfolio`, and run
   `scripts/register_telegram_webhook.py`.
5. Test `/help`, `/portfolio`, a Cancelled disposable command, and one confirmed disposable command.
6. Merge/push the branch, update the three Claude Routine prompts from `routines/README.md`, and
   manually verify pre-market, intraday, and post-market behavior.
7. Create the Friday 16:30 America/Chicago local ChatGPT audit task and inspect its first report.

## Next reliability work

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
- Social sentiment as context only, never a standalone signal.
- Congressional/13F digest.
- On-demand valuation models with explicit assumptions and no false precision.
- A genuinely separate-model manual second opinion for high-stakes decisions. The in-Routine Checker
  is a structured second pass by the same model and is not represented as independent validation.

## Unchanging guardrails

- No brokerage credentials or order endpoints in this repository.
- No autonomous real-money execution.
- Missing/stale/conflicting evidence cannot produce a new actionable conclusion.
- Telegram records only owner-reported events after explicit Confirm.
- Weekly ChatGPT audit is read-only and makes no fresh trade recommendation.
- Every owner still makes and places every trade personally.
