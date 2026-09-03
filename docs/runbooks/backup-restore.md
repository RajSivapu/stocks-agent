# Encrypted backup and staged restore

## Recovery contract

This backup exists for full project loss, not for undoing one trade. Owner exports and ledger
corrections remain the normal recovery paths. The recovery point objective is 24 hours. The target
for a full-loss recovery, including identity rebinding and reconnection checks, is three business
days.

The backup contains durable application rows plus the minimum identity map (`owner_id`, email). It
does not contain Vault plaintext, provider trigger URLs, inbound token digests, pending pairing or
callback tokens, fresh-auth receipts, cached quotes, run claims, rate-limit buckets, or in-flight
schedule claims. The identity map exists in plaintext only inside the mode-0700 runner temporary
directory and is part of the `age` ciphertext before anything is uploaded.

A second `age` object carries four continuity values: the rate-limit, step-up, and pairing peppers,
and the evidence signing key. Database credentials, webhook secrets, the Telegram bot token, R2
credentials, and the Supabase service-role key are inventory entries only and must be rotated after
a restore. Vault trigger secrets are deliberately not exported; every Claude connection must be
reconnected.

The SQL contract snapshots every `app` table and its columns. Any unreviewed table or column makes
the database RPC fail closed. The Python verifier independently requires every durable dataset,
canonical row ordering, exact counts, row digests, relationship and portfolio-projection digests,
and deletion tombstones. An archive that contains a tombstoned owner in any live dataset or identity
map is rejected.

## One-time setup

1. Create an `age` identity offline on an encrypted removable device. Keep a second offline copy in
   a different physical location. Put only the public `age1...` recipient in the private workflow.
   Never put the private identity in GitHub, Supabase, Cloudflare, or the production Mac keychain.
2. Enable Cloudflare R2 and create a private Standard-storage bucket. Cloudflare currently requires
   completing the R2 subscription/checkout flow even when usage remains inside the included free
   allowance. The allowance is not a hard spending cap. R2 currently includes 10 GB-month of Standard
   storage, 1 million Class A operations, 10 million Class B operations, and free direct egress each
   month; usage above those allowances is billable. Review current terms before enabling it:
   <https://developers.cloudflare.com/r2/pricing/>.
3. Create one bucket-scoped R2 S3 token for the private backup workflow. Do not grant account-wide
   bucket administration. The workflow needs list, put, head, and delete for this bucket. R2's S3
   endpoint and credential shape are documented at
   <https://developers.cloudflare.com/r2/examples/aws/boto3/>.
4. Apply `ops/backup/r2-lifecycle.json` to the bucket. The script keeps 14 daily, 4 weekly, and 14
   key-material objects; the 35-day R2 lifecycle is the independent hard backstop. Cloudflare notes
   that lifecycle deletion can lag by about 24 hours:
   <https://developers.cloudflare.com/r2/buckets/object-lifecycles/>.
5. Create a separate **private** GitHub repository and copy the reviewed backup tooling plus
   `ops/backup/private-workflow.yml` to `.github/workflows/backup.yml`. Keep Actions permissions at
   read-only contents. Do not add an artifact upload step. Scheduled runs can be delayed, so the
   independent 36-hour monitor is the actual freshness alarm.
6. Provision the `stock_agent_backup_runtime` login with `scripts/provision_runtime_roles.py`. Store
   only its Supavisor session-mode URL in the private repository as `BACKUP_DATABASE_URL`. This login
   can execute the two export RPCs plus the bounded success-receipt RPC and cannot select any app,
   Auth, or Vault table directly.
7. Add these private-repository secrets:

   - `BACKUP_DATABASE_URL`
   - `BACKUP_AGE_RECIPIENT`
   - `BACKUP_KEY_MATERIAL_JSON` (an exact JSON object containing `APP_RATE_LIMIT_PEPPER`,
     `APP_STEP_UP_PEPPER`, `EVIDENCE_SIGNING_KEY`, and `TELEGRAM_PAIRING_PEPPER`)
   - `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BACKUP_BUCKET`

8. Deploy `ops/backup/r2-age-monitor` as a Cloudflare Worker. Bind the same private bucket as
   `BACKUP_BUCKET`; the code lists only `stock-agent/daily/`. Add only
   `TELEGRAM_BOT_TOKEN` and `OPERATIONAL_TELEGRAM_CHAT_ID` as Worker secrets. The monitor receives no
   Supabase URL, database credential, portfolio data, provider credential, or `age` key. R2 list
   results provide object key, upload time, and size; the monitor never reads an archive body. The
   Workers list API and prefix behavior are documented at
   <https://developers.cloudflare.com/r2/api/workers/workers-api-reference/>.
9. Keep Cloudflare billing notifications enabled. The Worker warns at the configured 8 GiB storage
   threshold and rejects a planned-operation budget at 80% of the current included operation
   allowances. Its bucket-only binding cannot inspect unrelated account billing usage, so the
   Cloudflare billing notification is the second guard.

## Daily verification

The private workflow must finish with a sanitized JSON line containing only status, schema version,
ciphertext digests, byte count, and object count. It must never print the archive, identities,
credentials, R2 keys, or object names. Success means all of the following occurred in one run:

1. one repeatable-read snapshot completed through the backup RPC contract;
2. archive verification passed;
3. application data and key material were separately encrypted with pinned `age`;
4. R2 `PUT` and `HEAD` length/digest checks passed;
5. count retention completed without a truncated list;
6. only after those remote checks, the exporter recorded ciphertext time, size, and digest for the
   aggregate operator-health view.

The age monitor sends fixed operational text if no daily ciphertext is newer than 36 hours. To test
the alert without touching production data, deploy a test Worker with an empty, test-only prefix and
invoke its scheduled handler at `/cdn-cgi/handler/scheduled`; Cloudflare documents scheduled-handler
testing at <https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/>.

## Restore drill and full-loss recovery

Never restore directly into production. Resume or create the staging Supabase project first, and
pause production scheduler invocations, Claude Routines, the legacy routine, and Telegram mutation
webhooks. Record the pause evidence outside Supabase.

1. Apply the exact reviewed migration set for the archive's schema version to staging. Do not run the
   restore until `machine.backup_export_catalog({"schema_version":1})` succeeds there.
2. Download the newest application-data ciphertext from R2 to a mode-0700 offline working directory.
   Download the matching key-material ciphertext only to the offline recovery workstation. Verify
   the R2 object length and recorded ciphertext SHA-256 before decryption.
3. Supply the offline `age` identity as a mode-0600 file. Configure staging-only
   `STAGING_POSTGRES_URL`, `STAGING_SUPABASE_URL`, and
   `STAGING_SUPABASE_SERVICE_ROLE_KEY`; never supply production endpoints.
4. Run the destructive staging command with the exact project reference:

   ```bash
   python -m ops.backup.restore_backup \
     --ciphertext /private/path/stock-agent-backup.age \
     --age-identity /offline/path/stock-agent.agekey \
     --project-ref stock-agent-staging \
     --production-triggers-paused \
     --confirm "RESTORE STAGING stock-agent-staging"
   ```

   The script rejects a project reference containing `prod` or `production`, recreates confirmed
   staging identities through the Supabase Admin Auth endpoint, maps every old owner UUID to the new
   UUID, truncates only the enumerated staging `app` tables, applies tombstones first, loads durable
   rows, and verifies counts and digests before committing a restore receipt. If database restore
   fails, it attempts to remove every Auth identity it created.
5. Confirm the restore receipt is `verified`. Confirm tombstoned owners have no profile, identity,
   or owner row. Confirm every holding matches the ledger projection. Confirm pending commands are
   cancelled, queued sends are suppressed, interrupted runs are failed, and open operational events
   are resolved.
6. Rotate every database login, service-role exposure, webhook secret, scheduler secret, Telegram bot
   token if compromised, R2 token, and SMTP key. Restore continuity values only from the separate
   key-material ciphertext, then destroy its plaintext working copy.
7. Re-register the Telegram webhook with the rotated secret. Recreate future run slots. Require every
   owner to reconnect Claude so a new trigger URL enters Vault and a real handshake succeeds. No
   connection is active merely because its historical row was restored.
8. Run the two-owner isolation suite, ledger projection verifier, quiet and active intraday fixtures,
   Telegram replay tests, and one synthetic Claude handshake. Only then may staging be considered a
   successful drill. Record the receipt date; release gates require a successful drill within the
   last 30 days.

If Auth identity creation succeeds but cleanup fails after a rejected restore, stop: do not retry the
whole restore. Remove the listed staging-only Auth identities through the dashboard/Admin API, verify
the staging `app` schema is empty, rotate the staging service-role key, and then restart from step 1.

## Quarterly drill record

Record the archive digest, source export time, restore receipt ID, start/end time, row counts,
relationship digest, projection digest, reconnection result, and operator. Store no email, ticker,
quantity, message ID, token, URL credential, or provider transcript in the drill record. A backup
creation without a complete restore and reconnection test is not a passed drill.
