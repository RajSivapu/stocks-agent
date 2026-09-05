# GPT-6 Astra Audit Remediation — Rollout Record

Status: **local candidate only; rollout has not started**.

## Immutable boundary

- Audit baseline: `432d647ef911ff63da427097f02a852e18038b62`.
- Consolidated local-gate code candidate: `a0f8158c959e788b2a302ae6411328ec6e98a707`.
- Owner-only, signup/invitations disabled, suggestion-only, and no brokerage execution.
- Zero incremental cost: no paid provider, premium endpoint, trial, paid infrastructure, or metered
  runtime model API.
- No duplicate live scheduled run may be started to obtain evidence.
- Missing, stale, contradictory, quota-blocked, or unverifiable evidence fails closed.

## Local implementation receipt

Tasks 1–11 implement the complete F1–F19 remediation plus the owner six-digit OTP contract. Task
closure heads and focused evidence are recorded in
`docs/reviews/2026-09-04-astra-audit-remediation-review.md`; detailed red/green and scoped-review
records are retained in the local SDD ledger.

The local branch contains ordered additive database migrations, gateway/dashboard/Telegram code,
provider and intelligence controls, hash-locked dependencies, release/recovery workflows, and
candidate-bound verifiers. None is claimed applied to a live project.

## Required rollout receipts

| Stage | Status | Required evidence |
|---|---|---|
| Consolidated local gate | Pending for final fix wave | Historical `a0f8158` gate passed 1,006 tests; it does not verify the final Track A/B/C changes |
| Independent review | Pending | GPT-6 Astra whole-branch verdict with no unresolved Critical or Important finding |
| Exact-head CI | Pending | Successful protected CI bound to the final reviewed SHA and current `main` ancestry |
| Migration deployment | Pending | Ordered native/private ledger match, per-statement hashes, exact candidate suffix, and no drift |
| Function and Site deployment | Pending | Candidate source/static hashes, deployment IDs, downloaded parity, and final success status |
| Auth configuration | Pending | Live OTP length 6, email body containing the Supabase token variable, and successful owner code login |
| Access canaries | Pending | Anonymous denial, non-owner denial, owner success, exact CORS/no-store, and five owner surfaces |
| Recovery | Pending | Durable pre-mutation artifact/journal plus protected isolated restore and full holdings/commands/reports/roles/schema/delivery reconciliation |
| Scheduled chain | Pending | Existing post-deployment scheduled slot with collection, source/quota, packet/hash, evaluation, report/hash, original Telegram ID or persisted suppression, and dashboard/database receipts |

Earlier production and CI records predate this remediation and cannot satisfy these rows.

## Protected rollout order

1. Obtain the independent whole-branch review and resolve any material finding.
2. Run exact-head CI and verify current-main plus merged-review ancestry.
3. Capture immutable rollback state and acquire the durable production release lease.
4. Apply every ordered migration with native and private statement-ledger reconciliation.
5. Deploy the reviewed gateway/API/Telegram/Site artifacts and prove downloaded source/static parity.
6. Configure/read back six-digit owner Auth and run owner/denial canaries.
7. Perform the protected isolated restore drill before treating recovery as proven.
8. Reconcile the next existing scheduled chain without creating a duplicate run.

Every non-success release must restore through the durable recovery workflow. GitHub deployment
success is the last release action, after artifacts, release record, verification, and recovery
readiness have succeeded.

## Current production truth

The remediation has not been pushed, merged, deployed, or applied. The current production system
continues to reflect the prior release documented before the Astra audit. This record did not read
the current production database, Auth template, runtime secrets, provider health, Telegram state,
backup contents, or scheduler receipts.

The local Task 10 disposable-runtime drill is useful implementation evidence but is not a live
restore receipt. The Task 2 Auth tests are repository evidence but are not live six-digit login
proof. Focused tests and the forthcoming consolidated gate are not deployment evidence.

## Safety receipt

Creating this record performed no live database access or mutation, migration, deployment, Auth
configuration, Telegram send, scheduled run, provider/model call, or brokerage action.

## Final fix wave Track C transport boundary

Focused local tests passed **315/315**. The audited `20260907` bytes are restored and the additive
tail is `20261001_immutable_history_closure.sql`. Source history/hash enforcement is unchanged.
The scoped PostgreSQL tests exercised both native-ledger upgrade/retry and a real isolated restore.
These results are local evidence; the final consolidated and independent-review gates are pending.

The production CLI and independent recovery now share the six-component encrypted journal engine.
Every prior snapshot contains presence, complete configuration, exact files, private values,
platform identity/version, and an authenticated snapshot hash. Every attempted mutation is durably
marked before transport. Recovery first reads each marked component and restores only proven
differences, in reverse order, then reads back again. An attempted write that failed before mutation
never causes restore/delete/unset. Supabase redeployment allocates a new version: exact prior
bytes/configuration are restored, while both original and restoration identities remain in evidence.
The final verifier independently reads protected immutable artifacts for all three Edge functions
and the owner Site, requires platform identities and byte parity, and checks predeployment capture.

The configured zero-cost static target remains `.openai/hosting.json`. The PostgreSQL/Supabase
adapter is now concrete, but no reviewed Sites CI transport or immutable four-artifact publication
integration exists. The workflow checks that before creating a production deployment, and the CLI
checks before acquiring the mutation lease. Finding 3 is locally implemented; finding 5 remains
open at that explicit Sites conditional. No alternate hosting,
paid service, fabricated deployment receipt, or automatic deployment is introduced.

The fixed integration contract is `scripts.configured_native_release_adapter.py:create_adapter(context)`:

- `capture`, `apply`, and `restore` cover the three named Edge functions, runtime role, and managed
  secrets. Capture must query authoritative management/database state, including complete runtime
  role attributes, password verifier, memberships/grant options, settings, source bytes/import
  configuration, and the exact existing managed values or proven absence. Existing secret values
  must match the platform digests through `capture_managed_secrets` before any rotation.
- `site` remains `None`; its future reviewed implementation must bind capture/apply/restore to the exact manifest project. Initial capture
  must prove existing owner-only access and retain the prior immutable version and complete bytes.
  Recovery validates the captured prior state and must not require the failed candidate Site to
  be healthy before attempting other components' restoration.
  It must also implement `plan(context)` and `release_receipt(candidate_sha, prior, readbacks, static)`:
  the latter publishes real immutable protected artifacts and returns the existing full writer/verifier
  contract. This is an in-process integration contract, not a claimed Sites API or configured transport.
- `plan(context)` is read/build-only. It returns all six candidate snapshots; newly created platform
  identities can be returned by `apply` and must match an independent subsequent capture.
- `retain(encrypted_bytes)` must durably acknowledge each authenticated encrypted journal version
  before returning. `recover_retained(original_run_id)` retrieves the unique latest retained token,
  bound to the exact candidate, project, and original release run. The `RELEASE_RECOVERY_KEY` remains
  a protected secret; plaintext snapshots and credentials must never be uploaded or logged.
- `receipt(candidate_sha)` and `artifact(artifact_id)` supply the protected release writer/verifier
  contract, including original prior-source artifact identities, full four-component management-plane
  readbacks, runtime configuration, migration receipts, and rollback readiness. A receipt label alone
  cannot substitute for bytes, exact identities, or owner/denial canaries.

The adapter uses pinned `supabase@2.116.0` list/download/deploy/delete/secrets commands. Existing
functions require exact active identity/version, entrypoint/import configuration, stable metadata
before/after download, and complete base64 byte captures. Missing metadata or source fails capture.
The protected environment must provide `DASHBOARD_PRIOR_MANAGED_SECRETS_JSON`, containing every
currently existing managed value. Platform digests must match. Values unsafe for literal env-file
round trips are rejected before mutation; credentials never appear in command arguments or receipts.
The administrator must be able to capture `pg_authid`, membership options/grantors, and per-database
role settings. PostgreSQL DDL is transactional and passwords are captured/restored as exact verifiers.

Authenticated encrypted journal versions are committed to the append-only
`public.stock_agent_component_recovery_journals` metadata table under the unresolved protected lease.
PUBLIC and Supabase client/service-role grants are revoked, RLS is enabled, and independent recovery
selects the latest ciphertext for the exact project/candidate/original run. Candidate secret values
are encrypted before attempted writes so fresh-process lost-response recovery can resolve actual
platform digests. Keep `RELEASE_RECOVERY_KEY` available throughout the protected retention period.

The missing Sites transport/publication remains an implementation/integration requirement, not a completed live
rollout. Production cannot proceed until it is reviewed and available. All production gates above
remain pending, including exact-head CI, live six-digit Auth, Site deployment/parity, live isolated
restore, and the next existing scheduled run without any duplicate trigger.
