# Stocks Agent — Roadmap (all phases, one place)

Last updated: 2026-06-15. Covers both projects: `stocks-agent` (advisor) and `stock-trader-bot`
(future auto-trader). **Every phase below is suggestion-only/read-only EXCEPT Project 2, which is
the only one that ever executes trades — and only paper-first, much later.**

| Stage | What | Status | Gate to start | Docs |
|---|---|---|---|---|
| **v1** | Morning brief + suggestions, emailed, scheduled 07:30 ET; 70/20/10; insider "smart money"; multi-role reasoning; risk limits; suggestion logging | **Planned, ready to build** | none — build now | spec `…/specs/2026-06-15-stocks-agent-design.md`, plan `…/plans/2026-06-15-stocks-agent-morning-brief.md` |
| **v2 · Phase 3** | Intraday breaking-news watch (alerts only on material events) | Scope locked; plan deferred | v1 has run ~1–2 wks (need quota data) | v2 spec `…/specs/2026-06-15-stocks-agent-v2-design.md` §2 |
| ~~v2 · Phase 4a~~ Telegram delivery | **MOVED INTO v1** (primary delivery channel) | ✅ in v1 | — | plan + v2 spec §3 |
| **v2 · Phase 4b** | DEEPER monthly accuracy report (lightweight track-record already in v1) | Scope locked; plan deferred | ≥1 month of v1 logs | v2 spec §4 |
| **v2 · Phase 5** | Social sentiment (Reddit/X/RSS) lens | Scope locked; plan deferred | after core v2 works | v2 spec §5 |
| **v2 · Phase 6** | Congressional + 13F smart-money digest | Scope locked; plan deferred | after core v2 works | v2 spec §6 |
| **v2 · `valuation`** | On-demand DCF + comps ("cheap or overpriced?"). Zero daily quota | Scope locked; plan deferred | evaluate official plugins first | v2 spec §7a |
| **v2 · `earnings-review`** | On-demand: transcript → plain English + thesis re-check (hold/trim/sell lean) | Scope locked; plan deferred | evaluate official plugins first | v2 spec §7b |
| **v2 · CAN SLIM lens** | Replicate IBD/O'Neil CAN SLIM criteria (free) as a scorecard for swing/growth ideas | Scope locked; plan deferred | refinement, after core v2 | v2 spec §8 |
| **Project 2** | Real auto-trader, $100, paper-first | **Charter only** | ~Sept 2026 AND v1 accuracy report is good | `../../stock-trader-bot/docs/PROJECT-CHARTER.md` |

## Sequencing logic
1. **Build v1, run it ~1–2 weeks.** Collect: per-run Pro-quota cost, what "material" means to you,
   and real suggestion logs.
2. **Brainstorm the 3 calibration items** (intraday cadence, alert thresholds, accuracy metrics —
   see v2 spec §7), then write the v2 implementation plan and build Phases 3 → 4a → 4b.
3. **Add Phases 5–6** (social + congressional/13F) once core v2 is stable.
4. **Only if** the accuracy report shows the suggestions are genuinely good, AND it's ~Sept 2026,
   AND you've run it in paper mode — brainstorm Project 2 and build the auto-trader (paper first,
   then the $100).

## Unchanging guardrails across everything
- Suggestion-only / read-only until Project 2; no trade-execution tools installed in v1 or v2.
- Every output carries the "educational, not financial advice" disclaimer.
- Rejected for good: self-optimizing backtest loops (overfitting), day-trading/scalping,
  autonomous execution of real money without the Project 2 safety stack, gambling/prediction
  markets.
