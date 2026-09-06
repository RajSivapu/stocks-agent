# Production Schema Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile the exact inventoried production database to the reviewed Personal Stock Agent
V1 final schema, prove existing money-bearing rows are unchanged, and complete recovery/release gates
without fabricated migration history or duplicate scheduled runs.

**Architecture:** A manual protected workflow consumes the verified inventory receipt, captures and
authenticates an encrypted full legacy snapshot, then applies one immutable final-state SQL file
through the Supabase Management API in one locked transaction. One matching native/private receipt
truthfully records that reconciliation as a baseline; release and restore accept it without inventing
historical rows. Read-only receipts prove the final catalog and original-column data roots.

**Tech Stack:** Python 3.14, PostgreSQL 17, Supabase Management API, GitHub Actions, pytest.

**Spec:** `docs/design/2026-09-06-production-schema-reconciliation.md`

## Global Constraints

- Owner-only, suggestion-only, brokerage-free, and zero incremental cost.
- No production mutation without exact-current-main CI and the verified production inventory.
- Encrypt and authenticated-decrypt-test the legacy snapshot before the writer endpoint is used.
- One transaction owns the advisory lock, DDL, paired truthful receipts, and projected root proof.
- Never create historical migration rows for SQL that did not run.
- A write transport failure is unknown until read-only reconciliation proves committed or uncommitted.
- Never trigger a market, Telegram, Auth, Sites, release, or restore run merely to obtain evidence.

---

### Task 1: Authoritative read-only production inventory

**Files:**
- Modify: `scripts/inspect_production_schema_baseline.py`
- Modify: `.github/workflows/production-schema-inventory.yml`
- Test: `tests/test_inspect_production_schema_baseline.py`
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: exact-main CI run ID and masked Supabase project credentials.
- Produces: canonical `schema-inventory.json` with catalog, presence markers, and protected roots.

- [x] **Step 1: Implement bounded full-catalog and root inventory**
- [x] **Step 2: Pass focused PostgreSQL and workflow-contract verification**
- [x] **Step 3: Merge through exact-head and exact-main CI**
- [x] **Step 4: Run protected inventory and verify its canonical digest**
- [x] **Step 5: Record run `34029103876` and receipt `f1f08d635d59bb2e429c57deb1a2a9948d1ce631faa746bc8a19ea51372afb46`**

### Task 2: Immutable exact-baseline final-state SQL

**Files:**
- Create: `sql/migrations/20261003_release_ledger_acl_closure.sql`
- Create: `sql/reconciliation/20261004_production_schema_reconciliation.sql`
- Create: `tests/test_production_schema_reconciliation_sql.py`

**Interfaces:**
- Consumes: the verified 20260909-compatible catalog shape from inventory run `34029103876`.
- Produces: one bounded SQL program that creates the 17 absent public relations, current routines,
  roles, grants, policies, and the empty native ledger while preserving all legacy row projections.

- [x] **Step 1: Reconstruct the verified pre-state in disposable PostgreSQL**
- [x] **Step 2: Add the focused failing final-catalog and row-preservation test**
- [x] **Step 3: Author final-state objects without historical data backfills**
- [x] **Step 4: Prove final structure, projected-row equality, ACL closure, and retry failure**
- [x] **Step 5: Commit as `f4a84f0`**

### Task 3: Truthful reconciliation-baseline ledger support

**Files:**
- Modify: `scripts/deploy_owner_dashboard_api.py`
- Modify: `scripts/export_recovery_bundle.py`
- Modify: `tests/test_deploy_owner_dashboard_api.py`
- Modify: `tests/test_recovery_bundle.py`
- Modify: `tests/test_managed_isolated_restore.py`

**Interfaces:**
- Consumes: exact native/private `20261004` reconciliation receipt pair.
- Produces: `apply_release_migrations()` behavior that treats older migration files as subsumed
  without inserting their rows and still applies an exact contiguous future suffix.

- [x] **Step 1: Add failing paired-baseline, drift, and lookalike tests**
- [x] **Step 2: Implement exact reconciliation path/version/digest validation**
- [x] **Step 3: Preserve historical-prefix behavior when no baseline exists**
- [x] **Step 4: Pass the affected deploy, recovery, and managed-restore tests**
- [x] **Step 5: Commit as `a7a73af`**

### Task 4: Protected reconciliation runner and workflow

**Files:**
- Create: `scripts/reconcile_production_schema.py`
- Create: `.github/workflows/production-schema-reconciliation.yml`
- Create: `tests/test_reconcile_production_schema.py`

**Interfaces:**
- Consumes: inventory run ID/artifact, current-main SHA, exact-main CI run ID, project reference,
  access token, recovery key, and the immutable reconciliation SQL.
- Produces: `legacy-snapshot.enc`, a non-row sidecar, and
  `schema-reconciliation-receipt.json` containing only hashes, counts, identities, and status.

- [x] **Step 1: Add focused receipt, snapshot, identity, uncertainty, and workflow tests**
- [x] **Step 2: Implement exact receipt revalidation and one atomic bounded legacy snapshot**
- [x] **Step 3: Encrypt, tamper-test, and validate the snapshot before any writer call**
- [x] **Step 4: Implement the single locked transaction and projected-root assertions**
- [x] **Step 5: Implement read-only commit/uncommitted/ambiguous resolution with one retry maximum**
- [x] **Step 6: Add the manual protected, current-main/CI/inventory-bound workflow**
- [x] **Step 7: Pass focused verification and commit as `dc75384`; bind and fence the reviewed baseline in `97bf66a`**

### Task 5: Review, protected execution, restore, and release evidence

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/ROADMAP.md`
- Modify: this plan

**Interfaces:**
- Consumes: reviewed exact head, exact-head CI, exact-main CI, and the three production receipts.
- Produces: reconciled production schema, successful managed isolated restore/deletion, protected
  component release evidence, and a visible final V1 checkpoint.

- [x] **Step 1: Perform one focused independent safety review and resolve only material findings**
- [ ] **Step 2: Run focused reconciliation tests and the required repository gate once**
- [ ] **Step 3: Merge through exact-head and exact-main CI**
- [ ] **Step 4: Run the protected reconciliation and verify encrypted snapshot and receipt hashes**
- [ ] **Step 5: Run managed isolated restore and verify target deletion**
- [ ] **Step 6: Run protected component release when every required secret/transport preflight passes**
- [ ] **Step 7: Observe the next existing scheduled chain without triggering a duplicate**
- [ ] **Step 8: Mark V1-C2 through V1-C6 complete only when their receipts are present**
