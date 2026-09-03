# Account lifecycle runbook

Use this only from a trusted operator machine. Never paste service-role, database, SMTP, bot, age, or provider credentials into chat, source control, command history, screenshots, or application logs.

## Invite an owner

Prerequisites:

- Gate 0 through Gate F, the owner-soak receipt, and every prerequisite in
  `docs/runbooks/friend-onboarding.md` are current for the exact production commit. The
  `--smtp-verified` flag is an operator acknowledgement, not an automated substitute for evidence.
- Custom SMTP is configured and a real OTP delivery test passed. Supabase's default mailer is not accepted for friend onboarding.
- The intended email was verified out of band.
- `STOCK_AGENT_SUPABASE_URL`, `STOCK_AGENT_SERVICE_ROLE_KEY`, and `INVITE_RECEIPT_PEPPER` are supplied through the operator's secret manager.

Validate without network access:

```bash
.venv/bin/python scripts/invite_user.py --email friend@example.com --smtp-verified --dry-run
```

Create the confirmed Auth identity and private invited profile:

```bash
.venv/bin/python scripts/invite_user.py --email friend@example.com --smtp-verified
```

The script prints only a pseudonymous receipt. It does not send an invite email. Send the application URL through the established private channel; the owner signs in with email OTP and must accept the current disclosure before the workspace activates.

## Owner-requested account deletion

The owner starts deletion in Settings, completes fresh email-OTP step-up, and types `DELETE MY ACCOUNT`. The application immediately revokes provider and Telegram authority, disables schedules and notifications, restricts the account to export/cancel/sign-out, and starts the 72-hour grace period.

Find the owner UUID and deletion-request UUID through the protected operator database procedure. Do not put either value in tickets or chat. Before the grace period expires, the deletion CLI must reject mutation:

```bash
.venv/bin/python scripts/delete_account.py \
  --owner-id OWNER_UUID \
  --deletion-request-id DELETION_UUID \
  --dry-run
```

After the grace period, type the exact owner-bound confirmation locally:

```bash
.venv/bin/python scripts/delete_account.py \
  --owner-id OWNER_UUID \
  --deletion-request-id DELETION_UUID \
  --confirm "DELETE AUTH OWNER_UUID"
```

The database purge commits first. The CLI then deletes the Supabase Auth user and verifies the profile cascade. Exit code `3` means active data is already gone but Auth deletion or verification must be retried with the same IDs and confirmation. Never recreate the user: the retained deletion tombstone intentionally blocks resurrection.

Expected receipt: `status=deleted`, deletion timestamp, and recovery-archive expiry timestamp. It contains no email, owner UUID, holdings, or credentials.

## Owner-requested ledger reset

Use this only when the owner deliberately wants to replace all portfolio recordkeeping while preserving research history. The owner first creates a fresh five-minute step-up receipt in the application. Create and verify an offline age recipient before continuing.

Preview exact affected row counts:

```bash
.venv/bin/python scripts/reset_owner_ledger.py \
  --owner-id OWNER_UUID \
  --step-up-receipt-id RECEIPT_UUID \
  --output /secure/path/ledger-export.json.age \
  --age-recipient AGE_PUBLIC_RECIPIENT \
  --dry-run
```

Apply only after checking those counts:

```bash
.venv/bin/python scripts/reset_owner_ledger.py \
  --owner-id OWNER_UUID \
  --step-up-receipt-id RECEIPT_UUID \
  --output /secure/path/ledger-export.json.age \
  --age-recipient AGE_PUBLIC_RECIPIENT \
  --confirm "RESET OWNER_UUID"
```

The encrypted export must exist with mode `0600` before any ledger row is deleted. The operation consumes the step-up receipt, verifies an empty holding projection, and creates an immutable non-financial reset receipt.

## Incident checks

- If Telegram cleanup is partial or unavailable, record that state truthfully and tell the owner older history requires manual deletion.
- If account purge fails, the transaction rolls back; do not delete the Auth identity.
- If Auth deletion fails after purge, rerun the same deletion command. The tombstone makes the database step idempotent.
- Never bypass the grace period by editing timestamps in production.
