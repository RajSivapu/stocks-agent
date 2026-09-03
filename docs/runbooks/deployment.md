# Staging and production deployment

This product deploys from GitHub Actions; the operator's Mac is not a runtime dependency. Pull requests
receive no environment secrets. The `staging` and `production` GitHub environments hold separate
credentials and should both require protected-branch rules; production additionally requires a human
reviewer. Every referenced GitHub Action is pinned to an immutable commit.

## One-time setup

1. Create separate Supabase staging and production projects. Disable public registration and configure
   only phone-compatible email OTP through custom SMTP before any non-synthetic invitation.
2. Configure the two protected GitHub environments using the variable and secret names referenced by
   their workflow. Create the staging operator as an Auth user, store its UUID as
   `STAGING_OPERATOR_ID`, and store the existing production owner's Auth email only as the protected
   `PRODUCTION_OWNER_EMAIL` secret. Provider trigger credentials stay in Supabase Vault, not GitHub.
3. Configure two Cloudflare Worker custom domains/routes, one for each static asset worker. The web
   worker has no server code, secrets, environment variables, or service-role key. `workers_dev` stays
   disabled.
4. Configure the Edge runtime secrets documented by each function and run the real Claude no-write
   handshake from the Connections screen. Record only its public connection ID in the staging
   environment.
5. Configure encrypted R2 backup and the independent health monitor from
   `docs/runbooks/backup-restore.md`. These are external prerequisites; a green code build cannot
   substitute for a real archive and restore.

## Staging

`CI` runs secret scanning, dependency audits, SQL parsing, migration-from-current and fresh-schema
fixtures, the exposed-surface allow-list, all language tests, the web build, and a build-output scan.
After successful CI on `main`, staging replays the actual `supabase/migrations/` chain, provisions the
pre-created staging Auth identity as the hash-only release operator, rotates the four database runtime logins,
deploys exactly four replacement Edge Functions, and publishes static assets. It creates two temporary
Auth owners, initializes their private profiles, attacks RLS through raw PostgREST in both directions,
checks aggregate health, and deletes the temporary identities even when verification fails. The static
bundle contains a public two-field `release.json`; staging and production verification reject it unless
its environment and commit match the exact checked-out release.

The staging release report contains only status labels, the commit, a timestamp, recovery age, and a
SHA-256 evidence digest. It contains no tokens, email addresses, UUIDs, holdings, tickers, or model
content. A prior Claude handshake does not count forever: operational review must confirm the tested
connection and its evidence belong to the current release window.

## Production gate

Production is manual and protected. Before approval, verify all of these:

- the exact commit is deployed and accepted on staging;
- the latest encrypted production backup is less than **36 hours** old;
- the latest verified staging restore is less than **30 days** old;
- the old schedules, scheduler calls, Telegram webhook mutation route, and Run Now entrypoint are
  paused, then enter `TRIGGERS PAUSED`;
- a distinct, reviewed 40-character rollback reference exists;
- operator health reports no missed run, unavailable connection, failed projection, or paused owner;
- the owner has reviewed the backup and migration evidence and enters `DEPLOY PRODUCTION`.

For the first legacy-data release only, run `operation=owner-cutover`, enter `CUTOVER OWNER`, provide
the UTC time and SHA-256 digest of the private backup evidence, and keep `TRIGGERS PAUSED`. The job
uses Supabase's migration ledger: it pushes only through the additive tenancy foundation, resolves the
protected owner email to exactly one Auth user, performs the count/digest-verified owner backfill,
marks only the now-equivalent data-free bootstrap migration as applied, and pushes the remainder. A
failure at any point leaves triggers paused and is safe to retry; never repair any other migration
version manually.

After cutover, run the private encrypted backup, staging restore, provider handshake, and connection
checks. Then run `operation=deploy` with `DEPLOY PRODUCTION`. After migration verification,
runtime-role rotation, function deployment, static publication, and automated health pass, perform the
**owner-only smoke**: sign in, read all seven screens, preview and cancel a harmless recordkeeping
command, inspect the connection/run status, and verify that no Telegram/model notification was invented.
Then rerun the same commit with `operation=verify` and enter `OWNER SMOKE PASSED`. This sequence prevents
a pre-deployment checkbox from masquerading as post-deployment evidence. The workflow never enables
invitations.

If any check is missing, stale, skipped, unknown, or malformed, stop with triggers paused. Do not turn a
failed gate into a warning and do not copy private evidence into workflow logs or GitHub artifacts.
