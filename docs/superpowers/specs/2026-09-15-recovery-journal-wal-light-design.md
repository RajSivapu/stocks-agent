# WAL-Light Recovery Journal Compaction Design

## Goal

Reclaim the oversized protected recovery-journal relation without repeating the
large `DELETE` transaction that exhausted PostgreSQL WAL space, while preserving
one authenticated terminal checkpoint for every existing release identity and a
durable recovery path if the database connection fails after truncation.

## Constraints

- Keep the application owner-only, suggestion-only, and receipt-backed.
- Add no paid, premium, trial, brokerage, or metered dependency.
- Do not invoke market collection, analysis publication, or Telegram delivery.
- Preserve committed application data outside the redundant recovery-journal
  versions explicitly approved for removal.
- Never mutate the journal until the retained encrypted rows are authenticated,
  exported, uploaded as a short-retention GitHub artifact, and exactly bound to
  the reviewed main workflow run.
- Keep the release mutation advisory lock and require the singleton release lease
  to be resolved and expired.
- Fail closed on malformed identities, unauthenticated ciphertext, inventory
  drift, unexpected partial data, artifact mismatch, or inexact readback.

## Backup format and artifact boundary

The prepare phase reads the latest row for each
`(project_ref,candidate_sha,run_id,run_attempt)` identity while holding the
protected advisory lock. It authenticates every Fernet ciphertext and validates
the embedded terminal release journal before writing a deterministic JSON bundle.
The bundle preserves the exact `sequence`, identity columns, ciphertext bytes,
and `captured_at` value. A small manifest binds its SHA-256 digest, byte count,
source inventory, retained identities, terminal statuses, reviewed main SHA, and
project reference.

The workflow uploads the bundle and manifest with one-day retention and verifies
the GitHub artifact ID, name, digest, originating workflow run, repository, main
SHA, and bounded size. Mutation receives that verified binding as mandatory input.
The recovery key is never included in the artifact.

An optional `resume_backup_artifact_id` dispatch input downloads a still-live
backup from an earlier failed run. The workflow verifies that artifact against the
same repository, workflow, and exact current main SHA, authenticates the bundle,
then uploads a fresh one-day copy before any database mutation.

## WAL-light mutation and recovery

The apply phase authenticates the local bundle again, acquires the protected
advisory lock, checks the lease, and locks the journal table. A normal source must
match the manifest's full approved inventory and exact latest-row digest. A
previously interrupted source may contain zero or a strict subset of retained
rows, but every present row must byte-for-byte match the bundle.

For a full uncompact source, `TRUNCATE ... CONTINUE IDENTITY` commits separately.
This releases the old relation without row-by-row delete WAL. The exact unique
identity index is created while the table is empty. Retained rows are restored
with `OVERRIDING SYSTEM VALUE` and idempotent identity upserts, committing one row
at a time so an interrupted restore can resume from the artifact. The identity
sequence is set to the retained maximum, the table is analyzed, and an exact
readback validates every identity, sequence, timestamp, ciphertext digest, group
count, and index definition. `VACUUM FULL` is not used.

If the table already equals the retained bundle, apply is a no-op apart from
verification and receipt creation. Any other state fails closed and leaves the
artifact available for an explicit resume.

## Receipt and workflow behavior

The bounded V2 receipt records the source and final inventories and sizes, lease
state, terminal-status counts, retained identity digest, whether this run
truncated or resumed, the WAL-light method, and the exact backup artifact binding.
The receipt is uploaded and bound to the exact workflow run using the existing
90-day evidence retention policy.

The workflow remains manual, exact-review-bound, exact-main-CI-bound, protected by
the production environment and shared release concurrency group, and contains no
collector or Telegram command.

## Verification

Disposable PostgreSQL tests must prove that:

- a delete-blocking trigger does not prevent successful compaction;
- the backup preserves all retained values and detects tampering;
- mutation rejects a missing or malformed artifact binding;
- a zero-row and a partial post-truncate state resume to the exact retained set;
- unexpected partial rows fail closed;
- a second apply is idempotent;
- PostgreSQL statement logs contain `TRUNCATE` and no journal `DELETE` or
  `VACUUM FULL`;
- workflow ordering makes upload and artifact verification prerequisites of
  apply, and the optional resume path is exact-main bound.

Production execution waits for Supabase to restore database connectivity. It then
uses the protected workflow once, verifies the receipt and live readback, and does
not start a duplicate market or Telegram run.
