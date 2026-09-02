# External Stock-Agent Ideas Review

**Reviewed:** 2026-09-02
**Purpose:** Preserve decisions from the owner's review request so a future v2 does not lose useful
ideas or repeat already-rejected investigations.

This note evaluates concepts shared from social-media reels. The reels are research inputs only.
They are not items to store in Telegram, not investment instructions, and not authorization to
install software, connect a broker, or place a trade.

## Decision summary

| Idea | Decision for this repository |
|---|---|
| Puter user-pays AI | Defer to a possible future multi-user web UI; no current integration |
| Autonomous Grok/Polymarket agent | Reject for real-money use |
| Rare-earth magnets thesis | Treat as a research question only; no automatic ticker/watchlist change |
| NoFx | Do not integrate; borrow selected safety patterns |
| HKUDS Vibe-Trading | Do not integrate wholesale; reconsider selected research methods in an isolated lab |
| Telegram reel inbox | Not requested and not planned |

## 1. Puter user-pays AI

Puter documents a user-pays model in which each signed-in user's account covers AI and cloud usage.
Users receive a monthly allowance and are prompted to upgrade if it is exhausted. This can isolate a
developer from aggregate model bills, but it shifts cost and dependency to users rather than making
AI computation unlimited or intrinsically free.

Source: [Puter User-Pays Model](https://docs.puter.com/user-pays-model/)

**Decision:** no current use. The personal stock agent already has working scheduled reasoning and a
deterministic Telegram/Supabase path. Adding a browser-side AI provider would add identity, privacy,
vendor, and model-consistency risks without solving an existing problem.

**Possible future use:** an optional explanatory assistant in a friend-facing web application after
multi-tenant authentication and row-level security exist. Puter code must never receive a Supabase
service-role key, Telegram bot token, brokerage credential, or direct authority to mutate portfolio
records.

## 2. Viral autonomous Grok/Polymarket agent

The shared performance story could not be validated from an audited wallet, reproducible repository,
or complete trade history. Circulating versions also use incompatible implementation details and
performance amounts. A survival-style objective can reward risk escalation, and a Kelly calculation
does not make uncalibrated probability estimates reliable.

Examples of conflicting circulating versions:
[Grok/no-API version](https://zamantika.com/Argona0x/status/2094582017530765759) and
[Claude/Rust/VPS version](https://www.reddit.com/r/borsavefon/comments/1r2augl/bir_yapay_zek%C3%A2ya_50_dolar_verdim_ve_ya_kendi/).

**Decision:** reject autonomous execution. This repository remains suggestion-only and cannot gain
broker, exchange, wallet, order, cancellation, or position-management authority.

**Lesson retained:** extraordinary performance claims require a full ledger, fees/slippage,
drawdowns, resolved and unresolved positions, risk-adjusted returns, and out-of-sample evidence.
Screenshots and ending balances are not enough.

## 3. Rare-earth magnets, MP Materials, and Neo Performance Materials

The supply-chain and policy theme is real, but that does not establish that either security is a good
purchase at its current valuation and entry price.

- A White House report says the US government invested $400 million in MP Materials, became its
  largest shareholder at 15%, and included a ten-year price floor and magnet offtake commitment.
  [White House 2025 highlights](https://www.whitehouse.gov/wp-content/uploads/2026/01/WHOSTP-2025-Wins.pdf)
- MP Materials reported Q2 2026 Magnetics revenue of $16.5 million, down 17% year over year, and a
  consolidated net loss of $20.3 million. The reel's roughly $21 million figure appears to refer to
  an earlier quarter. [MP Materials Q2 2026](https://investors.mpmaterials.com/investor-news/news-details/2026/MP-Materials-Reports-Second-Quarter-2026-Results/default.aspx)
- Neo reported Q2 2026 adjusted EBITDA of $57 million and raised full-year adjusted EBITDA guidance
  from $100-$110 million to $140-$150 million while its European magnet facility continued ramping.
  [Neo Q2 2026](https://www.neomaterials.com/neo-performance-materials-reports-second-quarter-2026-results/)

**Decision:** no product or watchlist change. If the owner later requests research on MP, Neo, or the
theme, run the existing on-demand equity-research process with fresh prices and filings. Separate:

1. industry demand and government policy;
2. company-specific operating execution;
3. valuation and dilution/customer-concentration risk; and
4. entry timing, invalidation, and portfolio concentration.

## 4. NoFx

NoFx is open source, but its documented first-run flow asks the user to fund an AI-fee wallet and an
exchange account before enabling an autopilot that trades every few minutes. Its model decisions are
constrained by a runtime that applies position, leverage, drawdown, cooldown, and safe-mode limits.

Source: [NoFx repository](https://github.com/NoFxAiOS/nofx)

**Decision:** do not install or integrate it. It is execution-oriented, introduces exchange and
wallet credentials, adds a large attack surface, and uses an AGPL-licensed codebase that would need a
license review before any reuse.

**Patterns worth implementing independently:**

- deterministic limits outside the model;
- fail-closed behavior when data or model calls fail;
- throttling and deduplication;
- an emergency halt for notifications/recommendations; and
- a complete audit record linking evidence, model conclusion, vetoes, and delivered output.

The current agent already has parts of this pattern. The remaining high-value gap is a deterministic
portfolio-risk checker that can veto an oversized suggestion independently of prompt compliance.

## 5. HKUDS Vibe-Trading

Vibe-Trading is a broad research and trading-agent framework. Its useful concepts include benchmark
comparison, Monte Carlo/bootstrap analysis, walk-forward validation, factor testing, and trade-journal
review. Its hundreds of published factors are research primitives, not hundreds of proven profitable
strategies.

Sources: [Vibe-Trading repository](https://github.com/HKUDS/Vibe-Trading) and
[security policy](https://github.com/HKUDS/Vibe-Trading/security).

Versions before 0.1.7 had published critical/high vulnerabilities involving unauthenticated access,
LLM-callable command execution, and broad file reads. Those advisories list 0.1.7 as the patched
version:
[command-execution advisory](https://github.com/HKUDS/Vibe-Trading/security/advisories/GHSA-jqmf-mx4f-hfr6),
[file-access advisory](https://github.com/HKUDS/Vibe-Trading/security/advisories/GHSA-5rmq-chc7-m22f), and
[authentication advisory](https://github.com/HKUDS/Vibe-Trading/security/advisories/GHSA-v2f8-6655-7grj).

**Decision:** do not import the whole platform. If research history eventually justifies deeper
testing, build or evaluate a separate read-only validation lab with sanitized inputs, no Telegram or
Supabase production secrets, no broker connector, no order tools, and a fresh version-specific
security review.

## Prioritized v2 implications

1. **Recurring-investment reminders:** deterministic Supabase schedule, no model call, no assumed
   fill, and actual purchases still use Telegram preview and Confirm.
2. **Deterministic risk veto:** independently calculate projected bucket exposure, single-position
   concentration, and stop-distance risk before any actionable Buy suggestion is sent. Missing or
   stale inputs fail closed.
3. **Validation lab only after enough history:** benchmark, walk-forward, bootstrap/Monte Carlo, and
   survivorship-bias checks on paper. Keep it isolated from the operational agent.
4. **Optional sanitized trade-journal review:** analyze behavior and process mistakes without broker
   credentials or write access.
5. **Friend-facing application:** first implement per-owner identity, RLS, onboarding, and tenant
   isolation. Only then reconsider user-pays AI as an optional presentation layer.

## Decisions that remain unchanged

- No autonomous real-money execution.
- No brokerage credentials or order endpoints in this repository.
- No external project is installed merely because it is popular or claims exceptional returns.
- Social-media claims are untrusted until checked against primary sources and current data.
- The model may propose; deterministic evidence, freshness, and risk gates may veto.
- Every trade is placed manually by the owner and recorded only after explicit confirmation.
