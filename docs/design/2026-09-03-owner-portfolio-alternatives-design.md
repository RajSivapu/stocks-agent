# Owner Portfolio Alternatives Review

**Date:** 2026-09-03
**Status:** Deployed behind the protected gateway with a write-free, send-free rollout proof.
**Scope:** Personal, owner-only, suggestion-only research.

## Decision

The agent will not limit itself to maintaining current holdings. On explicit request and on the
first pre-market brief of each month, it may compare an existing holding or active recurring plan
with a bounded set of credible alternatives. It cannot modify a holding, switch a recurring plan,
advance a due date, or access a brokerage.

The current recurring baseline is VTI because that is the owner's recorded plan. This is not a
claim that VTI is permanently optimal. The monthly review asks whether another fund changes cost,
coverage, concentration, diversification, drawdown, or forward role fit enough to merit owner
research.

## Comparison groups

- **Like-for-like:** VTI against broad-U.S. total-market funds such as ITOT and SCHB.
- **Tilt:** VOO is evaluated as U.S. large-cap exposure rather than called an interchangeable total-
  market fund.
- **Diversifier:** VT changes the portfolio to global equity exposure; VXUS adds non-U.S. exposure
  beside a U.S. holding.
- **Peer:** an individual stock such as CENX is compared only with businesses whose revenue drivers,
  cyclicality, balance sheet, cash flow, valuation, and risk are genuinely comparable. Finnhub's
  peer endpoint supplies candidates, not a conclusion.

The initial candidate set is deliberately small. Adding a fund to the research set does not make it
eligible for an owner recurring plan; plan eligibility remains separately reviewed policy.

The structured relationship vocabulary is `like_for_like`, `tilt`, `diversifier`, and `peer` so a
large-cap concentration choice such as VOO is not mislabeled as a total-market substitute.

## Historical method

The model never supplies return numbers. The gateway fetches one year of adjusted daily history for
both symbols, synchronizes common dates, and computes:

1. same-window lump-sum total return;
2. a normalized equal-dollar contribution on the first synchronized observation in each of the last
   twelve calendar months;
3. relative return in percentage points; and
4. max drawdown over the synchronized window.

At least 240 synchronized sessions and eleven monthly observations are required. Missing, malformed,
or inadequate histories yield `insufficient_history` or `missing_history`, with no invented result.
The hypothetical monthly series does not reproduce the owner's exact execution prices, taxes,
cash-flow dates, or trading costs. It is labeled as hypothetical and not a forecast.

## Forward-looking method

Each alternative remains an ordinary candidate with its own current evidence, Analyst record,
Checker record, and gateway policy result. The comparison adds only one qualitative view:
`stronger`, `similar`, `weaker`, or `insufficient`. Its evidence IDs must belong to the alternative
candidate and be `fresh` or justified `fallback` evidence.

If the alternative evaluation is not policy-approved or its cited evidence is unavailable, the
gateway replaces the submitted forward view with `insufficient`. A stronger view means “stronger
candidate for owner research in this role,” not a probability of profit, price prediction, or
instruction to replace the holding.

## Message contract

The compact comparison block shows:

- current ticker, alternative, and relationship;
- matched equal-monthly historical result;
- max drawdown for both;
- evidence-linked forward view and reason;
- explicit confirmation that the holding/plan was unchanged; and
- “Hypothetical history is not a forecast.”

The block is allowed only in pre-market and on-demand output. It is excluded from intraday and
post-market notifications to avoid turning slow, strategic research into alert noise. On-demand
output remains suppressed from Telegram.

## Research basis

- Vanguard describes VTI as tracking the CRSP US Total Market Index and VOO as tracking the S&P 500;
  both list a 0.03% expense ratio. This supports treating VOO as a different coverage choice, not a
  duplicate: [VTI](https://investor.vanguard.com/investment-products/etfs/profile/vti) and
  [VOO](https://investor.vanguard.com/investment-products/etfs/profile/voo).
- Vanguard describes VT as global all-cap exposure and VXUS as global ex-U.S. exposure, supporting
  their diversifier labels: [VT](https://investor.vanguard.com/investment-products/etfs/profile/vt)
  and [VXUS](https://investor.vanguard.com/investment-products/etfs/profile/vxus).
- ITOT and SCHB publish broad-U.S.-market objectives and 0.03% expense ratios:
  [iShares ITOT](https://www.ishares.com/us/products/239724/ishares-core-sp-total-us-stock-market-etf)
  and [Schwab SCHB](https://www.schwabassetmanagement.com/products/schb).
- Investor.gov defines dollar-cost averaging as equal investments at regular intervals and warns
  that back-tested performance is hypothetical and past performance does not predict future
  results: [dollar-cost averaging](https://www.investor.gov/introduction-investing/investing-basics/glossary/dollar-cost-averaging)
  and [performance claims](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47).
- Damodaran's relative-valuation material explains that peer differences still need to be controlled
  for fundamentals: [relative valuation](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/controldifferences.htm).

## Guardrails

- owner-only; friend invitations remain disabled;
- no brokerage secrets, tools, links, or order authority;
- no automatic plan/holding/watchlist mutation;
- no “best fund” or future-profit guarantee from one year of history;
- no social-sentiment-only comparison; and
- every write/send claim must come from a gateway or Telegram receipt.
