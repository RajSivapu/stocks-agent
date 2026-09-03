# Owner-only cutover and soak

This runbook moves the existing owner's portfolio to the complete multi-user architecture before any
friend is invited. It is a controlled production operation, not a deployment shortcut. **Invitations
remain disabled** throughout cutover and the complete owner soak.

## Evidence boundary

Keep the manifest, step evidence, controller states, screenshots, database reports, and receipts under
ignored `artifacts/cutover/` with mode `0600`. Evidence files contain only status, UTC time, and a
SHA-256 hash of the private source evidence. They never contain an email, Auth UUID, Telegram ID,
holding, ticker, model output, URL token, database URL, or credential. A **missing, stale, skipped,
unknown, or failed** prerequisite is not a warning: do not start or advance.

Copy `config/cutover-manifest.local.json.example` to ignored
`config/cutover-manifest.local.json`, replace every sample timestamp, hash, commit, and count from the
actual private evidence, then set mode `0600`. The preparation manifest must prove fresh Gate 0 through Gate E evidence, a private encrypted backup
under 36 hours old, a verified restore under 30 days old, an immutable owner-identity evidence hash,
matching migration row counts/digests, paused legacy Routines, a paused old Telegram webhook mutation
path, and a reviewed rollback commit distinct from the candidate. Hash the validated capability and
acceptance evidence; do not copy their private sources into the manifest.

Prepare the first immutable state:

```bash
.venv/bin/python scripts/cutover_owner.py prepare \
  --manifest artifacts/cutover/manifest.json \
  --state artifacts/cutover/state-00.json
```

For each `next_step`, perform exactly that operation, retain private proof outside Git, and create a
four-field evidence file: `step`, `status`, `checked_at`, and `evidence_hash`. Advance to a new file;
never overwrite a prior state:

```bash
.venv/bin/python scripts/cutover_owner.py advance \
  --state artifacts/cutover/state-00.json \
  --evidence artifacts/cutover/evidence-01.json \
  --output artifacts/cutover/state-01.json
```

## Ordered operation

The controller enforces this sequence:

1. Reconfirm all legacy triggers and mutations are paused, then verify the fresh encrypted backup.
2. Run the owner migration/backfill, compare counts and digests, provision scoped machine roles, and
   deploy the candidate application while triggers remain paused.
3. Register the replacement Telegram webhook, connect one Claude Routine through its API-credential
   path, and complete a real no-write handshake. A provider green indicator is not proof.
4. Verify every private view and ledger projection. Exercise one harmless web recordkeeping preview,
   cancel it, then explicitly confirm one known-safe test record and its correction/void. Exercise one
   equivalent Telegram preview/Confirm/correction and compare the resulting receipts.
5. Only after replacement paths pass, revoke and remove the old runtime **service-role** secret,
   static Telegram owner configuration, broad `MARKET_AGENT_SECRET`, legacy Routine schedules, broad
   gateway secret, and obsolete public RPC grants. The previous reviewed bundle remains available as
   rollback code but no old endpoint remains reachable.
6. Resume web recordkeeping, Telegram recordkeeping, pre-market, intraday, post-market, and maintenance
   one at a time. Verify one path before advancing to the next.

Do not interpret a controller state as proof that an external operation happened. Each passing step
requires evidence from the actual target environment, and every state is protected by a digest.

## Immediate rollback

If any step fails, the next controller state is `rollback_required` and every mutation/scheduled path
returns to paused. Restore only the exact reviewed previous application/function bundle, rotate any
possibly exposed runtime credential, verify private reads and projections, and record the rollback:

```bash
.venv/bin/python scripts/cutover_owner.py rollback \
  --state artifacts/cutover/state-failed.json \
  --evidence artifacts/cutover/rollback-evidence.json \
  --output artifacts/cutover/state-rolled-back.json
```

Always retain additive owner columns and audit history; **never destructively down-migrate**. Any
ledger, identity, isolation, or data-integrity uncertainty keeps web mutations, Telegram mutations,
and all scheduled phases paused. A restored bundle is not permission to resume.

## Complete owner soak

With friend invitations still disabled, observe and hash evidence for:

- a full **pre-market** run;
- a **quiet intraday** run that sends no all-clear message;
- an **active intraday** test/run that produces only eligible edge alerts;
- the **post-market** run and grading receipts;
- **maintenance**, retention, projection checks, and missed-run detection;
- creation and independent age verification of the encrypted **backup**;
- one safely induced fixed-copy **operational alert**; and
- final counts/digests for ledger events, holdings projection, publications, message IDs, and run
  summaries.

Any mismatch or invented send/write claim triggers rollback state and a fresh soak after repair. Once
every controller step passes, finalize the hash-only receipt:

```bash
.venv/bin/python scripts/cutover_owner.py finalize \
  --state artifacts/cutover/state-final.json \
  --receipt artifacts/cutover/owner-soak-receipt.json
```

The final receipt proves only that the recorded evidence sequence passed. It has no private data and
keeps invitations disabled. Gate F may be signed only after separately checking the private evidence
against this receipt.
