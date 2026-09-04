# Owner Dashboard Web v1 — Code Review

Status: **release candidate; independent exact-head verdict pending**.

## Scope

- Branch: `codex/owner-alert-v3`
- Review base: `d4e0c3ddd5a032fd4a41599aa6ad1966c9d7594f`
- Current candidate: `7b36feaf9ef29090e17ae488d97f04ebec6d8a84`
- Design: `docs/design/2026-09-03-owner-only-personal-stock-agent-web-app-plan.md`
- Plan: `docs/superpowers/plans/2026-09-03-owner-only-personal-stock-agent-web-v1-implementation.md`

The release is single-owner, read-only, suggestion-only, friend-invitations-disabled, and has no
brokerage authority. The review must not treat the deferred multi-user branch as the release
candidate.

## Required independent review gate

An adversarial Claude review must inspect the exact range above and explicitly challenge all ten
targets in design section 25: anonymous/non-owner disclosure, database-role authority, stored-text
and source-link XSS, freshness semantics, side effects, Companion receipt boundaries, unnecessary
data exposure, session behavior, private-host/Auth/CORS alignment, and the sufficiency of deployment
receipts.

The production API and Site must not be deployed until that review returns no unresolved Critical
or Important finding. This section will record the verbatim disposition without overstating a
rate-limited or incomplete review as approval.

## Earlier review disposition

An earlier implementation review of the pre-release checkpoint identified the following material
issues. Each was verified against the code before it was changed.

| Finding | Disposition |
|---|---|
| Closed/unknown prices could be labeled current without complete source, time, and market-state context | Fixed; price context now fails closed and has mapper/repository tests |
| CI used a malformed action pin | Fixed with a valid immutable action commit |
| Pre-auth rate limiting trusted an unsafe forwarding value and allowed unbounded cardinality | Fixed with bounded network signals, capped storage, expiry, and tests |
| Expired entry zones could remain visible | Fixed with market-date filtering and tests |
| Run receipts omitted required system kinds, write-count completeness, and suppression distinctions | Fixed in contracts, repository mapping, and tests |
| Invalid owner/JWKS configuration could be represented as an ordinary authentication failure | Fixed with a pre-database `503` configuration kill switch |

## Additional internal adversarial finding

The final internal pass found that a failure after dashboard-secret publication but before a
verified function deployment could leave the secret manifest configured. It also found that a
naive cleanup could target a function that predated the rollout. Commit `7b36fea` resolves this by:

- proving the dashboard function is absent before database or secret mutation;
- rechecking function absence in the deployment operation;
- unsetting the exact three dashboard secrets after a post-publication failure;
- deleting only an initial dashboard function when presence is supported or unknown;
- revoking the runtime role membership and setting the login to `NOLOGIN PASSWORD NULL`; and
- attempting both database-login and Edge cleanup even if one cleanup operation fails.

Regression tests cover pre-publication failure, post-publication failure, an existing function,
function absence during rollback, and runtime-login invalidation.

## Verification receipts before independent review

- `npm run test:all` exited 0 on candidate `7b36fea` at 2026-09-04T12:34Z.
- Python: 201 passed.
- Telegram/command Node tests: 63 passed.
- Edge/Deno: 187 passed.
- Dashboard contracts: 4 passed.
- Web unit tests: 23 passed.
- Browser: 17 passed; one production-live canary was intentionally skipped before deployment.
- Type checking, lint, dependency-license check, production build, and bundle scan passed.
- `npm audit --audit-level=high --omit=optional --ignore-scripts` reported zero vulnerabilities on
  the immediately preceding candidate; it must be rerun on the deployed SHA.

## Platform boundary disclosed for review

Supabase supplies its standard built-in project environment variables to Edge Functions. The
dashboard source and its explicit secret manifest do not read or reference the service-role key;
all dashboard queries use the independently provisioned direct-`SELECT` database login. The three
custom dashboard secrets are project-level Supabase secrets rather than per-function secrets. This
is a platform boundary, not a claim that those values are absent from every other function runtime.

## Final independent verdict

Pending. Do not infer approval from this placeholder.

