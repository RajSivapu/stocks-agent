# Lessons Memory — the agent's running notebook

> The `market-briefing` agent READS this file every run, APPLIES the lessons, and UPDATES it with
> new observations. This is "learn from day 1" done the honest way: **memory + self-review over an
> LLM agent**, not a trained price-prediction model. Keep entries short, dated, and falsifiable.
> Prune or revise lessons that later prove wrong — be honest, never inflate.
>
> (v2: this file is `data/lessons.md`, migrated from the old `data/learning.md`. Structured grades
> live in the `suggestion_grades` Postgres table; this file holds the readable narrative + regime log.)

## How to use this file (instructions to the agent)
Each run:
1. **Read** the "Lessons learned" and "Market regime log" below and let them temper today's calls
   (e.g. be more cautious in buckets where past lessons say you've been wrong).
2. **Compare** the latest market trend to the previous entry in "Market regime log" — note what
   changed (direction, leadership, volatility, rates/news regime).
3. **Update**: append a dated regime line (post-market run); add/revise lessons when the grading pass
   teaches something new. Tie lessons to evidence from the `suggestions` / `suggestion_grades` tables.

## Lessons learned
_(none yet — will grow from the grading pass + track-record self-review. Example format below.)_
- `YYYY-MM-DD` — <lesson> — _evidence:_ <grade rows / outcomes that support it> — _action:_ <how it changes future calls>.

## Market regime log (latest trend vs prior)
_(one short dated line per run capturing the market backdrop, so trends can be compared over time.)_
- `2026-06-17` — UP, risk-on (small caps IWM +0.8% leading, QQQ +0.7%, VOO +0.2%); leadership: **semiconductors/AI** (SMH +3.3%, AVGO +5.5%, AMD +2.8%, TSM +2.7%), biotech firm (XBI +2.9%); laggards: mega-cap software (MSFT/GOOGL/AMZN ~-1.6%) — rotation out of software into chips; volatility: low–moderate; backdrop: easing US–Iran tensions calming oil. _(Baseline / first entry — nothing prior to compare yet.)_
- `2026-06-17` (pm) — UP but COOLING vs the morning risk-on pop: indices barely green (DIA +0.37%, QQQ +0.45%, VOO +0.04%) while mega-cap tech pulled back to/below its 50-day (NVDA, AVGO, MSFT, GOOGL MACD negative; MSFT/GOOGL/META RSI ~34-35; AMZN RSI ~23 oversold). Morning semis leadership faded (AVGO now below 50-day; SMH MACD rolling over). Read: healthy pullback within an uptrend (all indices still above 200-day), breadth narrowing. _vs prior 06-17 AM (semis ripping +3-5%): leadership cooled, momentum softened._

## Personalization notes (owner-specific)
- Owner: Rajrupesh — beginner, Year-1 foundation, starts $500/mo (first buy ~end of June 2026),
  scaling toward $1,000/mo, building slowly. Wants plain-English, honest, adaptive guidance.
- Update this as his portfolio, contributions, and risk comfort evolve.
