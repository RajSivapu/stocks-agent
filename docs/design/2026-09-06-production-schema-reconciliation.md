# Production Schema Reconciliation Design

## Goal

Bring the live Supabase database to the reviewed Personal Stock Agent V1 schema without inventing
historical migration receipts, changing existing portfolio facts, or weakening the recovery gates.

## Constraints

- The app remains owner-only, suggestion-only, brokerage-free, and zero incremental cost.
- Production currently has no native or private migration ledger and only 13 of 24 required recovery
  relations. Missing relations are not equivalent to empty datasets.
- Historical migrations are not safe to replay blindly because some update legacy rows, replace
  functions and constraints, and upsert schedule configuration.
- No production write occurs until a current-main, successful-CI, manual protected workflow has
  captured an encrypted legacy snapshot and verified the exact catalog baseline.
- DDL, truthful receipts for SQL executed now, and pre/post financial-root equality are one database
  transaction. A transport timeout is `unknown`, never `failed`; read-only reconciliation decides
  whether the transaction committed before any retry.

## Architecture

The rollout has two schema stages. The first stage is a read-only catalog inventory. It uses only the
Supabase Management API read-only SQL endpoint and emits a bounded metadata receipt: relation and
column shapes, constraints, functions, triggers, policies, grants, and deterministic server-side row
roots/counts for protected legacy tables. It never returns row contents.

The second stage is authored only against that exact receipt. It uses one new immutable reconciliation
SQL file containing the missing final-state objects and latest reviewed routine/ACL definitions. It
does not replay historical data migrations or synthesize historical ledger rows. The protected runner
encrypts the legacy data snapshot before mutation, revalidates the catalog receipt, acquires the
database advisory lock, executes one `BEGIN ... COMMIT` request, records only the reconciliation SQL
actually executed, and proves protected legacy roots did not change.

After the migration receipt is accepted, the existing managed isolated restore must pass and delete
its temporary project. The protected component release remains a separate gate because its native
Sites transport and protected configuration are not yet available.

## Evidence model

- `schema-inventory.json`: non-secret catalog metadata, exact main SHA, deterministic digest.
- `legacy-snapshot.enc`: encrypted authenticated row-level recovery artifact retained for 90 days.
- `schema-reconciliation-receipt.json`: before/after catalog digests, protected row roots, executed
  reconciliation path/hash, transaction outcome, and read-only uncertainty reconciliation.
- Managed restore and protected release retain their existing independent receipts.

## Failure behavior

- Unexpected catalog shape: stop before snapshot or mutation.
- Snapshot encryption or validation failure: stop before mutation.
- SQL assertion/DDL/ledger failure: transaction rolls back.
- Lost write response: make no retry; query the reconciliation identity, final catalog fingerprint,
  and protected roots read-only. Accept only the complete expected state or prove the pre-state before
  one retry.
- Root mismatch or ambiguous state: retain evidence and stop; never run schedules or publication to
  manufacture proof.
