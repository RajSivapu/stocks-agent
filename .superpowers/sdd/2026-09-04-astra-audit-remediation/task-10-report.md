## Task 10 report — Rebuild release verification and zero-cost recovery evidence

Implementation commit: `517fdec1b3831ba21539614175d6d5e1611fe8dd`

## Controller fix round 1

Commit: `214a9e201bdf5b761bda0eae46552956da59ab55`

- RED: authoritative-source and mandatory-encryption tests failed against the prior trust-by-shape implementation.
- GREEN: only the four Task 10 test files were run; `75 passed in 0.41s`.
- Release verification now derives CI/deploy/source/rollback gates from a separate authoritative record set and enforces a seven-day scheduled-receipt freshness bound.
- Recovery artifacts require caller encryption and decrypt verification, keep plaintext in a temporary directory, enforce exact per-dataset schemas, and require an isolated restore identity plus read-back records.
- Gateway bytes restore before dashboard cleanup; cleanup failure is reported only after the restore attempt.
- Source reconciliation now carries stored report-outbox delivery timestamps and stored suppression reasons rather than inventing suppression or original-delivery evidence.

Residual risk: deployment receipt collection must still populate the authoritative records from protected CI/deploy/database sources; no production operation was run here.

### Red/green evidence

- RED: `.venv/bin/python -m pytest -q tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py tests/test_deploy_owner_dashboard_api.py tests/test_recovery_bundle.py` initially failed during collection because the recovery bundle modules did not exist.
- GREEN: the same exact four focused files passed after implementation: `72 passed in 0.19s`.
- Hygiene: `git diff --check` passed. No full suite, live deployment, database, Telegram, cloud backup, or scheduled run was invoked.

### Changed files

- `scripts/verify_personal_stock_agent_v1.py` and its tests now reject on-demand, dry-run, duplicate, pre-merge/stale, incomplete-stage, and delivery-ID-less receipts; retained canonical record hashes are recomputed.
- `scripts/verify_owner_dashboard_deployment.py` and its tests now obtain and reconcile immutable intelligence event/ranking/packet/report data plus durable report-outbox state, recomputing retained record and render hashes.
- `scripts/deploy_owner_dashboard_api.py` and its tests bind every main deployment failure path after a gateway change to the captured, verified gateway restore artifact.
- `scripts/export_recovery_bundle.py`, `scripts/verify_recovery_bundle.py`, and `tests/test_recovery_bundle.py` add canonical NDJSON recovery export/isolated-restore verification for holdings, transactions, commands, runs, packets, reports, publications, roles, and schema version.
- `README.md` documents the local-only, caller-supplied encryption boundary.

### Residual risks

- Production remains unclaimed until an independently collected, post-merge normal scheduled receipt supplies all typed gates and canonical source bodies.
- Encryption is intentionally a caller-owned local command; operators must choose and validate their own local encryption/retention procedure.
- The recovery verifier compares data supplied from an isolated restored environment; it does not create, connect to, or mutate a database itself.

## Controller recovery completion — 2026-09-05

- Replaced caller-shaped release/recovery inputs with protected GitHub deployment/artifact reads and a restricted, repeatable-read PostgreSQL evidence source. Candidate Git objects, migration/function/static bytes, stage identities, times, report/outbox bindings, and captured rollback bytes are recomputed before a release receipt can be accepted.
- Added the dedicated `suppression_reason` migration (`20260926`) and its repository/handler/verifier wiring. Existing reasonless suppression rows are deliberately not backfilled and therefore cannot become release evidence.
- Added the `20260927` SELECT-only evidence-reader migration. Recovery requires a guarded, distinct restored project/database identity, exact allowlisted schemas, canonical hashes, and a one-to-one report/publication receipt chain.
- Recovery export now requires an authenticated binary encryption/decryption boundary, rejects readable/copy output, probes a changed ciphertext byte, and removes newly created artifact/sidecar files after export or verification failures.
- Retained predeployment gateway bytes and capture metadata for the immutable rollback artifact before release mutation. Gateway restoration remains ordered before dashboard cleanup.

### Final local evidence

- `.venv/bin/python -m pytest -q tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py tests/test_deploy_owner_dashboard_api.py tests/test_recovery_bundle.py tests/test_intelligence_controller_sql.py` — `132 passed in 13.58s`.
- `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/market-briefing-gateway/_shared/repository_test.ts supabase/functions/market-briefing-gateway/_shared/handler_test.ts` — `57 passed`.
- These are fixture/local PostgreSQL checks only. They do not claim a live migration, protected deployment, production database read, recovery drill, Telegram delivery, provider/model call, brokerage operation, or full-suite result.

## Controller acceptance remediation — 2026-09-05

- Added the protected `owner-dashboard-release.yml` producer. A successful main CI completion or an environment-protected explicit dispatch binds checkout, reviewed SHA, CI run, merged PR, and GitHub Deployment to one candidate before it invokes the existing protected deploy script. It does not start or duplicate a market run.
- The workflow captures prior gateway source as an immutable artifact, writes canonical candidate/deployment/workflow/migration/function/static receipt evidence, uploads a release record artifact, and attaches that immutable artifact identity to the production Deployment status. Candidate/ref mismatches fail before mutation.
- Replaced the deployer's fixed 20260906–08 migration list with a canonical discovery manifest. It hashes and applies every ordered candidate SQL migration exactly once per protected invocation, including 20260926/27 and later additions; deploy and verifier receipts use the same `{path, version, sha256}` form.
- Release verification now distinguishes the authoritative `dry_run: false` execution flag from separately shaped zero-side-effect evidence. Missing, null, true, string, and numeric flags fail closed.

### Final local evidence

- `.venv/bin/python -m pytest -q tests/test_deploy_owner_dashboard_api.py tests/test_owner_dashboard_release_workflow.py tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py` — `90 passed in 10.63s`.
- No workflow, deployment, database migration, scheduled run, recovery drill, Telegram send, provider/model call, brokerage action, or full suite was invoked.

## Controller acceptance remediation round 4 — 2026-09-05

- Hardened the protected release workflow before mutation: it exports `GH_TOKEN`, requires the protected `SUPABASE_ACCESS_TOKEN`, installs the pinned Node/Python verification dependencies, and resolves the pinned Supabase CLI before it can create a Deployment.
- Dispatch inputs are only hints. The workflow queries GitHub for the current `main` ref, exact successful CI run/path, merged main PR, and exact SHA review before creating the production-wide-concurrent deployment.
- Successful releases now carry predeployment gateway capture plus isolated rollback-readiness evidence instead of pretending a successful production deployment was rolled back. Failed releases retain the existing `rolled_back` restore/cleanup receipt path.
- Kept the verified rollback worktree until release-record artifact upload and success status complete. Any subsequent failure invokes the dedicated restoration command before posting failure status; Python restoration runs before cleanup, even if cleanup then fails.
- Added a transactional migration hash ledger. Candidate migrations are rehashed, prior ledger rows must match exactly, pending DDL and its immutable ledger row share one transaction, and receipts separate complete candidate/applied/skipped sets. `20260926` now guards its named constraint for replay safety.
- Replaced hard-coded dry-run claims with separate restricted-reader before/after snapshots around a safe non-scheduling `evaluate_alert_rules --dry-run` command. The canonical record retains queried IDs, counts, hashes, command hash, and zero deltas.

### Final local evidence

- Focused release/recovery contracts: `.venv/bin/python -m pytest -q tests/test_deploy_owner_dashboard_api.py tests/test_owner_dashboard_release_workflow.py tests/test_verify_personal_stock_agent_v1.py tests/test_verify_owner_dashboard_deployment.py` — `94 passed in 9.60s`.
- No workflow, deployment, database migration, scheduled run, recovery drill, Telegram send, provider/model call, brokerage action, or full suite was invoked.
