# Task 6 report — Bounded adaptive enrichment and primary exposure facts

Status: DONE

Base: `284ad026db206f95e532d708ddd241430ca87b8f`

## Implemented behavior

- Added immutable typed `EnrichmentRequest` and `SelectionManifest` contracts. Selection is
  deterministic under input permutation, rotates across themes, retains adverse paths, deduplicates
  issuer filing work while retaining every hypothesis, caps selection at four issuers and 100 run
  tasks, records stable deferral reasons, and always carries `execution_allowed=false`.
- Replaced the initial all-GDELT adaptive allocation with exact phase envelopes: pre-market
  `3 SEC submissions + 3 SEC documents + 4 Yahoo quotes + 2 GDELT reverse`, intraday and on-demand
  `1+1+1+1`, and post-market `2+2+2+2`. Holding and owner-plan quotes consume their reserved slots
  first. Existing persisted Task 5 plans retain their original compatibility behavior.
- Seals holding, initial, and filing-document manifests plus every exact child descriptor before
  transport. Restart reads and validates these immutable records from the protected context and
  replays frozen request identities. Planned/no-attempt, uncertain attempt barriers, completed
  checkpoints, response-loss replay, terminal reuse, empty selections, and stable deferrals are
  covered without duplicate transport.
- SEC submissions bind the requested issuer to the exact `data.sec.gov` response CIK and validate
  equal typed columns, unique accessions, supported forms, safe filenames, and exact issuer-CIK
  archive URLs. Filing-agent accession prefixes are valid. Arbitrary URLs, encoded separators,
  traversal, credentials, ports, queries, fragments, redirects, malformed response identity, and
  conflicting duplicate accessions fail closed. Only a primary document present in the validated
  issuer row is selected; historical pages and exhibits remain unresolved unless a later bounded
  contract supplies their exact membership.
- Filing parsing accepts only complete direct HTML, XHTML, or iXBRL documents up to 2,000,000
  bytes. It rejects PDF, SGML, unsupported XML, external entities, viewer/redirect routes, invalid
  encoding, and oversized bodies. Scripts, styles, navigation, `ix:hidden`, and XBRL metadata are
  excluded. One deterministic visible passage of at most 2,000 characters and locator of at most
  256 characters is retained with its raw response hash, normalized passage hash, parser/rule/schema
  versions, URL, and filing identity. No full filing is persisted.
- Added typed exposure facts bound to the current run's pinned immutable reference membership,
  selected SEC parent checkpoint, primary-document checkpoint, source item/receipt/cache hashes,
  issuer/security identity, locator, reporting dates, and reviewed role. Candidate taxonomy terms do
  not prove exposure. Operational, planned, forecast, customer, contradicted, and insufficient
  claims remain distinct. Business exposure and financial materiality are separate; unknown revenue
  share stays null, decimal percentages preserve their explicit unit and period, comparable conflicts
  fail closed, and amendments remain distinct without automatic supersession.
- Routed selected new-security and holding quotes through the protected gateway producer. SQL binds
  ticker, instrument, current reference revision, selection/task, receipt, cache key, and reservation
  before Yahoo transport. The server rejects unselected tickers, incompatible instruments, non-USD,
  non-positive/non-finite prices, stale/future timestamps, redirects, and oversized responses. One to
  five validated bars retain their exact sample count/window while average daily dollar volume stays
  unavailable; malformed samples become unavailable. Quote evidence remains market data and never
  establishes exposure, suitability, or action.

## Additive protected and recovery contract

- Added `sql/migrations/20261010_bounded_adaptive_enrichment.sql`; migrations `20261004` through
  `20261009` remain byte-for-byte unchanged. The migration adds append-only selection-manifest and
  request-descriptor ledgers, shared canonical semantic hashes, current-pin membership checks,
  protected quote binding, typed fact persistence, exact final source reconciliation, and a
  service-only context wrapper that exposes frozen selections while revoking the renamed internal
  functions.
- The filing-document seal requires exactly one succeeded frozen SEC submissions dependency and an
  actual same-run collection checkpoint. It compares the parent request hash and identity, exact
  submissions cache/receipt/response hash, official request and archive URLs, issuer CIK,
  accession, form, filename, filing/accepted/reporting dates, and rejects duplicate accession rows.
- Enrichment precedes final `market_source_items`, so facts first bind to durable same-run collection
  checkpoints. Completion then reconciles the exact final source item, content hash, receipt, URL,
  and run provenance before accepting the packet.
- Recovery export, validation, restore, protected evidence, and exact comparison include both new
  ledgers. Recovery recomputes manifest/request/fact hashes, validates current pinned membership and
  parent submissions/document checkpoints, rejects endpoint/date/issuer/hash/accession/source
  tampering and conflicting duplicate accessions, and rejects full filing bodies in durable evidence.

## TDD evidence

The first RED tests failed because SEC filing-agent accessions, safe retained filing passages, typed
exposure facts, provider-specific queues, immutable selection manifests, and two-stage persisted
enrichment did not exist. Focused RED cases then reproduced planned/customer/forecast/negation
collapse, missing materiality and amendment semantics, input-order instability, share-class duplicate
work, missing quote identity/freshness/liquidity checks, and restart reselection.

Protected SQL and recovery began RED before the additive ledgers and typed validators existed. The
final self-review added a separate two-test RED proving that a syntactically valid document descriptor
could be sealed without proving its accession and filename against the parent submissions checkpoint.
The GREEN contract now rejects a missing or altered checkpoint before accepting the exact issuer row.
A further RED case showed comparable 12% and 15% revenue claims being treated as supported; GREEN
evaluation now marks that evidence unresolved with `unresolved_comparable_claim_conflict`.

The first full Python run exposed 40 legacy-contract failures; these were resolved without weakening
the public wrappers. A later run had three legacy quote mechanics tests calling the newly protected
entrypoint without selection; those mechanics tests now call the renamed internal function as the
database owner while the public function is tested to reject unselected quote work.

## Final verification

```text
.venv/bin/python -m pytest -q
1278 passed, 3 skipped, 4 deselected in 217.74s

.venv/bin/python -m pytest -q \
  tests/test_enrichment_sql.py tests/test_intelligence_pipeline.py tests/test_recovery_bundle.py
210 passed in 9.56s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared/collection-quotes_test.ts \
  supabase/functions/market-briefing-gateway/_shared/handler_test.ts \
  supabase/functions/market-briefing-gateway/_shared/repository_test.ts
73 passed, 0 failed

npm run test:all
exit 0: Python 1278 passed/3 skipped/4 deselected; Node 71 passed;
Deno 312 passed; package tests 6 + 52 passed; typechecks, ESLint, dependency
licenses, production build, and bundle verification passed; Playwright 21 passed/1 skipped
```

The final check also runs `git diff --check`, fresh-schema parity, protected migration immutability,
and Python compilation. All tests use fixtures, test doubles, and disposable local PostgreSQL. No
live provider, collector, Telegram, schedule, deployment, brokerage, paid/trial source, metered LLM,
or production call was made.

## Self-review

- Confirmed the sole collector pipeline remains the only orchestration entrypoint; Task 6 adds
  persisted stages within that invocation and does not call the collector recursively.
- Confirmed holding quotes run before ordinary collection/enrichment, selection stays within phase,
  provider, capability, issuer, and 100-task ceilings, and unused capacity is represented by stable
  sealed deferrals and existing reservation completion accounting.
- Confirmed a response-lost fact commit replays its exact semantic fact and source checkpoint; an
  uncertain transport barrier never retries; terminal work performs zero transport; restart never
  derives descriptors from current code, time, or mutable metadata.
- Confirmed prior-run security revisions are accepted only when members of the current run's pinned
  finalized snapshot. Unpinned revisions and fact/descriptor/source/date/issuer/hash substitutions
  fail closed across Python, TypeScript, PostgreSQL, and recovery validation.
- Confirmed hypothesis plus quote evidence cannot qualify ranking and `execution_allowed=true` is
  absent from every queue, fact, quote, packet, and recovery contract.

## Concerns

- Primary filing documents are supported. Exhibits and historical submissions files intentionally
  remain unresolved because the current bounded protocol does not retrieve and bind an exact filing
  index or historical-page membership.
- Quantitative materiality is recognized only for an explicit percentage of revenue retained with
  its passage context. Other financial metrics remain unknown rather than being inferred.

## Review fix round 1

Base: `3c3f3ef5a533489bc796e825e894066cef33df01`

The first RED restart test crashed because the protected reader returned `exposure_facts` but the
pipeline hydrated only tasks and frozen selections. A fresh pipeline after a durable fact checkpoint
therefore lost the fact from qualification even though the terminal document task correctly caused
zero additional transport. GREEN now validates each stored typed fact and restores it only when its
semantic hash and deterministic fact ID match its current pinned security, frozen descriptor,
terminal task, exact collection checkpoint, source receipt, and retained source item. Exact replays
deduplicate by fact ID; conflicting or altered stored facts fail closed. The crash/restart regression
proves zero second transport and identical fact content, ID, relation eligibility, and qualification.

The second RED set rehashed a forged `financial_materiality=supported` fact with a business-exposure
metric and null value/unit. PostgreSQL and recovery validation accepted the internally consistent
hash before the fix. GREEN applies the same invariant in Python, TypeScript, PostgreSQL, and recovery:
supported financial materiality requires the explicit `revenue_share` metric, a canonical finite
decimal from 0 through 100, `percent_of_revenue`, a metric period bound to the reporting period, and
operational supported claim/business/status semantics. Business exposure and missing or invalid
metric inputs remain unknown or are rejected. One shared fixture supplies the accepted semantic hash
and eleven rehashed negative vectors across runtimes. PostgreSQL uses null-safe comparisons so a
missing unit, metric, period, or state cannot bypass validation.

Verification for this review fix:

```text
.venv/bin/python -m pytest -q \
  tests/test_enrichment_sql.py tests/test_recovery_bundle.py \
  tests/test_intelligence_exposure.py tests/test_intelligence_pipeline.py
257 passed in 10.74s

.venv/bin/python -m pytest -q
1305 passed, 3 skipped, 4 deselected in 213.52s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared/intelligence_test.ts \
  supabase/functions/market-briefing-gateway/_shared/repository_test.ts \
  supabase/functions/market-briefing-gateway/_shared/handler_test.ts
88 passed, 0 failed

npm run test:all
exit 0: Python 1305 passed/3 skipped/4 deselected; Node 71 passed;
Deno 314 passed; package tests 6 + 52 passed; typechecks, ESLint, dependency
licenses, production build, and bundle verification passed; Playwright 21 passed/1 skipped
```

The final review checks also cover fresh-schema parity, byte-for-byte immutability of migrations
`20261004` through `20261009`, `git diff --check`, and Python compilation. All tests remain local and
fixture-backed; no live provider, collector, Telegram, schedule, deployment, production, brokerage,
paid/trial source, or metered LLM call was made.
