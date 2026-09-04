# Owner-Only Personal Stock Agent Web v1 — Protected Rollout

Status: **not live; protected deployment pending independent review**.

## Release boundary

- Owner-only and read-only.
- Suggestion-only; the owner decides and places every trade.
- No broker connector, broker credential, or order endpoint.
- No public registration or friend invitation workflow.
- No endpoint that triggers research, a scheduled run, a provider request, a Telegram send, or a
  financial write.

## Candidate source receipts

- GitHub branch: `codex/owner-alert-v3`
- Implementation candidate SHA: `686bd3b57774a2462c49666b53b6b67f8e339a01`
- Private Site source repository: must be verified at the exact final review SHA before version
  creation.
- Working tree: clean after push.
- Site version: none.
- API function: not deployed.

## Production preflight receipts

### Supabase Auth

The authenticated project dashboard was changed and reloaded to verify:

- public email sign-up disabled;
- access-token lifetime 900 seconds;
- refresh-token replay detection enabled with a 10-second reuse interval;
- canonical Site URL set to the intended private Site origin; and
- redirect allowlist containing that exact origin only.

The Auth user inventory was empty at preflight. The protected bootstrap must create exactly one
confirmed owner account and fail closed for any different or additional account.

### Private static host

The Site access response reported:

- current caller role: owner;
- access mode: custom;
- one allowed owner account;
- zero allowed groups; and
- zero external visitors.

The hosting platform permits the owner to add external visitors in the future, but this rollout has
not added any and the product has no invitation UI. Any future sharing remains a separate,
explicitly approved multi-user redesign.

## Protected deployment sequence

1. Obtain a no-material-findings independent review for the exact candidate.
2. Create or idempotently verify the sole Supabase Auth owner without logging identity or secrets.
3. Reverify no existing `owner-dashboard-api` function and bind both administrator and session
   database URLs to the exact target project before opening a connection.
4. Apply the direct-column `SELECT` role migration and provision the scoped session-pooler login.
5. Structurally prove zero writes, zero application-function execution, zero object ownership, exact
   membership, and exact column allowlists.
6. Publish only `DASHBOARD_ALLOWED_ORIGINS`, `DASHBOARD_DATABASE_URL`, and
   `DASHBOARD_OWNER_USER_ID`; deploy the pinned function; run the GET-only reconciled canary; and
   globally revoke the temporary Auth session in every outcome.
7. If any post-publication step fails, remove the three dashboard secrets, remove only the new
   dashboard function, and disable the runtime login.
8. Build and scan immutable static assets from the deployed SHA, save a private Site version, deploy
   it, and verify the access policy again.
9. Verify HTTPS/security headers, owner sign-in, unauthenticated denial, non-owner denial, all seven
   views, a completed run, Light/Dark/System, and desktop/mobile layouts without starting a market
   run.

## API deployment receipt

Pending. No function version or live API claim is recorded yet.

## Static deployment receipt

Pending. No Site version, deployment ID, or live URL claim is recorded yet.

## GET-only source reconciliation

Pending. The canary must reconcile run/write, publication/send/suppression, active policy,
holding/price/data-time, and scoped-role claims with independent source reads. It may issue only
authentication operations plus dashboard `GET` requests; it must not trigger a market run or a
Telegram message.

## Owner access

Pending successful deployment. The final instructions will provide the private Site URL and OTP
sign-in flow without exposing the owner email or any credential in this receipt.
