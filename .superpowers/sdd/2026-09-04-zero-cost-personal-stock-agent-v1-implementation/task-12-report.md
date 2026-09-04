# Task 12 Report

- Base: `b892fa6`
- Scope: Task 12 five-surface owner dashboard only.

## RED

The exact Step 2 command ran once after the navigation, state, safety, absorption, and route tests
were written. It failed as expected with five failed files and five failed tests while six files and
20 tests passed. The failures directly showed the absent Intelligence and Reports modules, old
seven-link navigation, missing Portfolio/Ideas/System absorbed sections, and rejected Task 11 read
paths. Scenarios were frozen after RED.

## Implementation

- Replaced the seven-link navigation with exactly Portfolio, Ideas, Intelligence, Reports, and
  System / Receipts. Root and retired primary paths redirect into the new information architecture;
  report detail and run receipt detail remain subordinate routes.
- Portfolio now includes Today attention/market context and Companion context alongside holdings,
  plans, and transactions. Ideas separates relationship/exposure evidence, Analyst, Checker,
  deterministic policy, conditional scenarios, and eligible outcome observations.
- Added bounded Intelligence coverage, theme, event, value-chain relationship, count, failure,
  drop, quota-boundary, and limitation views without exhaustive-news language.
- Added immutable report lists and exact report-detail publication timelines. Telegram acceptance
  remains acceptance and is never upgraded to delivery.
- System / Receipts now summarizes run/write, alert/send, source/policy, deploy-unavailable, and
  permanent owner-only/suggestion-only/no-brokerage boundaries.
- Extended only the existing browser client path allowlist needed for Task 11 Intelligence and
  report GET routes. Unsafe stored content remains React text; only allowlisted HTTPS source links
  become anchors.
- Preserved the paired Midnight Navy/Warm Gold and Warm Pearl tokens, System/Light/Dark behavior,
  semantic statuses, keyboard focus, reduced motion, accessible tables, and page-level fit at 320
  CSS pixels.

## Verification

The exact Step 4 chain ran once and stopped at one test-fixture failure: a single `Response` body was
reused for three client reads. The other 30 unit tests passed. The response factory was corrected
without changing production behavior or expanding scenarios.

The failed unit component then passed 31/31. The unreached continuation passed TypeScript
typecheck, ESLint, Vite production build, dashboard bundle verification (14 files; initial JavaScript
131,055 gzip bytes), and Playwright with 17 passed and one optional live read-only canary skipped.
Browser coverage included both themes, keyboard access, axe checks, five primary routes, report
deep links, unsafe stored content, stale/owner-denied/expired states, and widths from 300 through
1,440 CSS pixels including 320.

No full repository suite, live provider/model/API/database call, production migration, Telegram
send, deployment, duplicate scheduled run, financial mutation, brokerage integration, or trade
execution occurred.

## Concerns / boundaries

- V1-C5 remains local and awaits the consolidated full suite and exact-head independent review.
- Task 11 intentionally exposes bounded source status rather than raw quota reservations and no
  deployment receipt in the current read model; the UI labels those limitations instead of
  reconstructing or inferring unavailable state.

## Fix round 1

- Moved the shell banner boundary from the always-loaded Today resource to each active route's own
  resource state. Intelligence, Reports, report detail, Ideas, System, Portfolio, and run detail now
  report their own loading, error, freshness, and `data_as_of` truth. Portfolio and System aggregate
  child freshness and label a child failure as partial rather than upgrading the route.
- Preserved Today/Companion and Runs/Alerts as full resource states through their absorbed parent
  surfaces. Their loading and error cards now remain distinct from ready-but-empty content; partial
  and unavailable ready envelopes remain visible in the route banner.
- Tightened the shared web source-link boundary to the exact approved Task 2 provider and official
  hosts. It accepts only HTTPS without credentials and without a non-443 explicit port; malformed,
  arbitrary, lookalike, and unapproved hosts remain inert text.
- No tests were added. Two existing positive-link fixtures were pointed from arbitrary
  `example.com` to the approved `www.sec.gov` host so their original assertions continue to exercise
  an accepted source link under the corrected policy.

The bounded fix gate passed once: 31/31 existing web unit tests, TypeScript typecheck, and ESLint.
Build, bundle, browser, full-suite, live, migration, Telegram, and deployment checks were not run in
this fix round, as directed.
