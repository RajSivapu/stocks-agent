# Task 13 report — review-only market learning

## Status

Implemented and locally focused-verified from base `f2148bf`. V1-C5 is marked complete locally;
production remains unclaimed. No provider, model, database, Telegram, migration, deployment, policy
activation, portfolio, plan, or delivery mutation occurred.

## RED evidence

The required Step 2 command failed at the intended missing boundary, and `&&` prevented the Deno
half from running:

```text
$ .venv/bin/python -m pytest tests/test_intelligence_learning.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts
E   ModuleNotFoundError: No module named 'lib.intelligence.learning'
1 error in 0.08s
```

Tests were frozen after this RED. The only later test edits repaired concrete gate compatibility:
the pre-existing fixed RPC allowlist gained `record_market_learning`, and one frozen TypeScript
fixture received an explicit `string[]` annotation.

## GREEN evidence

The exact Task 13 Step 4 command passed after fixing those two reported failures:

```text
$ .venv/bin/python -m pytest tests/test_intelligence_learning.py tests/test_gateway.py tests/test_security_invariants.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts supabase/functions/market-briefing-gateway/_shared/contracts_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts
60 passed in 0.31s
ok | 60 passed | 0 failed
```

The required dashboard mapper also passed a focused TypeScript check:

```text
$ npx --yes deno@2.9.6 check supabase/functions/owner-dashboard-api/mappers.ts
Check supabase/functions/owner-dashboard-api/mappers.ts
```

`git diff --check` completed with no output.

## Implemented boundary

- `LearningObservation` is frozen and slots-backed. Its evidence, limitations, metrics, sample,
  horizon, benchmark, run, and policy linkage are immutable; proposals contain no apply/update
  method.
- Only complete, synchronized, benchmark-backed 5/21/63-session outcomes are eligible. Mixed
  policy versions, benchmarks, horizons, or runs fail closed. Fewer than five eligible outcomes
  remain observations rather than owner-review proposals.
- False-positive/noise and source-failure observations are explicit. Historical outcome limitations
  state that in-sample results do not prove future performance.
- A missed event requires authoritative evidence discovered after the original run, an unranked
  candidate, and evidence whose source and occurrence time fall within the original declared
  coverage. Social, already-ranked, out-of-source, out-of-window, or not-later evidence is not
  blamed on the prior run.
- The strict `record_learning` operation validates exact bounded fields and semantic SHA-256 over
  the canonical observation body, supports a write-free/send-free dry run, and calls only the
  existing `record_market_learning` RPC.
- The Task 2 outer RPC input and semantic-hash contract remain unchanged. The schema mirror adds
  defense-in-depth rejection of executable apply/update/activation/provider/portfolio/plan/delivery
  keys while retaining immutable insert-only persistence and existing idempotency.
- The dashboard contract and mapper expose at most 20 learning records and redact evidence IDs and
  proposal contents into counts/availability plus bounded metadata.

## Concerns and boundaries

- The learning migration remains local-only and was not applied.
- Owner review is a proposal state only. There is intentionally no automatic policy, weight,
  threshold, source, sizing, holding, plan, or delivery activation path.
- The full repository suite and release deployment gates remain Task 14 work and were not run here.

## Reviewer fix round 1

`build_noise_observation` now fails closed unless every eligible outcome matches the caller-supplied
original run and policy version and the complete aggregation shares one eligible horizon and one
benchmark. Mixed provenance can no longer be labeled with metadata copied from the first row.
Insufficient-sample proposal behavior and the frozen tests remain unchanged.

Focused verification:

```text
$ .venv/bin/python -m pytest tests/test_intelligence_learning.py -q && npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/outcomes_test.ts
10 passed in 0.01s
ok | 12 passed | 0 failed
```
