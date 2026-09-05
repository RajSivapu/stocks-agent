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
