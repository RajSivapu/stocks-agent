# Production Schema Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a protected read-only workflow that fingerprints the exact live schema and
protected legacy data roots needed to author the reconciliation migration.

**Architecture:** A focused Python module builds fixed catalog queries and validates bounded metadata
returned by the existing `SupabaseManagementApi`. A manual-only GitHub workflow binds the checkout to
current main and successful exact-main CI, runs only the Management API read-only endpoint, and uploads
the canonical receipt. No production rows or secret values appear in logs or artifacts.

**Tech Stack:** Python 3.14, Supabase Management API, GitHub Actions, pytest.

**Spec:** `docs/design/2026-09-06-production-schema-reconciliation.md`

## Global Constraints

- Read-only Management API endpoint only; no database writer endpoint.
- Output catalog metadata, counts, and SHA-256 roots only; never output portfolio rows.
- Current main and successful exact-main Owner dashboard verification are mandatory.
- No schedule, Telegram, Auth, Sites, brokerage, or temporary-project action.
- All responses and artifacts are bounded and fail closed on unknown fields or identities.

---

### Task 1: Catalog fingerprint module

**Files:**
- Create: `scripts/inspect_production_schema_baseline.py`
- Test: `tests/test_inspect_production_schema_baseline.py`

**Interfaces:**
- Consumes: `SupabaseManagementApi` and `canonical_json` from `scripts/managed_isolated_restore.py`.
- Produces: `inspect_production_schema(request, project_ref, main_sha) -> dict[str, object]` and CLI
  arguments `--production-project-ref`, `--main-sha`, and `--output`.

- [ ] **Step 1: Write failing query-boundary tests**

  Assert every request path is exactly `/v1/projects/<ref>/database/query/read-only`, the first query
  proves `supabase_read_only_user` and `transaction_read_only=on`, and a writer path is rejected.

- [ ] **Step 2: Run the focused test and confirm failure**

  Run: `.venv/bin/python -m pytest -q tests/test_inspect_production_schema_baseline.py`

- [ ] **Step 3: Implement fixed catalog and root queries**

  Query allowlisted `public` relations, columns, constraints, functions, triggers, policies, and ACLs.
  Compute protected table counts and roots inside PostgreSQL with ordered `jsonb_agg(to_jsonb(row))`
  and `digest(..., 'sha256')`; return only count/root pairs. Enforce the 20-character project identity,
  40-character lowercase Git SHA, response byte limit, exact top-level keys, and canonical sorting.

- [ ] **Step 4: Add malformed/secret-leak/bounds tests and pass them**

  Cover wrong role, unexpected schema, duplicate identities, row-shaped output, invalid digest, response
  overflow, and deterministic receipt hashing. Run the same focused test file.

- [ ] **Step 5: Commit the module**

  Commit: `feat(recovery): fingerprint production schema baseline`

### Task 2: Protected inventory workflow

**Files:**
- Create: `.github/workflows/production-schema-inventory.yml`
- Modify: `tests/test_inspect_production_schema_baseline.py`

**Interfaces:**
- Consumes: environment secrets `SUPABASE_ACCESS_TOKEN` and `SUPABASE_PROJECT_REF`.
- Produces: artifact `production-schema-inventory-<run_id>-<run_attempt>` containing only
  `schema-inventory.json`.

- [ ] **Step 1: Write failing workflow-contract tests**

  Assert manual dispatch only, pinned actions, `owner-dashboard-production`, current-main/CI binding,
  locked dependencies, secret-backed project identity, read-only script invocation, artifact retention,
  and absence of restore/release/schedule commands.

- [ ] **Step 2: Run the focused test and confirm failure**

  Run: `.venv/bin/python -m pytest -q tests/test_inspect_production_schema_baseline.py`

- [ ] **Step 3: Implement the workflow**

  Checkout `main`, verify `GITHUB_REF`, `GITHUB_SHA`, remote main, and a successful exact-SHA
  `owner-dashboard-ci.yml` run. Install `requirements.lock`, run the inspector with secrets passed only
  through environment variables, and upload the one receipt for 90 days.

- [ ] **Step 4: Pass focused verification and commit**

  Run the focused test file and `git diff --check`. Commit:
  `feat(recovery): add protected schema inventory workflow`.

### Task 3: Review, merge, and execute inventory

**Files:**
- Modify after receipt: `PROJECT_STATUS.md`
- Modify after receipt: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: exact reviewed PR head, exact-head CI, merged main SHA, exact-main CI.
- Produces: one live read-only inventory artifact and its workflow URL/digest.

- [ ] **Step 1: Obtain two focused independent reviews**

  Review the exact head for data disclosure, Management API path containment, current-main binding,
  catalog completeness, and deterministic validation. Resolve only Critical or Important findings.

- [ ] **Step 2: Run one combined focused verification**

  Run: `.venv/bin/python -m pytest -q tests/test_inspect_production_schema_baseline.py tests/test_managed_isolated_restore.py`
  and `git diff --check`.

- [ ] **Step 3: Merge through exact-head and exact-main CI**

  Push, open the PR, wait for exact-head CI, merge, and wait for exact-main CI. Do not dispatch release
  or restore workflows.

- [ ] **Step 4: Dispatch inventory exactly once from main**

  Use `gh workflow run production-schema-inventory.yml --ref main`. Wait for completion, download the
  artifact, verify the receipt SHA and main binding, and inspect only schema metadata/count/root fields.

- [ ] **Step 5: Record the authoritative checkpoint**

  Update the canonical status files with the inventory run, digest, actual catalog shape, and the exact
  next reconciliation boundary. Never include project identities, tokens, URLs with credentials, or
  portfolio rows.
