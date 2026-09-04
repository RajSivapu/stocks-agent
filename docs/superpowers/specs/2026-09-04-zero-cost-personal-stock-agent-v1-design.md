# Zero-Cost Personal Stock Agent V1 Design

**Date:** 2026-09-04
**Status:** Written specification awaiting owner approval
**Release:** Personal Stock Agent V1
**Release control:** `PROJECT_STATUS.md`

## 1. Release definition

Personal Stock Agent V1 is one owner-only, suggestion-only research and monitoring system. It
collects a bounded cross-market evidence set from approved free sources, discovers themes and
companies, compares qualified candidates with the owner's holdings and plans, records every
material stage with receipts, and presents the result through a private dashboard and deliberately
quiet Telegram delivery.

V1 is complete only when checkpoints V1-C1 through V1-C6 are complete. Missing approved scope is
not deferred to a V1.1 label. A checkpoint may remain blocked behind owner approval, provider
availability, or a protected production gate without weakening the release definition.

## 2. Non-negotiable boundaries

- Zero incremental dollars: no paid provider, premium endpoint, paid trial, credit-card signup,
  metered runtime model API, or automatic upgrade.
- No brokerage integration, credentials, order route, or trade authority. Every conclusion is
  decision support; the owner places any trade manually.
- One pre-created owner account; public sign-up and friend invitations remain disabled.
- External articles, filings, feeds, and social posts are untrusted data, never instructions.
- No receipt means no claim that data was persisted, a report was published, Telegram accepted a
  message, the owner viewed it, or production was deployed.
- Do not start a duplicate live scheduled run merely to inspect output.
- Missing, stale, conflicting, quota-blocked, or insufficient evidence fails closed.
- No guaranteed-return language, deterministic future price, or uncalibrated probability of profit.
  Forward-looking output uses evidence-backed bear, base, and bull scenarios.
- Learning may produce review proposals but cannot silently alter policy weights, thresholds,
  source priority, alert routing, sizing, or authority.

## 3. Decisions and supersession

This specification retains the reliability gateway, Analyst and Checker contracts, deterministic
policy, portfolio recordkeeping, owner alert v3 shadow path, private dashboard security boundary,
and Midnight Navy/Warm Gold design system already approved in the repository.

It supersedes these narrower V1 assumptions only:

1. Market intelligence is no longer limited to holdings, watchlists, or preselected alternatives.
2. The dashboard's seven-page taxonomy is reorganized into five primary surfaces: **Portfolio**,
   **Ideas**, **Intelligence**, **Reports**, and **System / Receipts**. Existing Today, Companion,
   Alerts, and Runs components become views within those surfaces rather than discarded work.
3. Structured market events, evidence links, candidate relationships, and complete reports must be
   persisted append-only so the dashboard never reconstructs research from Telegram prose.
4. Weekly, monthly, and theme reports are stored once; Telegram receives only a short summary and
   an authenticated private dashboard link.

Massive, Benzinga, Alpaca, and every paid or commercially gated provider are outside V1.

## 4. System architecture

```text
approved free sources
        |
        v
deterministic collectors -> cache/quota ledger -> normalization/deduplication
        |                                             |
        +---------------- source receipts ------------+
                              |
                              v
                 event/theme/entity relationship graph
                              |
                              v
                deterministic candidate ranking
                              |
                bounded evidence packets only
                              v
                     Analyst -> Checker
                              |
                              v
                  deterministic policy gateway
                    /          |           \
             append-only    dashboard    Telegram policy
              reports       read API     (usually silent)
```

The deterministic pipeline owns collection, parsing, timestamps, caching, quotas, deduplication,
entity/theme mapping, candidate ranking, numerical analytics, and policy enforcement. Claude
receives only bounded evidence packets and produces structured analysis; it does not browse freely,
select providers, claim exhaustive coverage, change policy, or publish directly.

Browser requests remain GET-only and never call providers, models, routines, Telegram, or financial
mutation paths. The existing authenticated Telegram confirmation flow remains the only owner-facing
portfolio record mutation path and is not expanded into trade execution.

## 5. Free intelligence ingestion

### 5.1 Provider roles

| Source | V1 purpose | Required constraint |
|---|---|---|
| GDELT | No-key broad global event radar | Discovery only until corroborated; bounded time windows and result counts |
| Alpha Vantage | Topic-oriented financial news | Existing free key; configurable hard daily budget below 25 requests/day |
| Finnhub | Company news, fundamentals, earnings, cross-checking | Existing free key; bounded per-run calls and cached reuse |
| Yahoo | Quotes, adjusted history, locally computed technicals | Preserve timestamp/session validation and actionable freshness vetoes |
| SEC EDGAR | Filings and issuer disclosures | Official evidence; identify filing, accession, issuer, and retrieval time |
| Federal Register, White House, DOE, DoD, EIA | Policy, energy, industrial, and contract evidence | Official-source allowlist and document metadata |
| Fed/FRED, BLS, BEA | Macro releases and series | Release/observation dates remain distinct; revisable data is labeled |
| Reddit/social | Best-effort hypothesis discovery | Never authoritative, sufficient, or a standalone signal |

Collectors implement a common adapter contract: source, query purpose, requested window, retrieval
time, upstream item ID/URL, published/effective time, normalized content hash, status, quota cost,
and bounded error. Provider-specific raw payloads are not passed wholesale to the model or browser.

### 5.2 Budget, caching, and coverage

- A configuration-owned daily quota ledger reserves capacity for scheduled routines. Alpha Vantage
  must be configured to at most 20 requests/day, leaving at least five requests of headroom below
  its stated 25/day free limit. Per-phase budgets may be lower.
- Finnhub and other rate-limited sources use explicit per-run ceilings, response caching, exponential
  backoff, and no automatic paid fallback.
- Cache keys include provider, normalized query, window, and schema version. Revalidation never
  erases the original source receipt.
- Exact URL/upstream-ID matches and normalized content hashes remove duplicates. Near-duplicate
  clustering retains a canonical item plus all supporting source references.
- Every run records sources planned and queried, requests consumed/remaining when known, returned and
  accepted item counts, duplicates, drops by reason, failures, and explicit coverage limitations.
- User-facing copy says which bounded sources and windows were checked; it never says “all news” or
  implies complete market coverage.

### 5.3 Scheduled collection

- The first pre-market run on each trading day performs the full seed-domain discovery sweep.
- Intraday runs collect bounded deltas for holdings, active plans, qualified candidates, urgent
  policy/thesis events, and newly emerging high-materiality themes. A quiet result stays silent.
- Post-market reconciles the day's events, closes receipt gaps, and prepares persisted research for
  later periodic reports; it does not resend the morning report.
- Weekly and monthly jobs synthesize already persisted evidence plus explicitly budgeted updates.
- On-demand research uses a separate configured quota allocation and cannot consume the reserve
  required for the next scheduled cycle or imitate a scheduled production run.

The initial Alpha Vantage allocation within the 20-request internal daily ceiling is at most eight
pre-market, four intraday, four post-market, two on-demand, and two unallocated reserve requests.
Unused allocations may be reused only by a later scheduled phase, never by an automatic paid
fallback. Any cadence or allocation change is versioned configuration and requires review.

## 6. Discovery brain

### 6.1 Theme coverage

Every full discovery cycle evaluates these seed domains without requiring a predefined ticker:

- macro and policy;
- technology, AI, and semiconductors;
- energy, nuclear, and grid infrastructure;
- industrial infrastructure;
- critical minerals and magnets;
- healthcare;
- consumer;
- defense, trade, and geopolitics;
- earnings and M&A; and
- dynamically surfaced themes that meet novelty and evidence thresholds.

The theme catalog is configuration and taxonomy, not a watchlist. A new theme may be proposed from
clustered events, but it receives a stable ID and evidence trail before it can influence ranking.

### 6.2 Event-to-candidate chain

The pipeline records the following typed links:

`market event -> theme -> value-chain role -> entity -> security/ETF candidate`

Candidates may be direct beneficiaries/exposures or second-order suppliers, customers, substitutes,
infrastructure providers, or risk bearers. A relationship label is a hypothesis until supported by
exposure evidence.

A candidate cannot advance to Analyst review unless at least one current authoritative item supports
material exposure through a filing, named contract, backlog, revenue segment, production capacity,
official fund holdings/mandate, or equivalent official disclosure. Mere co-mention, social interest,
price momentum, provider peer lists, or model familiarity is insufficient.

### 6.3 Deterministic ranking and model packet

Ranking is reproducible and records component scores rather than a single opaque number. Components
cover event materiality, source authority and corroboration, exposure strength, recency, portfolio
relevance, tradability/liquidity, and duplication/concentration penalty. Missing components do not
receive neutral invented values.

Only the top 12 ranked candidates enter a model run. Each candidate carries at most eight evidence
items, each bounded to 2,000 normalized characters, and the complete serialized packet is capped at
96 KiB. Hitting a bound is recorded as a coverage limitation, not silently truncated into a claim
of completeness. Each candidate gets separate structured Analyst and Checker records. The gateway then accepts,
downgrades, vetoes, or marks insufficient using a versioned deterministic policy. Checker completion
is not represented as an independent model when it is another pass by the same model.

## 7. Personal comparison brain

Every policy-eligible candidate is compared with current holdings, active plans, and relevant
benchmarks. CENX and VTI are explicit initial comparison anchors when they remain in the owner's
current records; the database, not this specification, determines whether a holding or plan is
active at run time.

### 7.1 Individual positions and ideas

Compare thesis exposure, theme overlap, position concentration, liquidity, volatility/drawdown,
valuation evidence, catalyst horizon, invalidation, and bear/base/bull scenarios. A candidate may be
rejected because it duplicates an owned exposure or worsens concentration even when its standalone
case is credible.

### 7.2 Recurring-investment companion analysis

For long-term funds and recurring plans, compare diversification, holdings overlap, expense ratio,
valuation context, concentration, liquidity, correlation, drawdowns, and synchronized 3/5/10-year
history when complete. Historical replays remain hypothetical and past-tense.

VTI remains the recorded recurring baseline until the owner explicitly changes the plan. Known role
policy remains gateway-owned: like-for-like substitutes are not companions; diversifiers, tilts,
replacements, and concentrated satellites are labeled distinctly. No comparison mutates a holding,
watchlist, alert, or plan.

## 8. Persistence and receipts

Persistence is append-only for intelligence and published research. Corrections create a new version
or superseding record; they do not rewrite the historical claim.

Required logical records are:

- collection run and per-source request receipts;
- normalized source items and immutable content/source hashes;
- duplicate/drop decisions with reason codes;
- market events, theme/entity/value-chain links, and candidate rankings;
- bounded evidence packets and source membership;
- Analyst, Checker, and deterministic gateway decisions with policy versions;
- personal comparison and scenario results;
- reports, report versions, sections, and source/content hashes;
- publication attempts, suppression, Telegram acceptance IDs, and dashboard release references; and
- later outcome grades and missed-event coverage evaluations.

Identifiers connect the full chain without storing hidden reasoning. Stored external text is bounded,
escaped on output, and never executed. Source links must be allowlisted HTTPS URLs. Content hashes
prove byte identity, not truth or owner viewing.

Receipt language is exact: a database receipt supports “persisted”; a Telegram message ID supports
“accepted by Telegram”; an authenticated owner callback supports “owner action received”; a private
host deployment/version receipt plus owner and denial canaries supports “deployed privately.”

## 9. Reports and delivery

| Product | Persistence | Telegram behavior |
|---|---|---|
| Urgent alert v3 | Alert event and publication receipt | Only actionable risk or material thesis change; concise, receipt-linked |
| Morning brief | Completed report/run receipt | Concise portfolio and market summary |
| Weekly report | One immutable report version | Short summary plus authenticated private dashboard link |
| Monthly report | One immutable report version | Short summary plus authenticated private dashboard link |
| Theme report | One immutable report version per requested/generated edition | Short summary/link only when policy says owner attention is useful |
| No-trigger intraday | Suppression/quiet-run receipt where applicable | Silent |

The detailed report is stored once. Telegram must not copy the full report into multiple messages or
create a second report record. Links target the exact authenticated report version and reveal no
owner data before sign-in. Publication is idempotent by report version and channel.

## 10. Owner dashboard

The existing React/TypeScript/Vite application, private Codex Site, Supabase owner JWT verification,
exact-origin CORS, and dedicated direct-SELECT database role remain the foundation. The API exposes
versioned, bounded, redacted view models only; there are no browser-visible mutation routes.

The five primary surfaces are:

1. **Portfolio** — Today overview, holdings, plans, CENX/VTI context, concentration, performance,
   companion analysis, and complete/stale/unavailable price semantics.
2. **Ideas** — ranked candidates, direct/second-order relationship, exposure evidence, Analyst and
   Checker state, policy outcome, scenarios, invalidation, and eligible outcome grades.
3. **Intelligence** — events, themes, value-chain map, source coverage, emerging themes, material
   alerts, evidence links, and coverage limitations.
4. **Reports** — immutable morning, weekly, monthly, and theme report versions plus linked alert and
   publication timelines.
5. **System / Receipts** — collection quotas/failures, run chains, policy/shadow state, source and
   content hashes, writes, suppression, Telegram acceptance, deployment versions, and permanent
   owner-only/suggestion-only boundaries.

Dark mode uses Midnight Navy, Deep Midnight, Navy, Warm White, Blue Gray, and Warm Gold. Light mode
uses Warm Pearl, Midnight Navy, Soft White, Ink Navy, Slate, and restrained Warm Gold. System is the
initial theme setting; manual Light/Dark selection stores only the theme name. WCAG 2.2 AA,
responsive behavior down to 320 CSS pixels, keyboard use, reduced motion, and status labels beyond
color remain required.

## 11. Security and failure behavior

- Dashboard financial access requires a valid owner JWT; malformed owner configuration returns 503
  before database access. The direct database login has only allowlisted column-level SELECT and no
  RPC, write, ownership, DDL, provider, Telegram, or routine authority.
- Provider and model secrets stay server-side and out of frontend bundles, reports, logs, and source
  receipts. Logs redact authorization and identity values.
- External text and URLs pass through normalization, length bounds, neutral data envelopes, HTML
  escaping, and link allowlists. Prompt templates state that evidence is untrusted and cannot change
  the task or system policy.
- A partial provider failure may produce a coverage report but cannot be silently converted to a
  complete discovery result. A required-source or freshness failure vetoes affected candidates.
- Quota exhaustion stops that source, records the state, and continues only where remaining evidence
  meets policy; it never switches to a paid endpoint.
- Report, database, Telegram, and deployment failures remain distinct. Retry keys prevent duplicate
  persistence or delivery, and unknown external outcomes are recorded as unknown until reconciled.
- Rollback disables new schedulers/policies and deployed surfaces without deleting append-only
  evidence. Material data cleanup requires separate owner approval.

## 12. Learning boundary

Learning computes deterministic outcome and process observations: eligible 5/21/63-session grades,
benchmark-relative results, false-positive/noise rates, source failures, evidence gaps, and
missed-event coverage found through later authoritative evidence. It records sample size, horizon,
policy version, and known limitations.

Learning may draft a proposed policy change for owner review. No job or model may apply that change,
increase authority, add a paid source, alter a portfolio record, or present in-sample improvement as
proof of future performance.

## 13. Verification strategy

- Test-first implementation for adapters, normalization, budgets, caching, deduplication, graph
  links, ranking, contracts, policy, persistence, report rendering, dashboard states, and delivery.
- Deterministic fixtures cover hostile content, duplicate/near-duplicate items, revised macro data,
  missing timestamps, stale quotes, quota exhaustion, provider disagreement, second-order exposure,
  no-qualified-candidate, and no-trigger silence.
- Migration tests prove append-only constraints, uniqueness/idempotency, hash fields, and least
  privilege. Rollback tests prove new components can be disabled without erasing evidence.
- Repository verification includes Python, Node, Deno, contract, typecheck, lint, supply-chain,
  production build, bundle-secret scan, accessibility, and browser suites.
- An independent exact-head review must challenge security, source claims, policy bypass, prompt
  injection, persistence integrity, dashboard disclosure, quota behavior, and receipt sufficiency.
- Protected dry runs are write-free and send-free and must report zero writes and message IDs.
- Production deployment uses protected scripts, exact-source parity, owner/anonymous/non-owner
  canaries, provider health checks, and database/Telegram/deployment receipts. Existing scheduled
  runs supply live verification; no duplicate live run is created for inspection.

## 14. Checkpoints and acceptance gates

### V1-C1 — Release control

- This approved written specification is committed.
- Root `PROJECT_STATUS.md` is canonical and includes release status, decisions, owner actions,
  blockers, receipts, and commit/date references.
- The owner approves the written spec before the writing-plans workflow begins.

### V1-C2 — Free intelligence ingestion

- All approved provider adapters, budgets, cache, normalization, deduplication, hashes, and source
  receipts are implemented and tested.
- Paid/commercial providers are absent from V1 runtime configuration.
- Coverage and quota limitations are observable and truthful.

### V1-C3 — Market-discovery brain

- Required seed domains and dynamic themes flow through event, theme, value-chain, entity, exposure,
  ranking, Analyst, Checker, and gateway stages.
- Direct and second-order candidates cannot advance without exposure evidence.
- Full deterministic and hostile-input tests pass.

### V1-C4 — Personal comparison brain

- Qualified candidates compare with live owner records, including CENX and VTI when applicable.
- Recurring-investment analysis covers the approved portfolio factors and scenario language.
- No comparison path mutates holdings, plans, watchlists, alerts, or brokerage state.

### V1-C5 — Reports, dashboard, and delivery

- Append-only reports and event chains are persisted with hashes and receipts.
- All five dashboard surfaces show real receipt-derived data in both visual themes and supported
  responsive states.
- Telegram implements urgent-only v3, concise morning, summary/link reports, idempotency, and silent
  no-trigger behavior.

### V1-C6 — Independent review and protected rollout

- Exact-head independent review has no unresolved Critical or Important finding.
- Full local and CI verification passes at the exact candidate commit.
- Protected deployment, dry-run preview, source parity, owner/denial canaries, and rollback checks
  pass.
- Production claims are entered in `PROJECT_STATUS.md` only with receipt references; scheduled
  verification confirms the live behavior without a duplicate run.

## 15. Explicit exclusions

- Brokerage or exchange connectivity and any autonomous trading.
- Multi-owner tenancy, public registration, or friend invitations.
- Paid data/model services or automatic upgrades.
- Exhaustive-news claims, social-only signals, hidden self-tuning, or return guarantees.
- Arbitrary web browsing by the runtime model.
- Destructive cleanup of legacy production data as part of this release.
- A V1.1 bucket used to omit any requirement approved in this specification.
