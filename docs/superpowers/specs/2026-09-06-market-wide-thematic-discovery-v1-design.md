# Market-Wide Thematic Discovery V1 Design

**Date:** 2026-09-06
**Status:** Approved by the owner on 2026-09-06
**Release:** Personal Stock Agent V1, checkpoint V1-C3
**Supersedes:** Any claim that the configured `market_scan` already proves market-wide runtime discovery
**Reviews:** GPT-6 Astra architecture and independent GPT-6 Astra red-team review

## 1. Decision

Personal Stock Agent V1 will not close checkpoint V1-C3 until the deployed agent can discover and
retain evidence-backed research candidates outside holdings, plans, radar, and the checked-in
watchlist. The September 8 scheduled receipts validate the operational integrity of the currently
deployed bounded pipeline. They do not prove this expanded discovery capability.

The implementation is a bounded market-wide discovery funnel. It searches supported sources and an
eligible U.S.-listed security reference across all sectors, explains exactly what was and was not
covered, maps events through themes and business roles to securities, and sends only a small,
evidence-bearing candidate packet into the existing Analyst, Checker, and deterministic policy
gateway.

“Market-wide” means cross-sector discovery over a dated eligible U.S.-listed reference and supported
source plan. It does not mean every security was deeply analyzed, every article on the internet was
read, or coverage was complete. `complete_market_coverage` remains `false` in every V1 receipt.

## 2. Why the current pipeline is insufficient

The approved 2026-09-04 V1 specification requires discovery beyond holdings and watchlists, but the
runtime does not yet satisfy that requirement:

- `config/settings.json` declares an all-sector U.S. market scan and seven screens, but production
  code does not consume `market_scan`.
- Pre-market uses fixed theme targets while later phases mostly use holdings, plans, and previously
  qualified candidates.
- Provider and target selection is round-robin. With the real configuration, an offline planner
  probe produced only one GDELT macro query plus Yahoo holding quotes; it did not give every seed
  theme a usable source.
- Theme stories without a ticker are discarded before theme or entity reasoning.
- Only the first sorted security ID is used when an item names several securities.
- White House, DOE, DoD, EIA, BLS, and BEA abstract queries are deliberately unsupported.
- Alpha Vantage theme queries have no configured translations in the scheduled plan.
- Configured zero-padded SEC CIKs are rejected by the planner, and the SEC parser retains filing
  metadata rather than bounded business-exposure passages.
- Dynamic theme proposal code is not connected to the production pipeline.
- Newly discovered names do not receive same-run server-owned quote and liquidity enrichment.
- Research ranking currently requires portfolio overlap. An ETF-only or empty portfolio can make
  overlap unknowable and therefore remove every candidate from the Analyst packet.
- Fixed 16-hour pre-market windows miss most long-weekend events, including the period before the
  September 8 session.
- The scheduled environment documentation omits several configured source domains and contradicts
  the Alpha Vantage configuration.

These are implementation gaps, not failures of the existing fail-closed safety controls. The new
design preserves those controls and creates a complete route into them.

## 3. Boundaries

The following requirements remain non-negotiable:

- Zero incremental dollars. No paid provider, trial, brokerage API, metered model API, or automatic
  paid fallback.
- Owner-only access. No public signup, friend access, or shared portfolio.
- Suggestion-only operation. The system cannot place, modify, or cancel a trade.
- Exactly one collector command per scheduled routine. Adaptive stages run inside that invocation.
- No arbitrary model browsing. Analyst and Checker receive one bounded persisted packet.
- External text is untrusted data and never instruction authority.
- Missing, stale, ambiguous, contradictory, quota-blocked, or unsupported evidence remains visible
  and cannot authorize Buy/Add/Reduce/Sell.
- No duplicate live scheduled run for verification.
- Learning can propose a reviewed change but cannot tune thresholds, weights, allocation, sources, or
  authority silently.
- Alert V3 remains disabled and shadow-only until its separate owner-controlled rollout.

Social sentiment, congressional trading, 13F digests, multi-user access, and brokerage execution are
not required to complete this design. Form 4 open-market purchase research may be implemented only
with transaction-code parsing; 13D/13G filings do not substitute for insider-purchase evidence.

## 4. Product states

Discovery, suitability, and action must use separate states.

### 4.1 Research state

An event or company may appear in research even when it is not investable.

- `observed`: a source-backed event exists independently of any ticker.
- `unresolved`: a theme or entity is plausible but identity or relationship evidence is incomplete.
- `resolved`: stable entity and security identities are known.
- `exposure_supported`: retained primary evidence supports the stated business role.
- `analysis_ready`: exposure, freshness, and minimum research inputs meet the bounded Analyst packet
  contract.
- `research_rejected`: a documented reason prevents further progression.

### 4.2 Suitability state

Suitability compares an analysis-ready security with the owner's current portfolio and constraints.

- `unknown`: required valuation, liquidity, overlap, or concentration evidence is unavailable.
- `eligible`: all required suitability inputs are current and pass policy.
- `vetoed`: one or more deterministic suitability gates fail.

An ETF-only or empty portfolio may leave overlap unknown. That blocks an actionable suggestion but
does not erase an exposure-supported research candidate.

### 4.3 Action authorization

Only the existing deterministic gateway may authorize an actionable suggestion. Research priority,
theme popularity, a model conclusion, price momentum, or a named government program cannot grant
action authority.

## 5. Architecture

```text
dated source cursors + eligible security reference + protected owner context
                                |
                                v
                  capability-aware collection plan
                                |
           +--------------------+--------------------+
           |                                         |
           v                                         v
  official/news/theme signals                 bounded broad screens
           |                                         |
           +--------------------+--------------------+
                                v
                   ticker-independent market events
                                |
                                v
                   theme and value-chain hypotheses
                                |
                                v
                entity/security resolution and queue
                                |
                                v
           primary exposure + quote/liquidity enrichment
                                |
                                v
              research priority and suitability status
                                |
                                v
             bounded packet -> Analyst -> Checker
                                |
                                v
                 deterministic policy and publication
```

The existing source, event, relationship, ranking, packet, request, quota, checkpoint, report, and
publication ledgers remain authoritative. New tables extend that ledger for reference versions,
adaptive stage tasks, exposure facts, theme memory, and follow-up research nominations. No vector
database, graph service, brokerage service, or new paid runtime is introduced.

## 6. One-invocation staged collection

Each scheduled routine still calls `python scripts/collect_market_intelligence.py` exactly once. That
process executes these durable stages:

1. Read protected holdings, plans, radar, prior research nominations, active theme episodes, source
   cursors, and the current reference-manifest identity.
2. Build and persist a complete bounded plan. Reserve safety-critical holding refreshes first, then
   broad discovery, then a maximum adaptive enrichment allowance.
3. Collect broad signals from enabled capability-matched sources.
4. Create and persist events before ticker resolution.
5. Classify themes and construct evidence-linked value-chain hypotheses.
6. Use the pre-reserved discovery budget to run bounded role/theme-search tasks against approved
   broad sources. These queries derive from the event and taxonomy roles, contain no permanent
   company list, and can return entity names that were absent from the original story and owner data.
7. Resolve zero, one, or several original or reverse-discovered entities and securities. Persist ambiguity rather than choosing the
   alphabetically first ticker.
8. Select and persist a bounded enrichment queue using only information already retained in the run.
9. Fetch primary issuer-exposure evidence and protected quotes for that exact queue.
10. Calculate research priority, suitability status, and action-gate inputs.
11. Build one bounded packet, persist one terminal completion, and return it to Analyst and Checker.

Each task has a stable identity derived from `(run_id, stage, capability, query_hash,
dependency_ids)`. It is persisted before outbound work. Retry resumes the same task and window; it
cannot silently select a new queue or duplicate an uncertain request. Unused adaptive reservations
close explicitly.

The model may submit at most a small configured number of evidence-linked research nominations for
the next normal run. Same-run model output cannot trigger a second browsing pass. An owner on-demand
request may prioritize a pending topic without imitating or consuming the scheduled run.

## 7. Coverage and time windows

The planner uses a capability matrix rather than provider/target round-robin assignment. Each
capability declares:

- a stable `capability_id`, with provider retained separately for aggregate quota accounting;
- query kind: `feed`, `theme_search`, `issuer_submissions`, `filing_document`, `series`, `screener`,
  `quote`, or `universe`;
- approved hosts and path patterns;
- required identifiers and credential presence;
- supported phases and themes;
- request, pagination, item, byte, and time budgets;
- authority and which claim types the source may establish;
- cache and retention class;
- parser version and last endpoint/terms review;
- requirement tier: `required_baseline` or `optional` for its scheduled scan cycle;
- current health: `enabled`, `configuration_missing`, `unsupported`, `degraded`, or `disabled`.

The registry is keyed by `capability_id`, not provider. This permits keyless EIA RSS and keyed EIA
statistics, or SEC universe and filing-document routes, to carry different credentials, budgets,
retention rules, and health while still sharing a provider quota ledger.

V1-C3 defines a versioned required baseline for each scan cycle. At minimum it includes a current
eligible-security reference and a keyless broad `theme_search` path that can perform reverse
discovery. A required capability counts only with a parsed, receipt-backed `success_empty` or
`success_nonempty` result for its due window. `failed`, `uncertain`, `deferred`, `disabled`,
`unsupported`, `configuration_missing`, and `quota_blocked` keep capability closure open. Optional
capability failures remain visible but do not falsify the required baseline.

Every enabled seed theme receives at least one supported discovery opportunity within its configured
scan cycle. Fairness prevents holdings, alphabetically early themes, or high-volume AI stories from
using every slot.

Per-source and per-query watermarks replace the fixed lookback as the sole coverage boundary. A
`completed_through` watermark means every page in the contiguous interval has been exhausted and
durably processed. A separate active-window and page/backlog cursor resumes newest-first truncated
results without skipping older unprocessed pages. Each new window starts before the last completed
watermark by a configured overlap, ends at the run's protected time, and is capped by a bounded
backlog policy. A fully exhausted empty window advances `completed_through` to its protected end; a
truncated or failed window does not. The receipt records:

- requested and completed cursor;
- overlap duration;
- oldest and newest accepted event time;
- pagination and item limits;
- backlog remaining;
- source failure or truncation;
- next eligible retry phase.

A source failure cannot be reported as `no_event`. A capped result cannot be described as fully
searched. Durable company exposure evidence has its own validity and reporting dates and does not
expire merely because the catalyst query window closes.

Value-chain expansion must include a real reverse-discovery operation. For example, a magnet award
that names only a private recipient creates bounded `theme_search` tasks for supplier, substitute,
and downstream roles. At least one keyless approved broad source must support those tasks in the
baseline plan. Returned organization names pass through the dated security reference and evidence
checks before enrichment; a taxonomy role by itself never implies a company or stock.

## 8. Source plan

The routes below are feasibility inputs. A source is not a deployed capability until normal runtime
network access, credential presence where required, parser behavior, retention, and receipts pass.

| Source | V1 role | Route and constraints |
|---|---|---|
| SEC issuer map | CIK, canonical name, ticker, exchange reference | `https://www.sec.gov/files/company_tickers.json`; official and keyless, but SEC does not guarantee complete accuracy or scope |
| SEC submissions | Filing history and issuer identity | `https://data.sec.gov/submissions/CIK##########.json`; descriptive User-Agent and conservative throttle required |
| SEC Company Facts | Shortlisted standardized facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`; not proof of product/segment exposure by itself |
| SEC filing documents | Primary contracts, capacity, backlog, revenue, risks | Fetch only shortlisted filings/exhibits; persist bounded relevant passages, hashes, locators, and links rather than complete filings |
| Nasdaq symbol directory | Complementary listing/type reference | Validate an HTTPS retrieval route and terms before activation; retain creation time and exclude tests, preferreds, warrants, rights, and units from common-stock suggestions |
| Federal Register | Policy, rules, notices, effective dates | Documented no-key `/api/v1/documents.json`; persist proposed/final/public-inspection status and pagination limits |
| White House | Fact sheets, presidential actions, statements | Bounded `/fact-sheets/`, `/presidential-actions/`, `/briefings-statements/`, and sitemap-derived URLs; do not invent an RSS or WordPress API |
| DOE | Energy programs, awards, policy, infrastructure | Current Energy News RSS `https://www.energy.gov/rss/energygov/2193718`; validate XML rather than trusting MIME |
| EIA news | Energy demand, supply, and market context | `todayinenergy.xml` and `press_rss.xml`; small feeds require watermarks and explicit backlog limits |
| EIA statistics | Specifically configured energy series | API v2 requires a free key; statistics remain `configuration_missing` until an existing owner-approved key and route are verified |
| DoD | Contracts, releases, industrial-base developments | Official Defense.gov RSS feeds with source-specific parsing and bounded links |
| GDELT | Broad event leads | Keyless discovery only; publisher identity and corroboration are required before stronger claims |
| Yahoo/yfinance | Quotes and feasibility-tested broad screens | Unofficial and personal-use constrained; every hidden cookie, crumb, redirect, and retry request must cross the transport/quota barrier |
| Alpha Vantage | Optional topic news or one-call market screen | Existing free key only, fixed documented topics, internal ceiling of 20 requests/day; zero-key baseline must still work |
| Finnhub | Ticker-specific enrichment | Never required for broad zero-key discovery; retention and derived-result terms must be represented by provider policy |
| FRED/BLS/BEA | Configured macro series | Series-specific collection only; generic theme text is unsupported |

Provider policy records storage class and retention behavior. Durable append-only storage keeps only
content and derived fields permitted by the applicable source terms. A provider whose terms are
incompatible with the ledger remains disabled for that claim type rather than weakening provenance.

## 9. Security reference and screening

The eligible suggestion universe is dated and explicit:

- U.S.-listed common shares;
- eligible U.S.-listed ADRs, typed separately;
- ETFs as a separate instrument class.

Private companies, foreign-only listings, OTC securities, preferred shares, warrants, rights, units,
and ambiguous instruments can appear as context but cannot silently become stock suggestions.
Security identity uses stable issuer and listing IDs, not ticker alone. It retains CIK, canonical and
former names, ticker aliases, exchange, class/type, validity interval, status, and source provenance.
Share classes, ADRs, and foreign ordinary shares are not duplicated as separate businesses without
an explicit listing relationship.

The current $5 price, $2 billion market-cap, and one-million average-volume floors remain action
filters. Unknown market cap or volume blocks suggestion eligibility. An economically important
smaller company may remain visible in Research with an exclusion reason.

The seven configured screens are activated only after a feasibility test proves bounded transport,
field semantics, attempt accounting, and repeatability. Each screen result is labeled a sampled
source output, never a scan of every listed security. If an unofficial screener cannot meet the
transport and usage boundary, the screen remains disabled and the report says so. The event/theme
path still provides cross-sector discovery through the zero-key official baseline.

## 10. Entity, theme, and value-chain reasoning

Resolution order is deterministic:

1. explicit validated CIK or security ID;
2. exact unique canonical or former issuer name;
3. reviewed alias for a product, subsidiary, or parent relationship;
4. unresolved or ambiguous state.

Short ordinary words and ticker-like text such as `AI`, `ON`, or `IT` never resolve merely because
they are uppercase. A private company is never mapped to a public parent or peer without evidence.

The versioned taxonomy contains roles and query packs rather than permanent buy lists. Initial packs
are:

- **Magnets:** mining, separation, metals/alloys, magnet manufacturing, recycling, substitutes, and
  downstream motors.
- **Aluminum/copper:** extraction, refining/smelting, recycling, semifabrication, conductors,
  cooling, and structures.
- **Data-center power:** generation, fuel, transmission, transformers/switchgear, power
  electronics, cooling, storage, and construction.
- **Nuclear/uranium:** mining, physical holdings, conversion, enrichment/SWU, HALEU, fuel
  fabrication, reactor equipment, operators, waste, and decommissioning.
- **Robotics:** OEMs, actuators/motors, gears/reducers, sensors/vision, controls, chips/compute,
  integrators, batteries, and magnets.

Graph edges record direction, role, geography, horizon, dependency, confidence, evidence, and
invalidation. Allowed roles include beneficiary, supplier, customer, substitute, competitor,
infrastructure provider, and input-cost risk. Every candidate requires its own evidence; evidence
for one named recipient does not qualify its peers.

Both upside and downside paths are retained. More data-center power demand may benefit equipment
suppliers while increasing a smelter's electricity costs. Rare-earth-free magnets may expand magnet
capacity while weakening a simple rare-earth-demand thesis. Grid bottlenecks may delay revenue even
when long-run demand is credible.

## 11. Evidence and materiality

The agent distinguishes these claims:

1. a speaker made a statement;
2. a forecast was attributed to that speaker;
3. a policy was proposed, signed, or became effective;
4. funding was announced, appropriated, awarded, or received;
5. an issuer has a verified business relationship or exposure;
6. that exposure is financially material;
7. the security has an acceptable valuation, price, portfolio, and risk setup.

Each later step requires new evidence. Official authorship establishes what the agency or issuer
said; it does not make a forecast true.

An exposure fact records subject entity/security, role, metric, value and unit when available,
period, passage text, passage locator, source item/hash, publication/reporting/effective dates,
status, confidence, and limitations. Missing revenue percentage remains unknown. A filing title or
accession alone is not exposure evidence.

Research priority uses event materiality, source independence, exposure support, recency, novelty,
and theme importance. It is not valuation or expected return. Suggestion eligibility additionally
requires protected quote identity/freshness, liquidity, market-cap floor, valuation evidence,
portfolio suitability, sizing, reward/risk, and every existing policy gate.

## 12. Theme memory and research nominations

Theme episodes retain first and last seen dates, revisions, supporting and opposing claims,
investigated entities, unanswered questions, invalidation conditions, next review date, and closure
reason. Stable identity survives paraphrased headlines; materially different information produces a
new revision.

Analyst or Checker may nominate a bounded follow-up with theme, entity/security if known, business
role, reason, evidence IDs, required evidence kind, priority, and expiry. The next normal collector
independently validates and resolves it. A nomination cannot alter the watchlist, holdings, plans,
policy, or alerts and cannot authorize an action.

## 13. Persistence changes

One additive migration extends the current immutable ledgers with:

- `market_reference_manifests`: source/version/hash/count/status for a reference refresh;
- `market_security_reference_revisions`: stable issuer/security/listing identity, type, aliases,
  validity, eligibility, exclusion reasons, and provenance;
- `market_discovery_stage_tasks`: persisted adaptive task identity, dependencies, cursor/window,
  budget, state, attempt lineage, and terminal result;
- `market_exposure_facts`: typed bounded evidence claims used by relationships and rankings;
- `market_theme_episode_revisions`: append-only theme state and evidence changes;
- `market_research_nominations`: bounded next-run follow-up lifecycle.

`market_event_relationships` remains the graph edge store. Existing `market_source_items`,
`market_events`, `market_candidate_rankings`, and `market_evidence_packets` remain the run ledger.
The new tables receive least-privilege service writes, owner dashboard reads only where needed,
append-only or controlled lifecycle enforcement, release-reader coverage, encrypted export/restore
coverage, and bounded indexes.

Reference refresh stores one manifest and changed revisions rather than duplicating the complete
universe daily. Raw full filings and pages are transient. Only bounded relevant passages, hashes,
locators, and source links persist. Storage growth must be measured against current free-tier
headroom before deployment.

## 14. Packet, report, and dashboard contract

The packet includes two bounded lanes:

- `research_candidates`: exposure-supported or explicitly unresolved ideas with research state,
  theme, roles, evidence URLs/dates/authority, uncertainty, and rejection or missing reasons;
- `action_candidates`: the subset with complete suitability and action-gate inputs.

The Analyst sees research candidates even when action is blocked. The Checker and gateway cannot
upgrade a research-only candidate into an actionable suggestion. Packet evidence includes source
reference, authority, published/retrieved/reporting/effective dates, claim type, and normalized text,
while retaining the existing item IDs and hashes.

Reports and the private dashboard show:

- sources and themes planned, attempted, succeeded, failed, truncated, or configuration-missing;
- universe/reference version and enabled instrument scope;
- new companies found outside holdings/watchlist;
- research, wait, insufficient-evidence, and action states;
- relationship path and primary exposure evidence;
- opposing evidence and invalidation;
- exclusion and missing-data reasons;
- backlog and next review state.

`No candidate` is distinct from `source unavailable`, `coverage truncated`, and `research unresolved`.
Telegram remains concise and quiet; the dashboard holds the deeper evidence.

## 15. Capacity limits

Planning treats the complete run as one shared budget. It cannot sum every configured provider
maximum independently. The implementation must remain inside the existing hard ceilings unless a
reviewed additive contract change lowers or safely extends them:

- 100 source receipts;
- 500 source items;
- 100 events;
- 500 relationships;
- 100 rankings;
- 32 KiB coverage object;
- 96 KiB Analyst packet and collector output;
- 1 MiB terminal persistence payload.

Deterministic thinning preserves official and adverse evidence, candidate rejection reasons,
source/theme coverage, task deferrals, and uncertainty. Holding quote capacity is reserved before
broad discovery. Budget exhaustion produces deferred tasks and a bounded receipt rather than hidden
drops or a paid fallback.

## 16. Acceptance scenarios

All thematic fixtures begin with their relevant public tickers absent from holdings, plans, radar,
watchlist, prior candidates, and hardcoded query terms.

1. A magnet announcement naming only a private company is retained as an event. The private company
   stays private. Bounded reverse-discovery tasks find the fixture's previously unseen supplier,
   substitute, and downstream names, and only the expected public company with primary exposure
   evidence reaches `exposure_supported`.
2. An aluminum/data-center event records conductors, cooling, and structures as possible demand paths
   and electricity cost as an adverse smelting path. Unsupported claims that data centers consume
   most aluminum or that gold implies aluminum appreciation are rejected.
3. A data-center electricity event distinguishes generation, grid equipment, storage, and cooling.
   Geography, contract status, capacity, backlog, and timing determine whether a company advances.
4. A nuclear event keeps uranium mining, physical holdings, conversion, enrichment/SWU, fuel,
   reactor equipment, and utilities distinct.
5. A robotics forecast retains speaker attribution and uncertainty. OEMs, actuators, sensors,
   reducers, compute, and integrators require independent product/revenue/customer evidence; no
   company is assumed to supply Optimus.
6. A held-out non-priority-sector event finds an eligible company not present in any initial fixture
   list. Renaming fixture companies and tickers preserves behavior.
7. No-ticker stories, several tickers, private/public name collisions, zero-padded CIKs, dual share
   classes, ticker changes, ADRs, and ordinary-word collisions resolve or fail predictably.
8. Only qualifying Form 4 purchase transactions may count toward insider-purchase research. Grants,
   exercises, gifts, amendments, duplicates, and 13D/13G stubs do not.
9. Empty and ETF-only portfolios still receive research candidates. Missing overlap or valuation
   prevents action authorization.
10. A long weekend is covered from saved watermarks with bounded overlap; truncation and remaining
    backlog appear in the receipt. A newest-first truncated page cannot advance the completed-window
    watermark, and a fully exhausted empty window does advance it.
11. A source outage cannot produce a `no_event` result for that source/theme. Complete source failure
    leaves the run explicitly incomplete.
12. A crash between stages or between transport attempt and response resumes the same saved tasks
    without duplicate provider calls or publication.
13. Hostile source instructions, spoofed issuer URLs, caller-supplied prices, exposure, or overlap,
    and syndicated duplicates cannot become authority.
14. Full fixtures stay inside request, item, relationship, packet, persistence, time, and free-source
    budgets, retaining explicit deferred work when a limit is reached.
15. A normal scheduled production receipt records the source plan, reference version, stage tasks,
    coverage, outside-watchlist discovery or an honest no-candidate result, packet, policy result,
    and original Telegram delivery or suppression.
16. One versioned NYSE calendar source drives Python policy, gateway session logic, and dashboard
    freshness through the [official published 2026–2028 holiday and early-close schedule](https://www.nyse.com/trade/hours-calendars). Generated
    runtime copies match it exactly, and dates after maintained coverage fail closed.

Acceptance never requires a positive stock suggestion or a real market event on a particular day.
It requires correct research, correct unresolved states, and an auditable chain.

## 17. Rollout gates

1. Preserve and reconcile the September 8 pre-market, intraday, and post-market receipts plus the
   first Friday weekly audit. These are operational evidence for the current deployment only.
2. Pass local source-feasibility fixtures and a real-config plan audit without provider transport.
3. Implement and verify additive schema, contract, export, restore, RLS, and release-reader coverage.
4. Pass all thematic, generalization, ambiguity, budget, security, and crash-recovery scenarios.
5. Extend and synchronize the maintained NYSE calendar through the official published 2028 schedule.
6. Run the full repository gate and independent GPT-6 Astra review on one exact candidate.
7. Merge through protected main and exact-main CI.
8. Apply the additive migration and publish changed backend components only through the protected
   multi-component release/recovery path. Do not reuse the Site-only path for backend changes.
9. Read back runtime domains, credential presence without values, source capability health, deployed
   bytes, and database state.
10. Publish the exact reviewed owner Site, recheck sole-owner access and live asset hashes, and retain
    the prior Site as rollback.
11. Observe normal scheduled shadow discovery. Never trigger a duplicate run to create a candidate.
12. Close V1-C3 only after a production receipt proves the new source plan and graph path, or an
    honestly empty outcome where every due `required_baseline` capability has a parsed,
    receipt-backed `success_empty` result and no fabricated coverage. An all-failed, disabled,
    unsupported, deferred, uncertain, or quota-blocked baseline cannot close V1-C3.
13. Close the complete V1 goal only when V1-C2 through V1-C6 and this V1-C3 capability gate are all
    complete.

Alert V3 shadow review and the later owner-approved `stop_breach` canary follow discovery validation.
The optional 15-minute monitor and automatic policy learning are not part of this release.
