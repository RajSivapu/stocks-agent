# Owner-Only Long-Term Companion

**Date:** 2026-09-03
**Status:** Approved for implementation by the owner.
**Scope:** Personal, owner-only, suggestion-only research beside an existing holding or recurring reminder.

## Decision

Add a Long-Term Companion layer to the existing portfolio-alternatives review. The layer answers a
different question from “did another ticker outperform VTI last year?”: does one currently supported
candidate add a distinct, defensible portfolio role beside the owner's recorded core, and is there
enough evidence to bring that candidate to the owner for further research?

The feature may select one candidate or explicitly report that none qualified. It cannot create,
change, cancel, or advance an investment reminder; edit a holding; assume that the owner has new
money available; connect to a brokerage; or place an order. Friend invitations remain disabled.

VTI remains the current recorded monthly baseline until the owner separately confirms a change.

## Product language

The agent must keep these concepts separate:

- **Substitute:** ITOT or SCHB performs substantially the same broad-U.S. core job as VTI. Holding
  both usually adds duplication rather than a new portfolio role. A substitute can be compared but
  cannot be selected as a companion.
- **Diversifier:** a supported holding such as VXUS adds exposure that the U.S.-only baseline does
  not provide. It can qualify for an owner-reviewed recurring-reminder discussion.
- **Tilt:** a supported fund such as VOO or SCHD deliberately changes concentration or factor
  exposure. It is not represented as broader diversification or as a default improvement over VTI.
- **Concentrated satellite:** an individual company or an unsanctioned fund may be researched only
  as a small, separate sleeve. It is never described as similar to a total-market fund and is not
  eligible for a recurring core reminder through this feature.

VT remains a global-core replacement candidate, not a companion beside VTI, because pairing it
with VTI would duplicate VT's U.S. holdings. Unsupported tickers fail closed as concentrated or
unsanctioned research rather than being inferred to be diversified funds.

## Structured request

`DecisionBundle` gains one optional top-level `companion_proposal` object, allowed only when the
bundle also contains `comparisons` and the phase is `pre-market` or `on-demand`:

```json
{
  "baseline_ticker": "VTI",
  "companion_ticker": "VXUS",
  "role": "diversifier",
  "thesis": "Non-U.S. equity exposure may reduce dependence on one national market.",
  "risk_note": "Currency, geopolitical, and non-U.S. market risks can create long periods of lagging U.S. stocks.",
  "evidence_ids": ["vxus-official-profile", "vxus-current-risk"]
}
```

Allowed roles are `diversifier`, `tilt`, and `satellite`. The baseline and companion must be
ordinary candidates with separate Analyst and Checker records. The pair must already exist in the
bundle's `comparisons` array, and its relationship must agree with gateway-owned role policy.
Evidence IDs must belong to the companion candidate, must also support one of its factors, and must
be fresh or an explicitly justified fallback. The schema permits at most one proposal.

The proposal is omitted when no additive candidate qualifies. When comparisons are present but a
proposal is absent, the renderer says that no additive companion cleared current evidence. This is
a valid conclusion, not a system failure.

## Gateway-owned qualification

The model nominates; the gateway decides what may be displayed. The gateway applies these checks:

1. The baseline is a current holding or active owner plan.
2. The nominated pair is present in the validated alternatives review.
3. A like-for-like substitute cannot become a companion.
4. Known fund roles cannot be relabeled: ITOT/SCHB are substitutes, VOO/SCHD are tilts, VT is a
   global-core replacement, and VXUS is an international diversifier beside a U.S. core.
5. An unknown ticker can qualify only as a `satellite`, never as a diversified or recurring core.
6. The companion evaluation must be policy-approved and its cited evidence available.
7. At least three years of synchronized adjusted history is required for a qualified display.

If current evidence, policy, or history fails, the gateway returns an `insufficient` companion
analysis with the reason. It does not silently promote a different ticker or reuse the model's
claim.

Recurring-reminder review eligibility is gateway-owned. In the initial catalog, only VXUS can be
marked eligible as an additive recurring core companion to VTI. Eligibility means the owner may
discuss or explicitly create a reminder later; it is not a recommendation, a reminder mutation, or
a brokerage instruction.

## Long-horizon analytics

The gateway, not model prose, fetches up to ten years of adjusted daily history for the baseline and
companion. It synchronizes common exchange dates and computes deterministic results for every
available 3-, 5-, and 10-year window:

- annualized adjusted-price return for each ticker;
- max drawdown for each ticker; and
- correlation of synchronized daily returns.

A window needs at least 240 synchronized sessions per requested year. The gateway shows only
complete windows and never extrapolates a shorter history into a longer one.

The gateway also computes a normalized rolling one-year contribution replay for the companion. Each
historical window contributes $100 on twelve consecutive monthly observations and records the
ending value after the twelfth contribution. From all complete windows it reports the 10th,
50th, and 90th percentile ending values, the fixed $1,200 contribution, and the sample count. The
calculation uses adjusted prices but excludes taxes, brokerage effects, spreads, the owner's actual
fill dates, and fund expenses not already reflected in market prices.

The replay is deliberately labeled **weak history**, **middle history**, and **strong history**—not
bear/base/bull forecasts and not probabilities. One year is a planning illustration only. The
message always states that historical scenarios are not forecasts and future loss is possible.

## Telegram message

The existing alternatives block remains available. When a qualified proposal exists, one compact
section is added:

```text
🧠 LONG-TERM COMPANION
Core stays: VTI · $300/month reminder
Research candidate: VXUS · DIVERSIFIER
Why it adds something: ...
Main risk: ...
3Y: VTI ... · VXUS ... · correlation ...
5Y: ...
10Y: ...
Per $100/month, rolling 1Y history: $1,200 contributed → weak ... · middle ... · strong ...
Plan status: eligible for owner review; no reminder was added or changed.
Historical scenarios are not forecasts. Future loss is possible.
```

For a tilt or satellite, the final status instead says it is research-only and not eligible for a
recurring core reminder. The section cannot contain `buy`, `sell`, `switch`, `replace`, `allocate`,
share quantities, proposed dollar amounts, price targets, stop levels, guaranteed-return language,
or commands. Those belong to separately evaluated action proposals and owner-confirmed portfolio
commands.

On-demand output remains visible only in the current Codex task and is suppressed from Telegram.
The scheduled version may appear only in the first pre-market brief of a calendar month, enforced
by the gateway's configured NYSE calendar. An on-demand bundle containing a companion proposal must
be a dry run and is rejected before repository or market-data work otherwise.

## Research basis

Investor.gov explains that allocation depends on the investor's time horizon and risk tolerance,
and that funds can still overlap or remain narrowly concentrated. FINRA likewise advises looking
through fund holdings and warns that correlated or single-security exposure can create concentration
risk. These principles require the feature to measure role and correlation rather than pick the
highest recent return.

Investor.gov also warns that past performance does not predict future results, that backtests are
hypothetical, and that fees, methodology, personal circumstances, and the benchmark all matter.
Therefore the one-year display is a normalized historical replay embedded in a longer 3/5/10-year
view, not a price prediction or promise.

Initial authoritative references:

- [Investor.gov: Asset Allocation and Diversification](https://www.investor.gov/introduction-investing/getting-started/asset-allocation)
- [Investor.gov: Performance Claims](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47)
- [Investor.gov: International Investing](https://www.investor.gov/introduction-investing/investing-basics/investment-products/international-investing)
- [FINRA: Concentration Risk](https://www.finra.org/investors/insights/concentration-risk)
- [Vanguard VTI](https://investor.vanguard.com/investment-products/etfs/profile/vti)
- [Vanguard VXUS](https://investor.vanguard.com/investment-products/etfs/profile/vxus)

## Rollout and receipts

1. Add contract, provider-range, analytics, qualification, and renderer tests before production
   code.
2. Run the complete Python, Node, Deno, type-check, and diff checks used by the owner-alert release.
3. Obtain an independent code review and resolve every material finding.
4. Deploy only the market-briefing gateway through the protected Supabase project path.
5. Run an `on-demand`, `dry_run: true` preview. It must be suppressed, show zero writes, show no
   Telegram message IDs, and leave the existing VTI plan unchanged.
6. Record the exact gateway version, request/run IDs, comparison coverage, rendered preview, table
   deltas, and plan state in the rollout evidence document.
7. Keep the existing Friday scheduled verification chain. A protected preview does not replace a
   real scheduled receipt check.

## Unchanging guardrails

- Personal owner-only system; friend invitations stay disabled.
- Suggestion-only; no brokerage credentials, endpoints, or execution authority.
- No automatic holding, watchlist, alert-rule, or recurring-plan mutation.
- No claim that a companion will make money or outperform in the next year.
- No selection from social sentiment, one return window, or provider peer lists alone.
- Missing, stale, conflicting, or inadequate evidence produces no qualified proposal.
- Every external write/send/deployment claim must be supported by its own receipt.
