# Task 14 report — V1 release infrastructure and local verification

## Status

Implemented from base `1155327`. Local release infrastructure is present and the consolidated
repository gate passes. Exact-head CI, independent review, protected dry-run, production migrations,
function/static deployment, live canaries, source parity, and scheduled receipts remain pending.
V1-C6 is not marked complete.

No production migration, provider/model request, database write, Telegram send, scheduled research,
Site deployment, function deployment, secret publication, or runtime-login mutation occurred.

## RED evidence

The required verifier test command failed at the intended missing-module boundary:

```text
$ .venv/bin/python -m pytest tests/test_verify_personal_stock_agent_v1.py -q
E   ModuleNotFoundError: No module named 'scripts.verify_personal_stock_agent_v1'
1 error in 0.07s
```

The verifier tests were frozen after this RED. The focused deployment/canary contract was also
observed failing for the absent release migration/function manifests, thin-dashboard guard,
five-surface canary routes, and non-owner denial before those behaviors were implemented.

## Implemented release boundary

- The fail-closed V1 verifier requires all 15 release gates, exact true dry-run zero-write/zero-send
  booleans, a canonical candidate SHA, expected receipt statuses, SHA agreement for CI/review/source
  parity, exact migration versions, positive function versions, and a non-empty Site version.
- Local and CI gates compile/run the verifier, and CI rejects a checkout whose HEAD differs from
  `GITHUB_SHA`.
- The protected deployment applies `20260907` and `20260908` in fixed order with SHA-256 receipts,
  deploys only the changed market gateway and owner dashboard API, requires the gateway to exist and
  the initial dashboard API to be absent, and records exact source-tree hashes and versions.
- Deployment requires an explicit independently reviewed SHA equal to the clean pushed HEAD. Static
  assets are built from that checkout, bundle-scanned, hash-receipted, and candidate-SHA-bound.
- The deploy guard requires Portfolio, Ideas, Intelligence, Reports, and System routes and explicitly
  refuses the superseded thin dashboard.
- GET-only verification covers all five primary surfaces plus supporting receipt routes, completed
  run detail, anonymous denial, authenticated non-owner denial, and independent read-only source
  reconciliation.
- Failure after dashboard secret publication removes the three new dashboard secrets, removes only
  the newly introduced dashboard function, and disables the new runtime login. Artifact failure uses
  the same rollback; append-only evidence migrations are not destructively removed.
- Review and rollout records contain pending receipt fields only. They make no live or completion
  claim.

## Verification evidence

The first complete run stopped with 363 Python tests passed, 19 skipped, and one failure. Root-cause
inspection showed Task 13 had added executable-authority rejection to `sql/schema.sql` but not its
canonical `20260907` migration, so the migration mirror test correctly failed and a production apply
would have omitted that defense. The same clause was added to the migration; the exact failing test
then passed.

The complete repository gate then passed:

```text
$ npm run test:all
364 Python passed, 19 skipped
63 Node passed
218 Deno passed
6 dashboard-contract tests passed
31 web unit tests passed
typecheck, lint, license, production build, and bundle scan passed
17 Playwright passed, 1 opt-in live canary skipped
```

Total: 699 passed and 20 skipped. The opt-in live check remained skipped because this local task was
not authorized to touch production. The final focused release/migration gate passed 85 tests, and
`git diff --check` produced no output.

## Protected rollout requirements

The controller still needs the exact checkpoint SHA pushed through the protected branch, matching
exact-head CI and independent review receipts, existing project-bound database/Supabase credentials,
an authenticated non-owner canary token, a protected write-free/send-free dry run, private Site
version/deployment receipts, live source parity, owner/denial canaries, rollback evidence, and the
next existing scheduled receipts. No duplicate scheduled run is authorized.
