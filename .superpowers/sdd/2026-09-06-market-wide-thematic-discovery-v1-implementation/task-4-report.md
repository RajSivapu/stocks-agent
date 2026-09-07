# Task 4 report — Official feeds, safe cursors, and truthful source coverage

Status: DONE

Base: `2e6b8ff947db2a7751406176be9f97d4824e9b07`

## Implemented behavior

- Added persistent `SourceCursor`, `CollectionWindow`, `CollectionPage`, and `SourceOutcome`
  value types with exact mapping round trips, a minimum two-hour overlap, a 31-day upper bound,
  bounded backlog tokens and accepted-item identities, exact active-window retry, and contiguous
  watermark advancement only after exhaustion.
- Added bounded RSS/Atom parsing. XML is rejected before parsing when it contains DTDs or entity
  declarations; feed parsing also rejects script-like content, malformed dates, missing identities,
  unsafe item links, oversized bodies, and unsupported MIME types. DOE's reviewed XML response is
  accepted when served as `text/html`.
- Added verified, ticker-free collection for DOE Energy News, both keyless EIA RSS feeds, separate
  Defense releases/news feeds, White House fact-sheet/presidential-action/briefing listings and
  current post sitemaps, Federal Register document search, and SEC issuer/primary-document routes.
- Added exact Defense redirect admission from each reviewed `www.defense.gov` path/query to only
  its matching `www.war.gov` path/query. A changed content type, site, limit, host, or path is
  rejected before a second transport open.
- Added Federal Register `next_page_url` validation and `search_after` continuation through either
  reviewed documents API path. Document type/status and effective date remain explicit.
- Added the reviewed EIA v2 electricity route behind both a free `EIA_API_KEY` and the configured
  series route. The capability remains disabled with `configuration_missing`; both EIA RSS routes
  remain keyless.
- Removed the hard-coded SEC contact from the reference refresh and legacy EDGAR client. SEC access
  now requires injected or configured `SEC_USER_AGENT_CONTACT`, returns configuration-missing
  coverage before transport when absent, and never places the contact value in health output or a
  source receipt.
- Extended collection queries and receipts with bounded capability, cursor, page, accession,
  primary-document, overlap, backlog, retry-phase, and truthful coverage metadata. `SourceItem`
  continues to support issuer/security identities but no longer requires either for market-wide
  official evidence.
- Kept the established pre-transport adapter contract: invalid, unsupported, and missing-
  configuration queries raise `SourceFailure`. The pipeline converts those outcomes into truthful
  terminal receipt/coverage metadata. The deliberate public semantic change is ticker-free
  `SourceItem`; the additions to `CollectionQuery` and `RequestReceipt` are optional and backward
  compatible.
- Replaced the healthcheck's provider-name-only output with injected, testable capability health.
  It reports reviewed hosts, path patterns, static route host/path, credential presence, exact
  redirect results, and `ok`, `ready_requires_identifier`, `configuration_missing`, `unsupported`,
  or `source_failed` without values. Missing optional Alpha Vantage never controls the SEC/GDELT
  baseline.
- Updated Routine and root setup documentation plus the local-secret example for the exact official
  domains, required SEC contact, optional existing-free-key Alpha Vantage, and disabled EIA
  statistics posture.
- Preserved owner-only, suggestion-only behavior and `execution_allowed=false`. No brokerage,
  execution, notification, schedule, deployment, production, collector, or Telegram operation was
  invoked.

## TDD evidence

The implementation began with meaningful failures before production code:

- cursor tests: import failed because `lib.intelligence.cursors` did not exist;
- official-source tests: import failed because `lib.intelligence.providers.rss` did not exist;
- capability registry: failed because the reviewed Defense capability was absent;
- SEC reference tests: failed because `refresh_sec_reference` did not accept a configured contact;
- health tests: import failed because `lib.intelligence.health` did not exist;
- redirect preflight: failed because `HttpRequest` had no exact redirect allowlist;
- pipeline outcomes: three failures showed configuration, unsupported, and source failures lacked
  truthful terminal metadata;
- self-review cursor tests: two failures showed a first failed page did not retain its exact window
  and inconsistent truncation flags were accepted;
- authority tests: three failures showed multi-capability adapters used a provider-wide authority;
- XML transport test: a valid DTD/entity document reached the parser instead of failing first;
- SEC filing-time test: retrieval time was incorrectly used as publication time.

Every RED was followed by the smallest corresponding implementation and focused GREEN. The first
full suite then found 19 integration failures: 17 durable SQL attempt-barrier failures shared one
cache-key identity mismatch, and two assertions still described the old health/environment
contract. The pipeline cache identity now includes the same optional cursor/capability fields as
the adapter, preserving the existing durable crash barrier. Targeted reruns passed 17 SQL cases and
three health/documentation cases.

## Final verification

Required focused source/cursor command:

```text
.venv/bin/python -m pytest tests/test_intelligence_cursors.py tests/test_intelligence_official_sources.py tests/test_intelligence_providers.py tests/test_intelligence_http.py tests/test_healthcheck.py -q
120 passed in 0.29s
```

Complete Python suite after the route, cursor, DTD, and filing-time review fixes:

```text
.venv/bin/python -m pytest -q
1105 passed, 3 skipped, 4 deselected in 86.32s (0:01:26)
```

The final localized health-presence correction then passed its complete affected slice:

```text
.venv/bin/python -m pytest tests/test_healthcheck.py tests/test_gateway.py tests/test_security_invariants.py -q -k 'healthcheck or routine_documentation_exposes'
8 passed, 61 deselected in 0.27s
```

Additional static and cross-runtime checks:

```text
node --test tests/*.mjs
71 passed

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
ok | 309 passed | 0 failed

npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/telegram-portfolio/index.ts \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
exit 0

npm run typecheck --workspace @stocks-agent/dashboard-contracts
npm run typecheck --workspace @stocks-agent/web
npm run lint --workspace @stocks-agent/web
all exited 0
```

`py_compile`, both modified JSON documents, and `git diff --check` exited 0. The production
reconciliation `20261004` and reviewed `20261005`/`20261006` migration files have no diff from the
base.

## Self-review

- Confirmed every new outbound route is HTTPS, uses an approved host/path, and is bounded by bytes,
  item count, request quota, page count, or cursor size before persistence.
- Confirmed credentials are admitted before transport and omitted from normalized source URLs,
  canonical content, health results, errors, and receipts.
- Confirmed White House sitemap `lastmod` remains metadata and is never treated as publication time;
  SEC primary-document retrieval likewise leaves unknown publication time unset.
- Confirmed source failures, empty successful results, missing configuration, unsupported queries,
  quota blocks, truncation, and remaining backlog stay distinguishable.
- Confirmed cache keys match across adapters, attempt barriers, checkpoints, and restart hydration.
- Confirmed Task 4 tests use fixtures/test doubles only. No live source, collection, Telegram,
  schedule, deployment, or production mutation was performed.
- Confirmed `20261004`, `20261005`, and `20261006` remain immutable.

## Concerns

- EIA statistics are intentionally unavailable until a later reviewed configuration explicitly
  enables the exact route with an existing free key. No signup, paid tier, trial, or fallback is
  attempted.
- BLS and BEA abstract series adapters remain explicitly unsupported/configuration-missing as
  established by the capability registry; Task 4 did not invent series identifiers or broaden
  those routes.

## Fix round 1 — scheduled execution, adversarial sources, and cross-run cursors

Base: `169859e8f38e9b3b06eb6fd0ce3d259d9d3cb500`

The sole scheduled collector now executes Task 1's exact persisted capability plan. It hydrates
each task's durable capability/theme cursor, derives the bounded window/page/token query, writes
planned and attempting checkpoints before transport, and persists terminal cursor plus receipt
metadata in the existing task result/checkpoint fields. Terminal replay reconstructs the cached
receipt without calling a provider again. Configuration and programmer validation errors retain
their established adapter contract; the pipeline alone converts expected source outcomes into
truthful receipts.

Source hardening now disables automatic health-probe redirects and admits only the two reviewed
Defense-to-war.gov path/query pairs. Rolling unpageable RSS overflow freezes its watermark/window
and reports `truncated`, `backlog_remaining`, `continuation_unavailable`, and `coverage_gap`.
Pageable adapters reject repeated continuation tokens. White House sitemap continuation carries
the sitemap-child index and child offset and visits every approved child within its fixed bounds.
Every retained item is checked against its official host/path; Federal Register and SEC payload
identities must match the requested document. Decoded active markup is rejected from identity,
title, and summary fields. Accepted IDs clear only after a fully exhausted contiguous window.

Cross-run catch-up uses the additive
`sql/migrations/20261007_discovery_cursor_context.sql`. Its service-only RPC returns at most 100
unique latest cursors per capability/theme from prior completed runs and succeeded terminal tasks.
It rejects malformed, future-dated, or mismatched cursor state and attaches the exact source
run/task/update provenance. Failed, uncertain, current-run, and future-watermark records cannot
advance a later run. The gateway validates the complete RPC shape and provenance pair before
placing it in `intelligence_collection_context`; the service collector checks the same pairing
before hydrating `SourceCursor`. Recovery preserves the terminal task result verbatim and rejects
malformed cursor metadata. The reviewed 20261004, 20261005, and 20261006 migrations remain
unchanged.

### Fix-round TDD evidence

- Scheduled-path RED: planned capabilities and persisted cursors never reached the only production
  pipeline invocation. The new integration first failed with no `discovery_plan` or
  `source_cursors`, then passed with one collector call and durable planned/attempting/terminal
  transitions.
- Redirect RED: the health transport followed redirects before validating the destination. The
  regression now proves redirects are disabled and only each exact Defense-to-war.gov pair is
  admitted.
- Cursor/feed RED: eight adversarial cases exposed pre-slice overflow loss, fabricated
  continuation, repeated tokens, incomplete sitemap-child traversal, and premature accepted-ID
  clearing. The corrected adversarial source slice passed all 49 cases.
- Provenance RED: five cases accepted off-domain item links, Federal Register identity drift, SEC
  CIK/accession/document drift, or decoded active markup. All now fail closed before retention.
- Cross-run RED: no 20261007 migration existed and a new run's protected context contained neither
  `source_cursors` nor `last_completed_scans`. The first PostgreSQL test returned zero rows
  until task and cursor capability identity were made exact. The repository test then failed its
  typecheck because both context fields were absent, and a second RED proved cursor hydration was
  incorrectly conditional on quote-context availability. Each test now passes.

### Fix-round verification

```text
.venv/bin/python -m pytest -q tests/test_market_wide_discovery_sql.py \
  tests/test_collect_market_intelligence.py tests/test_recovery_bundle.py
145 passed in 9.45s

.venv/bin/python -m pytest -q tests/test_market_wide_discovery_sql.py \
  tests/test_collect_market_intelligence.py tests/test_recovery_bundle.py \
  tests/test_intelligence_pipeline.py tests/test_intelligence_cursors.py \
  tests/test_intelligence_official_sources.py tests/test_healthcheck.py
220 passed in 9.07s

.venv/bin/python -m pytest -q tests/test_intelligence_controller_sql.py
38 passed in 9.80s

npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
ok | 310 passed | 0 failed

npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/telegram-portfolio/index.ts \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
exit 0
```

The complete Python suite passed earlier in this fix round with `1122 passed, 3 skipped,
4 deselected in 154.84s`. Later changes were limited to the additive 20261007 SQL, protected
gateway mapping, collector validation, and recovery validation; the affected suites above were
rerun instead of repeating the unchanged full suite. Node tests (71), workspace typechecks,
ESLint, dependency-license checks, production build/bundle, and Playwright (21 passed, 1 skipped)
also passed in this fix round. Final `py_compile` for all modified Python modules and
`git diff --check` exited 0.

### Fix-round self-review

- The new RPC is `SECURITY DEFINER` with `search_path=pg_catalog`; execute is granted only to
  `service_role`. Owner/dashboard roles receive no cursor-result access.
- Cross-run selection is bounded, unique, prior-run-only, non-future, and provenance-carrying.
  Same-run replay stays on the existing run-scoped discovery context.
- Cursor and receipt state are stored inside existing protected task fields and recovery datasets;
  no duplicate collector or provider call was introduced.
- No test used a live source. No collector, Telegram, schedule, deployment, or production mutation
  ran during the fix.
