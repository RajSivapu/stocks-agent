# One-friend onboarding gate

This release is invite-only. Complete the current owner's cutover and soak before exposing any
friend's identity, portfolio, Telegram account, or provider account. Onboarding one person is a
production security exercise, not permission for general signup.

## Hard prerequisites

Every item must have current private evidence:

- Gate 0 through Gate F pass for the exact deployed commit; no `skipped`, `unknown`, or mock result.
- The hash-only **owner-soak receipt** says `owner_soak_complete` and invitations are still disabled.
- The production encrypted backup is under **36 hours** old and the staging restore drill is under
  **30 days** old.
- **Custom SMTP** uses a verified operator-controlled domain. A six-digit OTP was received and entered
  from a **non-team** address in an iOS or Android **phone mail client**; Supabase's default mailer is
  not accepted.
- The application URL, privacy notice, risk disclosure, deletion behavior, support channel, and
  operator availability have been reviewed from the friend-facing perspective.
- The friend has an eligible **separate Claude account** and understands its subscription usage and
  daily Routine allowance. There is no paid model-API fallback.
- Public signup is disabled, no brokerage surface exists, and only the offline operator invitation
  command can create an invited profile.

If any prerequisite is missing or stale, stop. Do not compensate with the operator's Claude account,
a shared credential, a public spreadsheet, a local Mac process, or manual database edits.

## Synthetic owner first

Before inviting a person, create a new synthetic owner in staging and complete the entire flow:

1. Receive the **six-digit OTP** in a phone mail client and accept the current privacy/risk/provider
   **consent** versions.
2. Verify all seven screens and attack owner A/B IDs through browser-origin PostgREST and Edge calls.
3. Preview, cancel, confirm, and correct one synthetic ledger record; verify the holding projection.
4. Use an independent synthetic provider account/environment to complete an **unassisted** setup from
   the connection kit and a **real no-write handshake**.
5. Complete **private Telegram** pairing, preview/cancel/Confirm recordkeeping, status, and unlink.
6. Download the JSON **account export** and ledger CSV; verify neither contains reusable credentials
   or raw provider/Telegram secrets.
7. Request and cancel deletion with fresh OTP, then use another synthetic identity to finish
   **account deletion** and prove tombstone-first restore behavior.

Delete the synthetic Auth identity and provider/Telegram credentials after evidence is hashed. A
passing local mock or provider-green screen does not satisfy this step.

## Invite one trusted friend

Invite exactly **one trusted friend** through the offline command in
`docs/runbooks/account-lifecycle.md`. Verify the email address out of band. Send only the static
application URL over the established private channel; the script does not send a magic link and the
operator never asks for the friend's OTP.

The friend must personally:

1. sign in by email OTP and accept every disclosure;
2. review that Stock Agent is suggestion-only with **no brokerage** capability;
3. connect a **separate Claude account**, create their own environment/Routine/credentials from the
   kit **unassisted**, and complete the application-fired no-write handshake;
4. pair their own private Telegram chat;
5. add or import only records they understand, always through preview and Confirm;
6. download an account export and ledger CSV; and
7. read the account deletion/grace-period and manual older-Telegram-cleanup explanation.

Never batch owners into one model prompt, run, credential, transcript, database query export,
support screenshot, incident attachment, or backup evidence sample. Never view or handle the friend's
OTP, Claude token, inbound credential, holdings, or Telegram IDs for convenience.

## First complete friend cycle

Keep every other invitation disabled while observing one pre-market, quiet and active intraday,
post-market, maintenance, backup, and operational-alert cycle. Compare run slots, evidence freshness,
policy outcomes, actual database writes, publication receipts, message IDs, and provider usage. Review
notification noise, source failures, ledger projection, backup age, isolation attacks, and how much
help the setup required.

Support is best-effort from a **single operator**; there is no 24/7 monitoring, trade help, guaranteed
response time, or investment advice. Do not ask a friend to wait on an unmonitored alert before acting
through their broker.

Any tenant-isolation, ledger, SMTP, OTP, provider, Telegram, recovery, deletion, source-integrity,
delivery-truthfulness, or operator-capacity failure must **disable further invitations**. Repair the
issue, rotate affected credentials, repeat staging acceptance, and repeat a complete owner/friend
cycle as applicable. Do not downgrade the failure into documentation.

Only after the first friend cycle is reviewed may `friend_onboarding_cycle` receive signed-manual
evidence in the final Gate A-G report. Retain screenshots privately; the report stores hashes only.
