# WAL-Light Recovery Journal Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failed delete-and-vacuum compactor with a durable backup-first, resumable TRUNCATE-and-restore workflow.

**Architecture:** A read-only prepare phase authenticates and exports the exact retained encrypted journals plus a digest manifest. GitHub Actions uploads and verifies a one-day artifact before a separate apply phase may acquire the protected lock, revalidate database state, commit `TRUNCATE ... CONTINUE IDENTITY`, and idempotently restore and verify the retained rows.

**Tech Stack:** Python 3.11+, pytest, PostgreSQL/psycopg, Fernet, GitHub Actions, PyYAML

**Spec:** `docs/superpowers/specs/2026-09-15-recovery-journal-wal-light-design.md`

## Global Constraints

- Keep the application owner-only, suggestion-only, receipt-backed, and at zero incremental cost.
- Do not invoke a market collector, analysis publication, brokerage path, or Telegram delivery.
- Do not authorize a paid Supabase upgrade or deletion of committed data outside the approved redundant journal versions.
- Require a verified short-retention encrypted backup artifact before any journal mutation.
- Keep the protected advisory lock, resolved/expired lease gate, exact reviewed-main trust gate, and exact readback.
- Use `TRUNCATE ... CONTINUE IDENTITY`; do not use a journal `DELETE` or `VACUUM FULL`.
- Make recovery from zero or an exact partial retained set explicit and idempotent.
- Wait for database access to return before production execution.

---

### Task 1: Build and verify the deterministic encrypted backup

**Files:**
- Modify: `scripts/compact_recovery_journals.py`
- Modify: `tests/test_recovery_journal_compaction.py`

**Interfaces:**
- Produces: `prepare_recovery_journal_backup(*, admin_url: str, project_ref: str, main_sha: str, recovery_key: bytes, backup_path: Path, manifest_path: Path, expected_rows: int | None, expected_groups: int | None) -> dict[str, object]`.
- Produces: `verify_recovery_journal_backup(*, backup_path: Path, manifest_path: Path, project_ref: str, main_sha: str, recovery_key: bytes, expected_rows: int | None, expected_groups: int | None) -> dict[str, object]`.
- Consumes: the existing terminal-journal authentication, project/SHA/run validators, release lease, and advisory lock.

- [x] **Step 1: Write failing backup-preservation and tamper tests**

Create two terminal identities with duplicate earlier versions and distinct exact
`captured_at` values. Call the wished-for prepare API and assert the bundle's
literal identity fields, sequence values, timestamp strings, ciphertext hashes,
manifest source inventory, retained count, and terminal status counts. Mutate one
ciphertext character and assert `verify_recovery_journal_backup()` raises
`recovery journal backup digest mismatch` before opening a database connection.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_recovery_journal_compaction.py -q`

Expected: FAIL because `prepare_recovery_journal_backup` and
`verify_recovery_journal_backup` do not exist.

- [x] **Step 3: Implement the minimal deterministic bundle and manifest**

Write canonical JSON with mode `0600`, exclusive non-symlink output paths, and a
192 MiB bound. Bundle records use the exact shape:

```python
{
    "sequence": int(row["sequence"]),
    "project_ref": row["project_ref"],
    "candidate_sha": row["candidate_sha"],
    "run_id": row["run_id"],
    "run_attempt": row["run_attempt"],
    "ciphertext": bytes(row["ciphertext"]).decode("ascii"),
    "captured_at": row["captured_at"].isoformat(),
}
```

The manifest stores the bundle SHA-256 and bytes, source rows/groups/sizes,
retained rows and identity digest, project/SHA, terminal statuses, and creation
time. Verification enforces exact keys and re-authenticates every ciphertext.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_recovery_journal_compaction.py -q`

Expected: PASS.

- [x] **Step 5: Commit the backup boundary**

```bash
git add scripts/compact_recovery_journals.py tests/test_recovery_journal_compaction.py docs/superpowers/specs/2026-09-15-recovery-journal-wal-light-design.md docs/superpowers/plans/2026-09-15-recovery-journal-wal-light.md
git commit -m "feat: stage authenticated recovery journal backup"
```

### Task 2: Replace row deletion with resumable WAL-light restore

**Files:**
- Modify: `scripts/compact_recovery_journals.py`
- Modify: `tests/test_recovery_journal_compaction.py`

**Interfaces:**
- Produces: `apply_recovery_journal_backup(*, admin_url: str, project_ref: str, main_sha: str, recovery_key: bytes, backup_path: Path, manifest_path: Path, backup_artifact: Mapping[str, object], expected_rows: int | None, expected_groups: int | None) -> dict[str, object]`.
- Consumes: Task 1's verified bundle/manifest and mandatory artifact binding `{id,name,digest,workflow_run_id}`.

- [ ] **Step 1: Write failing mutation and recovery tests**

Add integration tests that install a statement-level delete trigger which raises,
then prepare and apply a backup successfully. Record PostgreSQL statements and
assert a journal `TRUNCATE` occurred while journal `DELETE` and `VACUUM FULL` did
not. Add cases for malformed artifact binding, zero rows after manual TRUNCATE,
one exact restored row, one unexpected row, and a second idempotent apply.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_recovery_journal_compaction.py -q`

Expected: FAIL because `apply_recovery_journal_backup` is absent and the existing
implementation invokes the delete trigger.

- [ ] **Step 3: Implement minimal TRUNCATE-and-restore behavior**

Under the advisory lock, accept only one of these literal states:

```python
source_matches = (rows, groups) == (manifest_rows, manifest_groups) and latest_digest == retained_digest
retained_subset = rows == groups and every_present_row_matches_bundle
```

For `source_matches` with redundant rows, execute and commit
`TRUNCATE ... CONTINUE IDENTITY`; create the exact unique identity index; restore
records with `OVERRIDING SYSTEM VALUE ... ON CONFLICT
(project_ref,candidate_sha,run_id,run_attempt) DO UPDATE`, one autocommit statement
per record; set the identity sequence to the retained maximum; run `ANALYZE`; and
compare the full readback to the bundle. A complete exact retained set skips
TRUNCATE. Any other state raises without further mutation.

- [ ] **Step 4: Emit the bounded V2 receipt**

Record the required artifact binding, source and final inventories/sizes,
`method="truncate_restore"`, `truncated`, `resumed`, removed redundant count,
terminal statuses, retained digest, exact unique-index result, and
`vacuum_full=False`.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_recovery_journal_compaction.py -q`

Expected: PASS with real disposable PostgreSQL behavior.

- [ ] **Step 6: Commit the WAL-light mutation**

```bash
git add scripts/compact_recovery_journals.py tests/test_recovery_journal_compaction.py
git commit -m "fix: compact journals with resumable truncate restore"
```

### Task 3: Put the verified GitHub artifact before mutation

**Files:**
- Modify: `.github/workflows/recovery-journal-compaction.yml`
- Modify: `tests/test_owner_dashboard_release_workflow.py`

**Interfaces:**
- Produces: optional dispatch input `resume_backup_artifact_id`.
- Produces: one-day artifact `recovery-journal-backup-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}` containing only the encrypted bundle and manifest.
- Consumes: Task 1 `prepare`/`verify` and Task 2 `apply` CLI commands.

- [ ] **Step 1: Write the failing workflow behavior test**

Parse the workflow and assert step order is authenticate, checkout, prepare-or-
download, offline verify, upload backup, bind backup, apply, upload receipt, bind
receipt. Assert the apply command receives the bound artifact outputs; the resume
path validates the source artifact repository, workflow path, exact current main
SHA, digest, expiry, and 192 MiB size; backup retention is one day; the receipt
retention remains 90 days; and no collector/Telegram entrypoint exists.

- [ ] **Step 2: Run the workflow test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_owner_dashboard_release_workflow.py::test_recovery_journal_compaction_workflow_is_exact_review_bound_and_trigger_free -q`

Expected: FAIL because the current workflow mutates before uploading any backup.

- [ ] **Step 3: Implement the backup-first and resume orchestration**

Add the optional numeric input. A fresh run calls `prepare`; a resume run downloads
and validates its exact artifact. Both call `verify`, upload a fresh one-day copy,
bind its API metadata to this exact run/main SHA, and only then call `apply` with
the artifact ID/name/digest/workflow-run ID. Keep pinned action SHAs, environment,
permissions, and shared production concurrency.

- [ ] **Step 4: Run focused compaction/workflow tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_recovery_journal_compaction.py tests/test_owner_dashboard_release_workflow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit workflow orchestration**

```bash
git add .github/workflows/recovery-journal-compaction.yml tests/test_owner_dashboard_release_workflow.py
git commit -m "fix: require durable backup before journal compaction"
```

### Task 4: Verify, review, merge, and execute once PostgreSQL recovers

**Files:**
- Modify after successful protected execution: `docs/rollouts/2026-09-15-personal-stock-agent-v1.md` or the current V1 rollout checkpoint selected by repository convention.

**Interfaces:**
- Consumes: Tasks 1-3, GitHub protected review/CI/main gates, Supabase support recovery, and the next normal scheduled publication receipt.
- Produces: reviewed merge, exact-main CI, protected V2 compaction receipt, live database readback, then the existing protected release and normal-schedule proof.

- [ ] **Step 1: Run proportional and full local verification**

Run the focused compaction/workflow tests, then `npm run test:all`. Inspect the
worktree diff and confirm no secret, paid provider, brokerage execution, collector
trigger, or Telegram invocation was added.

- [ ] **Step 2: Request review through the protected PR path**

Push the branch, create a PR, wait for exact-head CI, and add the exact three-line
owner compaction approval only after CI succeeds:

```text
OWNER_RECOVERY_JOURNAL_COMPACTION_APPROVAL_V1
reviewed_sha=<exact PR head SHA>
pr_ci_workflow_run_id=<successful exact-head CI run ID>
```

- [ ] **Step 3: Merge and verify exact main**

Merge only with green exact-head CI and valid owner approval. Wait for the exact
main push CI and bind its run ID to the protected workflow dispatch.

- [ ] **Step 4: Wait for Supabase database recovery without live side effects**

Use only read-only connection/inventory checks. Do not start or replay a market or
Telegram run. If PostgreSQL remains unavailable, retain the active heartbeat and
wait for the support response.

- [ ] **Step 5: Run protected compaction once and verify the receipt**

Dispatch the protected workflow with candidate/main SHA, reviewed SHA, PR CI, main
CI, and PR number. If it fails after TRUNCATE, rerun only with the emitted
`resume_backup_artifact_id`. Verify 45 rows/groups, exact retained digest/index,
reduced relation/database bytes, artifact binding, and resolved expired lease.

- [ ] **Step 6: Finish the existing V1 release and production proof**

Continue the already-authorized protected release when the zero-cost service gate
permits it. Prove behavior from the next normal scheduled pre-market or post-market
publication receipt and later nonblocking research progress; never trigger a
duplicate live run merely to inspect output.
