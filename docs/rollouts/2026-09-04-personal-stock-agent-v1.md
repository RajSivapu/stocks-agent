# Personal Stock Agent V1 — Protected Rollout Record

Status: **not deployed; protected rollout and scheduled verification pending**.

## Immutable release boundary

- Owner-only, friend invitations disabled, suggestion-only, and brokerage-free.
- Zero incremental dollars and approved free sources only.
- Browser surfaces are GET-only and cannot trigger research, Telegram, or financial mutation.
- No duplicate live scheduled run may be started for verification.
- The superseded thin dashboard is not a deployable V1 artifact.

## Candidate and review receipts

- Prior candidate `a5d4296595ab5f41a7120953ba117638888dccf6`: blocked by independent review and never deployed.
- Replacement candidate SHA: pending the fix-round checkpoint and exact-head re-review.
- Local full suite: passed on 2026-09-04 with 699 tests passed and 20 skipped; typecheck, lint,
  license, production build, bundle scan, and Playwright gates passed.
- Exact-head CI SHA and run URL: pending.
- Independent review verdict: `a5d4296` blocked; replacement verdict and reviewed SHA pending.

## Protected dry-run receipts

- Fixture/provider mode: pending.
- Packet size and candidate/evidence bounds: pending.
- Provider quota receipts: pending.
- Before/after table counts and zero-write result: pending.
- Telegram message IDs and zero-send result: pending.
- Holdings and plans unchanged: pending.

## Database and function receipts

- Migration `20260907` hash/application/verifier receipt: pending.
- Migration `20260908` hash/application/role-verifier receipt: pending.
- `market-briefing-gateway` version, candidate SHA, and source hash/parity: pending.
- `owner-dashboard-api` version, candidate SHA, and source hash/parity: pending.
- New dashboard secret manifest receipt: pending.
- Dashboard runtime-login least-privilege receipt: pending.

Only those two changed functions are in the V1 deployment manifest. The existing Telegram function
is not part of this deployment.

## Static and access receipts

- Immutable static asset hashes and candidate SHA: pending.
- Private Site version and deployment ID: pending.
- One-account host allowlist and security headers: pending.
- Owner canary across Portfolio, Ideas, Intelligence, Reports, and System / Receipts: pending.
- Anonymous denial: pending.
- Authenticated non-owner denial: pending.

## Reconciliation and scheduled receipts

- Independent database/source parity: pending.
- Intelligence, report, publication, and hash reconciliation: pending.
- Next existing pre-market/intraday/post-market/Friday scheduled chain: pending.
- Owner-approved alert-class canary, if approved after a real shadow example: pending.
- Telegram acceptance and separate owner acknowledgement: pending.

## Rollback receipt

- Local rollback-path verification: the protected path requires a prior gateway Git ref, matching
  source SHA-256, and current live version before mutation, then redeploys and verifies that source
  while removing the new dashboard authority. No production rollback was invoked.
- Initial production rollback exercise/result: pending.
- Dashboard secrets unset: pending.
- Newly introduced dashboard function removed: pending.
- Dashboard runtime login disabled: pending.

## Canonical verifier input

The protected controller must populate and pass this schema to
`scripts/verify_personal_stock_agent_v1.py`; `null` is intentionally fail-closed:

```json
{
  "candidate_sha": null,
  "exact_head_ci": null,
  "independent_review": null,
  "quota_receipts": null,
  "dry_run_zero_writes": null,
  "dry_run_zero_sends": null,
  "migration_version": null,
  "gateway_version": null,
  "dashboard_api_version": null,
  "site_version": null,
  "owner_canary": null,
  "anonymous_denial": null,
  "non_owner_denial": null,
  "source_parity": null,
  "scheduled_receipt": null,
  "rollback_check": null
}
```

No production, Telegram, owner-view, or C6-complete claim is supported by this pending record.
