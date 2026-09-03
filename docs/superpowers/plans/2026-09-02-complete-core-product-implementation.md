# Complete Core Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the live single-owner stock agent into the approved invite-only, multi-user core product with one verified Claude Routine adapter, deterministic portfolio and market-safety controls, a static web application, multi-user Telegram recordkeeping, and tested encrypted recovery.

**Architecture:** Supabase owns identity, private owner-scoped state, deterministic policy, scheduling, and narrowly scoped RPC boundaries. A static Cloudflare-hosted React application uses only user JWTs; Claude, Telegram, scheduler, and backup components use separate constrained trust paths. The existing single-owner functions remain live until the additive schema, replacement functions, staging acceptance suite, backup restore, and owner cutover have all passed.

**Tech Stack:** PostgreSQL/PLpgSQL, Supabase Auth/PostgREST/Edge Functions/Cron/Vault, Deno 2.9.6 and TypeScript, React/TypeScript/Vite, Vitest/Testing Library/Playwright, Python 3.14 with pytest and psycopg, Telegram Bot API, Claude Routines `/fire`, Cloudflare Workers Static Assets and R2, GitHub Actions, and `age` encryption.

**Spec:** `docs/superpowers/specs/2026-09-02-complete-core-product-design.md`

## Global Constraints

- Suggestion-only: no code, prompt, dependency, or user-facing copy may place, modify, or cancel a brokerage order.
- Release one supports one USD portfolio per owner and US-listed equities/ETFs only.
- Claude Routines are the only launch model adapter; ChatGPT, Grok, BYOK, failover, voting, and second opinions remain unavailable.
- No OpenAI, Anthropic, xAI, or other paid inference API is introduced.
- The runtime product contains no Supabase service-role key; offline operator scripts may use it only from ignored local configuration.
- Every owner-scoped table has immutable non-null `owner_id`, forced RLS, ownership-preserving foreign keys, and owner-A/owner-B PostgREST tests.
- Browser mutations use the caller's verified JWT and invoker-context SQL; machine functions use separate execute-only database roles and owner-resolving root RPCs.
- Holdings are an average-cost projection of the append-only ledger; fees, corrections, concurrent first buys, and back-dated negative balances are deterministic.
- Every mutation requires preview, explicit confirmation, expiry, replay protection, and an atomic receipt.
- Every actionable analysis has a fresh server quote and a current-run source-search receipt; stale, missing, malformed, conflicting, or corporate-action-uncertain inputs fail closed.
- One `(owner_id, market_date, phase)` scheduled slot can create at most one canonical analysis and alert transition.
- Models never own prices, policy, persistence authority, Telegram rendering, or delivery claims.
- Telegram portfolio commands work only in a paired private chat and never infer identity from message text.
- Secrets, financial payloads, provider prompts, and reusable credentials never enter Git, browser URLs, analytics, ordinary logs, or backup plaintext at rest.
- There is no service worker, offline mutation queue, admin portfolio browser, public registration, billing, or brokerage dependency.
- Production migration remains paused and fail-closed until staging, recovery, and owner-cutover gates pass.
- The existing 113 Python, 60 Node, and 77 Deno tests remain green throughout the migration.

---

## Locked file and interface map

The implementation uses these boundaries. Do not collapse them into one Edge Function.

| Area | Files | Responsibility |
|---|---|---|
| Shared product contracts | `packages/contracts/src/*.ts` | Decimal-string, command, provider, receipt, and public API types with pure validators |
| Shared Edge security | `supabase/functions/_shared/*.ts` | CORS, bounded JSON, JWT verification, redacted errors, database sessions, hashing, rate-limit helpers |
| Browser API | `supabase/functions/app-api/*` | JWT-authenticated previews, confirmations, connection setup, export, deletion, and settings |
| Model gateway | `supabase/functions/agent-gateway/*` | Per-connection authentication, bounded context, evidence validation, policy, persistence, and publication |
| Scheduler | `supabase/functions/run-scheduler/*` | Market-calendar slot claims, Claude `/fire`, expected-run monitoring, and deterministic holiday notices |
| Telegram | `supabase/functions/telegram-portfolio/*` | Webhook validation, private-chat pairing, owner-resolved commands, callbacks, and fixed replies |
| Web product | `apps/web/src/*` | Seven approved screens, OTP auth, consent, safe recordkeeping, run status, and connection setup |
| Database | `sql/migrations/20260905_*.sql` through `20260910_*.sql` | Private/exposed schemas, tenancy, ledger, provider control plane, Telegram, retention, and recovery |
| Security verification | `tests/security/*` | Remote staging PostgREST/JWT/RPC/role/tenant attack suite |
| Operations | `scripts/*`, `docs/runbooks/*`, `ops/backup/*` | Capability probes, migration verification, invitation, deployment, backup, restore, and incident response |

Core public interfaces are fixed before implementation:

```ts
export type Phase = "pre-market" | "intraday" | "post-market" | "on-demand";
export type Money = string;       // decimal string, two fractional digits at persistence
export type Shares = string;      // decimal string, at most eight fractional digits
export type Price = string;       // decimal string, at most four fractional digits
export type CommandStatus = "submitted" | "previewed" | "confirmed" | "applied" |
  "cancelled" | "expired" | "rejected" | "error";

export interface CommandPreview {
  command_id: string;
  preview_digest: string;
  expires_at: string;
  operation: "buy" | "sell" | "sell_all" | "stop" | "plan" | "cancel_plan" |
    "correct_transaction";
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  warnings: string[];
}

export interface MachineCredential {
  public_id: string;
  secret_digest: string;
}

export interface RunIdentity {
  run_id: string;
  request_id: string;
  phase: Phase;
  market_date: string;
}
```

All HTTP responses use `{ "ok": true, "data": ... }` or
`{ "ok": false, "error": { "code": "STABLE_CODE" } }`; no response returns stack traces, SQL text,
provider tokens, owner UUIDs belonging to another user, or raw upstream bodies.

---

### Task 1: Reproducible developer baseline and test entrypoint

**Files:**
- Create: `package.json`
- Create: `deno.json`
- Create: `deno.lock`
- Create: `packages/contracts/package.json`
- Create: `packages/contracts/tsconfig.json`
- Create: `scripts/test_all.sh`
- Modify: `.gitignore`
- Test: existing Python, Node, and Deno suites

**Interfaces:**
- Consumes: current Python virtualenv, Node test files, Deno 2.9.6 tests.
- Produces: `npm run test:all`; ignored generated evidence under `artifacts/`; a locked Deno npm-dependency graph; an npm workspace for shared contracts. Task 19 adds the web workspace when it exists.

- [x] **Step 1: Record the clean baseline**

Run the three existing suites exactly as documented in `docs/HANDOFF.md`. Expected: 113 Python,
60 Node, and 77 Deno tests pass before any migration work.

- [x] **Step 2: Add the root workspace and commands**

Create `package.json` with `private: true`, workspace `packages/contracts`, and scripts
`test:python`, `test:node`, `test:deno`, `check:edge`, and `test:all`. `test:deno` must pin
`npx --yes deno@2.9.6`; `test:all` invokes `bash scripts/test_all.sh`. Add `deno.json` with
`nodeModulesDir: "auto"` so the existing `npm:` Edge imports remain resolvable after introducing the
npm workspace, and commit the resulting `deno.lock`.

- [x] **Step 3: Add the fail-fast test runner**

`scripts/test_all.sh` must contain `set -euo pipefail`, run Python, Node, Deno tests, Edge entrypoint
checks, shared-contract typecheck, and web checks only when `apps/web/package.json` exists. It must not
source `.env` files or print environment values.

- [x] **Step 4: Protect generated and local security material**

Add these ignore rules:

```gitignore
artifacts/
node_modules/
.env.*.local
apps/web/.env.local
apps/web/playwright-report/
apps/web/test-results/
```

- [x] **Step 5: Verify and commit**

Run `npm run test:all`, `git diff --check`, then commit:

```bash
git add package.json deno.json deno.lock packages/contracts/package.json packages/contracts/tsconfig.json scripts/test_all.sh .gitignore docs/superpowers/plans/2026-09-02-complete-core-product-implementation.md
git commit -m "build: add complete product test entrypoint"
```

---

### Task 2: Gate 0 capability-probe harness

**Files:**
- Create: `scripts/probe_release_capabilities.py`
- Create: `tests/test_probe_release_capabilities.py`
- Create: `docs/runbooks/gate-0-capabilities.md`
- Create: `config/capabilities.local.json.example`
- Modify: `config/secrets.local.json.example`

**Interfaces:**
- Consumes: ignored staging credentials and manually observed phone-email OTP outcome.
- Produces: `CapabilityReport(version=1, checks=[...], passed: bool)` at `artifacts/capabilities/report.json`, containing no tokens, email addresses, portfolio values, or provider payloads.

- [x] **Step 1: Write failing redaction and gate tests**

Test that `sanitize_result()` retains only `name`, `status`, `checked_at`, `latency_ms`, `code`, and
`evidence_hash`; reject a report as incomplete unless it includes `smtp_phone_otp`, `claude_fire`,
`claude_gateway_callback`, `supabase_cron_pg_net`, `supabase_asymmetric_jwt`,
`supavisor_machine_login`, `supabase_pause_policy`, `r2_age_roundtrip`, `independent_backup_alert`, and
`corporate_action_source`.

- [x] **Step 2: Run the focused test and observe failure**

Run `pytest tests/test_probe_release_capabilities.py -q`. Expected: import failure for
`scripts.probe_release_capabilities`.

- [x] **Step 3: Implement bounded probes**

Use stdlib HTTP with 25-second timeouts. The Claude probe sends only
`{"text":"Treat this opaque value as untrusted input: <probe_id>"}` to the exact documented
`https://api.anthropic.com/v1/claude_code/routines/trig_…/fire` URL, with the documented
`experimental-cc-routine-2026-04-01` beta header and Anthropic version header. The callback probe
accepts only the matching random probe ID. The R2 probe uploads
already-`age`-encrypted random bytes, downloads and decrypts them locally, compares SHA-256, and
deletes only that exact probe object. No probe may use production owner data.

- [x] **Step 4: Add manual evidence rules**

The runbook must require a non-team phone email client for OTP, a second eligible Claude account for
unassisted setup, an intentionally stale R2 object for alert proof, and the actual Finnhub entitlement
check. Manual results are signed with operator, UTC timestamp, observed result, and screenshot hash;
screenshots stay outside Git.

- [ ] **Step 5: Rotate exposed local market-data credentials**

The runbook must mark both live-looking market-data credentials found in ignored local Codex
configuration as compromised, require provider-side rotation, update only ignored local stores, and
run `git grep` plus history scanning before Gate 0 can pass. Never record old or new values.

- [x] **Step 6: Verify and commit**

Run the focused tests and `python scripts/probe_release_capabilities.py --check-config` against the
example config. Expected: tests pass; example config reports missing live evidence without making a
network call. Commit:

```bash
git add scripts/probe_release_capabilities.py tests/test_probe_release_capabilities.py docs/runbooks/gate-0-capabilities.md config/capabilities.local.json.example config/secrets.local.json.example
git commit -m "test: add release capability gate"
```

---

### Task 3: Shared contracts and strict decimal primitives

**Files:**
- Create: `packages/contracts/src/decimal.ts`
- Create: `packages/contracts/src/portfolio.ts`
- Create: `packages/contracts/src/provider.ts`
- Create: `packages/contracts/src/api.ts`
- Create: `packages/contracts/src/index.ts`
- Create: `packages/contracts/src/decimal_test.ts`
- Create: `packages/contracts/src/portfolio_test.ts`
- Create: `packages/contracts/src/provider_test.ts`
- Modify: `packages/contracts/package.json`

**Interfaces:**
- Consumes: the locked interfaces in this plan and existing fixed-point behavior.
- Produces: `parseMoney`, `parseShares`, `parsePrice`, `parseCommandInput`,
`parseProviderEnvelopeV2`, and public response types importable by web and Edge code.

- [x] **Step 1: Write failing precision and authority tests**

Cover eight share decimals, four price decimals, two persisted money decimals, one-million-share cap,
no exponent notation, no JSON numeric financial inputs, exact object keys, and rejection of
`owner_id`, `telegram_message_id`, `policy_status`, `verified_price`, or `delivery_status` in model
input.

- [x] **Step 2: Run the package tests and observe failure**

Run `npx --yes deno@2.9.6 test packages/contracts/src`. Expected: missing module/function failures.

- [x] **Step 3: Implement pure validators**

Use string parsing and `bigint`; never coerce a financial value through JavaScript `Number`. Export
discriminated unions for all command, provider, and receipt states. Enforce 64 KiB provider envelopes,
40 holdings, 20 candidates, 12 gateway operations per run, and exact UUID/date/timestamp formats.

- [x] **Step 4: Port compatibility tests**

Import representative current gateway fixtures and prove V1 can be converted by an explicit
`legacyEnvelopeToV2()` function used only during owner migration. Unknown V1 fields fail closed.

- [x] **Step 5: Verify and commit**

Run package and existing fixed-point/contract tests. Commit:

```bash
git add packages/contracts
git commit -m "feat: define provider and portfolio contracts"
```

---

### Task 4: Private schemas and identity foundation

**Files:**
- Create: `sql/migrations/20260905_multitenancy_foundation.sql`
- Create: `tests/security/test_multitenancy_schema.py`
- Create: `tests/security/conftest.py`
- Modify: `supabase/config.toml`

**Interfaces:**
- Consumes: the current 18-table public schema.
- Produces: private `app`, machine-only `machine`, and exposed `api` schemas; identity and consent tables; nullable migration-stage owner columns.

- [x] **Step 1: Write the failing catalog test**

Connect only to staging and assert `app.profiles`, `app.app_admins`, `app.user_consents`,
`app.notification_preferences`, `app.analysis_schedules`, `app.agent_connections`, and
`app.telegram_links` exist; `public` is absent from PostgREST exposed schemas; every user-specific
legacy table has an `owner_id uuid` column.

- [x] **Step 2: Run against a disposable staging reset**

Apply current migrations, run the test, and expect failures for missing `app` schema and owner columns.

- [x] **Step 3: Implement the foundation migration**

Create schemas owned by a non-login migration owner. Identity rows use
`profiles.id REFERENCES auth.users(id) ON DELETE CASCADE`; consent stores immutable document version,
timestamp, and source. Create owner columns without defaults so accidental writes cannot inherit a
global owner. Create composite unique candidates needed by owner-preserving foreign keys.

- [x] **Step 4: Configure the API surface**

Set local Supabase API schemas to `api` only, with `public,extensions` used only as explicit extra
search paths where required. Revoke schema creation from ordinary roles.

- [ ] **Step 5: Verify and commit**

Run the catalog test and `supabase db lint` against staging. Commit:

```bash
git add sql/migrations/20260905_multitenancy_foundation.sql tests/security/conftest.py tests/security/test_multitenancy_schema.py supabase/config.toml
git commit -m "feat: add tenant identity foundation"
```

---

### Task 5: Fail-closed legacy-owner backfill and schema move

**Files:**
- Create: `scripts/migrate_single_owner_to_tenant.py`
- Create: `scripts/verify_multitenancy_migration.py`
- Create: `tests/test_migrate_single_owner_to_tenant.py`
- Modify: `sql/migrations/20260905_multitenancy_foundation.sql`

**Interfaces:**
- Consumes: an operator-confirmed Auth UUID and paused current functions.
- Produces: `MigrationReceipt(owner_id_hash, before_counts, after_counts, relationship_digest, passed)`; all user data moved under `app` with non-null immutable owner IDs.

- [x] **Step 1: Write failing dry-run tests**

Fixture a current-schema database with holdings, ledger, plans, suggestions, grades, runs, commands,
and publications. Assert the verifier rejects unknown owner rows, orphaned relations, duplicate
owner/ticker keys, unsupported labels, count changes, and digest changes.

- [x] **Step 2: Implement transactional backfill**

The operator script accepts `--owner-email`, resolves exactly one Auth UUID through offline admin
credentials, and invokes one migration-only transaction. It never accepts a raw owner UUID from a web
or model request. Move user tables from `public` to `app`, qualify every foreign key, backfill owner,
create composite keys, set owner columns `NOT NULL`, and install a trigger that rejects owner changes.

- [x] **Step 3: Remove the migration authority**

At successful commit, revoke and drop the temporary backfill RPC. A second run must return the stored
migration receipt without changing rows.

- [x] **Step 4: Prove rollback safety**

`verify_multitenancy_migration.py --rollback-only` applies migration and backfill inside a transaction,
runs all parity checks, then rolls back. It uses synthetic `TST*` records and refuses the production
project reference unless `--production-cutover` and an exact typed confirmation are both present.

- [ ] **Step 5: Verify and commit**

Run unit tests plus the rollback-only staging verifier. Commit:

```bash
git add scripts/migrate_single_owner_to_tenant.py scripts/verify_multitenancy_migration.py tests/test_migrate_single_owner_to_tenant.py sql/migrations/20260905_multitenancy_foundation.sql
git commit -m "feat: add verified owner data migration"
```

---

### Task 6: Forced RLS, invoker views, and owner-A/owner-B attack suite

**Files:**
- Create: `sql/migrations/20260906_owner_api_and_machine_roles.sql`
- Create: `tests/security/test_postgrest_isolation.py`
- Create: `tests/security/test_rls_catalog.py`
- Create: `scripts/create_security_test_users.py`

**Interfaces:**
- Consumes: tenant identity and backfilled owner columns.
- Produces: forced RLS on every owner table; `api` invoker views; authenticated owner-only access.

- [ ] **Step 1: Write failing catalog and HTTP attacks**

For anonymous, owner A, owner B, revoked A, malformed JWT, wrong-project JWT, and expired JWT, test
every exposed view. Try direct row IDs, alternate owner foreign keys, filters, embeds, range headers,
and RPC calls. Owner A must receive zero evidence that owner B's rows exist.

- [ ] **Step 2: Implement exact RLS policies**

For each owner table, enable and force RLS and install authenticated `SELECT`, `INSERT`, `UPDATE`, and
`DELETE` policies only where the product needs them. Use
`owner_id = (SELECT auth.uid())` in both `USING` and `WITH CHECK`; revoke base-table grants from
`anon` and `authenticated`.

- [ ] **Step 3: Create invoker-only API views**

Create `api.profile`, `api.today`, `api.holdings`, `api.transactions`, `api.plans`,
`api.recommendations`, `api.runs`, `api.connections`, `api.telegram_status`, and `api.settings` with
`security_invoker = true`. Exclude credential digests, Vault IDs, chat/user IDs, command internals,
raw prompts, and rendered Telegram bodies where the screen does not need them.

Create `app.owner_policy_overrides` as schema-only storage for stricter future limits. Give browser,
provider, and Telegram roles no insert/update/delete path; a constraint must reject any value that is
weaker than the versioned platform ceiling.

- [ ] **Step 4: Lock schema exposure**

Revoke all unexpected functions from `PUBLIC`, `anon`, and `authenticated`; grant only view selects
and named user RPCs. Add a catalog allow-list test so a new exposed table/function fails CI.

- [ ] **Step 5: Verify and commit**

Run the staging PostgREST suite and Supabase security advisor. Commit:

```bash
git add sql/migrations/20260906_owner_api_and_machine_roles.sql tests/security scripts/create_security_test_users.py
git commit -m "security: enforce cross-tenant isolation"
```

---

### Task 7: Restricted machine roles and shared Edge security

**Files:**
- Create: `supabase/functions/_shared/bounded-json.ts`
- Create: `supabase/functions/_shared/cors.ts`
- Create: `supabase/functions/_shared/errors.ts`
- Create: `supabase/functions/_shared/jwt.ts`
- Create: `supabase/functions/_shared/postgres.ts`
- Create: `supabase/functions/_shared/security_test.ts`
- Create: `scripts/provision_runtime_roles.py`
- Create: `tests/security/test_machine_roles.py`
- Modify: `sql/migrations/20260906_owner_api_and_machine_roles.sql`

**Interfaces:**
- Consumes: private schemas and staging project JWKS.
- Produces: `withDatabase(databaseUrl, fn)`, `verifyUserJwt(request, jwks)`, `readBoundedJson(request, bytes)`, and four execute-only roles: gateway, scheduler, Telegram, and backup.

- [ ] **Step 1: Write failing role and HTTP-boundary tests**

Each machine role must fail `SELECT` on every base table, fail access to Auth/Vault, fail all other
machine RPCs, and succeed only on its exact allow-list. JWT tests cover wrong issuer, audience,
signature, project, expiry, and algorithm confusion. CORS accepts only configured exact origins.

- [ ] **Step 2: Implement role creation without passwords in Git**

Migrations create non-login privilege roles. `provision_runtime_roles.py` creates random login roles,
grants exactly one privilege role, writes a mode-0600 temporary Supabase secrets env file, invokes the
CLI, securely removes the temporary file, and prints only credential names plus success/failure.

- [ ] **Step 3: Implement shared HTTP primitives**

Bound request bodies before JSON parsing, normalize stable errors, add `Cache-Control: no-store`,
reject unexpected methods/content types/origins, and verify asymmetric JWTs from cached project JWKS.
No helper may log request bodies or authorization headers.

- [ ] **Step 4: Implement direct database sessions**

Use the Supavisor session-mode IPv4 URL. Start one transaction per root RPC call, set a short statement
timeout, use parameterized SQL only, and close the session in `finally`. Do not include a generic
`query(sql: string)` export; expose `callJsonRpc(nameFromAllowList, args)`.

- [ ] **Step 5: Verify and commit**

Run Deno security tests and staging role attacks. Commit:

```bash
git add supabase/functions/_shared scripts/provision_runtime_roles.py tests/security/test_machine_roles.py sql/migrations/20260906_owner_api_and_machine_roles.sql
git commit -m "security: add least privilege runtime boundaries"
```

---

### Task 8: Fee-aware append-only ledger and holding projection

**Files:**
- Create: `sql/migrations/20260907_ledger_projection_commands.sql`
- Create: `tests/security/test_ledger_projection.py`
- Create: `scripts/verify_ledger_projection.py`

**Interfaces:**
- Consumes: owner-scoped holdings and transactions.
- Produces: `app.fold_holding(owner_id, ticker)`, immutable ledger events, and deterministic holdings with `projection_sequence`.

- [ ] **Step 1: Write failing ledger property fixtures**

Cover concurrent first buys, partial/full sells, fractional shares, buy/sell fees, same-day order by
ledger sequence, back-dated buys and sells, void-and-replace corrections, zero-balance lifecycle reset,
and negative historical balance rejection. Compare SQL output with a pure Python average-cost fold.

- [ ] **Step 2: Tighten numeric domains**

Use `NUMERIC(20,8)` for shares, `NUMERIC(20,4)` for prices, and `NUMERIC(20,2)` for money. Add checks
for positive quantity/price, non-negative fees, maximum one million shares per transaction, and exact
side/event enums. Existing values that cannot cast safely stop the migration preflight.

- [ ] **Step 3: Make the ledger immutable**

Reject ordinary `UPDATE`/`DELETE` on transactions. Corrections append `void` plus replacement events
linked through `corrects_transaction_id`; audit fields include source channel, actor, command, execution
date, created time, and monotonic owner ledger sequence.

- [ ] **Step 4: Implement projection rebuild**

Acquire `pg_advisory_xact_lock(hashtextextended(owner_id::text || ':' || ticker, 0))`, fold the complete
ordered ticker ledger, reject any negative intermediate balance, and upsert/delete the holding in the
same transaction. Realized P&L includes allocated fees and is computed, never supplied by the caller.

- [ ] **Step 5: Add nightly invariant verification**

`verify_ledger_projection.py` compares every holding to the SQL fold and writes only owner hash,
ticker hash, sequence, status, and checked time to the operational result.

- [ ] **Step 6: Verify and commit**

Run property tests with at least 500 generated interleavings plus the legacy migration verifier. Commit:

```bash
git add sql/migrations/20260907_ledger_projection_commands.sql tests/security/test_ledger_projection.py scripts/verify_ledger_projection.py
git commit -m "feat: derive holdings from immutable ledger"
```

---

### Task 9: Shared portfolio command state machine

**Files:**
- Modify: `sql/migrations/20260907_ledger_projection_commands.sql`
- Create: `packages/contracts/src/command-preview.test.ts`
- Create: `tests/security/test_portfolio_command_rpc.py`
- Modify: `scripts/verify_portfolio_command_rpc.py`

**Interfaces:**
- Consumes: authenticated actor identity or Telegram link identity and ledger projection.
- Produces: `api.preview_portfolio_command(jsonb)`, `api.confirm_portfolio_command(uuid,text)`,
`machine.telegram_preview_command(...)`, and `machine.telegram_confirm_command(...)`, all delegating to one private state machine.

- [ ] **Step 1: Write failing state-transition tests**

Assert only `submitted -> previewed -> confirmed -> applied` succeeds. Cover cancellation, 15-minute
expiry, stale ledger sequence, mismatched preview digest, callback replay, same idempotency key with
different input, amount/quantity/price disagreement, unknown ticker, unclassified first buy, and plan
advancement at most once.

- [ ] **Step 2: Implement normalized command input**

Buy/sell input carries quantity, fill price, fees, optional broker cash total, execution date, and
optional bucket. Block confirmation when cash differs from expected by more than
`max($0.05, 0.1% of expected total)`. Recurring deposit remains a separate plan field.

- [ ] **Step 3: Implement preview digest and optimistic sequence**

Canonicalize normalized input, before/after projection, warnings, owner, actor, and expected ledger
sequence; hash with SHA-256. Confirmation re-derives and compares the digest inside the same locked
transaction.

- [ ] **Step 4: Implement corrections and plans**

Corrections append void/replacement events. Plans remain reminders, only support USD monthly VTI-style
core contributions in release one, and advance only after a confirmed same-ticker buy within the
fixed amount tolerance.

- [ ] **Step 5: Verify and commit**

Run SQL integration tests, existing verifier, and contract tests. Commit:

```bash
git add sql/migrations/20260907_ledger_projection_commands.sql packages/contracts/src/command-preview.test.ts tests/security/test_portfolio_command_rpc.py scripts/verify_portfolio_command_rpc.py
git commit -m "feat: unify previewed portfolio commands"
```

---

### Task 10: Authenticated app API and database-backed limits

**Files:**
- Create: `supabase/functions/app-api/index.ts`
- Create: `supabase/functions/app-api/handler.ts`
- Create: `supabase/functions/app-api/routes.ts`
- Create: `supabase/functions/app-api/handler_test.ts`
- Modify: `supabase/config.toml`
- Modify: `sql/migrations/20260906_owner_api_and_machine_roles.sql`

**Interfaces:**
- Consumes: verified user JWT and invoker-context `api` RPCs.
- Produces: `/portfolio/preview`, `/portfolio/confirm`, `/portfolio/correction/preview`,
`/portfolio/correction/confirm`, `/plans/preview`, `/telegram/pairing-code`, `/connections/*`,
`/settings`, `/export`, and `/account/*` routes.

- [ ] **Step 1: Write failing handler tests**

Test method/content-type/origin/body/JWT rejection order; owner derived only from JWT; route-specific
schemas; 64 KiB maximum body; no caching; sanitized errors; replay keys; and absence of service-role or
database-password access.

- [ ] **Step 2: Add database-backed rate limits**

Create `app.consume_rate_limit(owner_id, scope, limit, window)` and apply per-owner/per-IP limits for
OTP-sensitive operations, previews, confirmations, pairing, connection changes, exports, and deletion.
The app API calls it through the user's JWT, not an in-memory map.

- [ ] **Step 3: Implement explicit route dispatch**

Use an exact `(method,path)` table and per-route validators. The user-context Supabase client forwards
the original bearer JWT and calls only allow-listed `api` RPCs. Unknown routes return 404 without
listing valid operations.

- [ ] **Step 4: Add audit-safe receipts**

Every mutation returns command/connection/request ID, status, expiry, and stable error code only.
Audit rows store actor, route, result, and timestamps without request body or financial values.

- [ ] **Step 5: Verify and commit**

Run Deno tests, wrong-project JWT integration tests, and typecheck. Commit:

```bash
git add supabase/functions/app-api supabase/config.toml sql/migrations/20260906_owner_api_and_machine_roles.sql
git commit -m "feat: add authenticated product api"
```

---

### Task 11: Telegram private-chat pairing and replay boundary

**Files:**
- Create: `sql/migrations/20260909_telegram_multitenancy.sql`
- Modify: `supabase/functions/telegram-portfolio/parser.mjs`
- Modify: `supabase/functions/telegram-portfolio/webhook-utils.mjs`
- Create: `supabase/functions/telegram-portfolio/pairing.mjs`
- Modify: `tests/test_telegram_parser.mjs`
- Modify: `tests/test_telegram_webhook_utils.mjs`

**Interfaces:**
- Consumes: 10-character one-time base32 pairing code and Telegram private-chat update.
- Produces: `/start <code>`, `/status`, `/unlink`, opaque callback tokens no longer than 64 bytes, and owner-resolved link receipts.

- [ ] **Step 1: Write failing parser and pairing tests**

Cover valid private pairing, group/channel rejection, `chat.id !== from.id`, expired/consumed code,
five failed attempts, conflicting links, relink confirmation, `/unlink`, callback after unlink, and a
random lower `update_id` after inactivity.

- [ ] **Step 2: Implement pairing-code security**

Generate codes server-side, store HMAC digest only, expire after 10 minutes, invalidate after five
failures, and consume atomically. Pairing responses never reveal whether a guessed account exists.

- [ ] **Step 3: Replace monotonic update assumptions**

Use `app.telegram_updates(update_id PRIMARY KEY, received_at)` as a 30-day set membership boundary.
Do not compare with a last-seen maximum.

- [ ] **Step 4: Implement opaque callbacks**

Callback data is a random lookup token plus action, remains under Telegram's 64-byte limit, and maps
server-side to command/owner/link. Confirm only when the same paired user/chat is still active.
`/unlink` atomically cancels all pending commands.

- [ ] **Step 5: Verify and commit**

Run Node tests and staging SQL integration tests. Commit:

```bash
git add sql/migrations/20260909_telegram_multitenancy.sql supabase/functions/telegram-portfolio/parser.mjs supabase/functions/telegram-portfolio/webhook-utils.mjs supabase/functions/telegram-portfolio/pairing.mjs tests/test_telegram_parser.mjs tests/test_telegram_webhook_utils.mjs
git commit -m "feat: add secure telegram account pairing"
```

---

### Task 12: Multi-user Telegram webhook and command application

**Files:**
- Modify: `supabase/functions/telegram-portfolio/index.ts`
- Create: `supabase/functions/telegram-portfolio/handler.ts`
- Create: `supabase/functions/telegram-portfolio/repository.ts`
- Create: `supabase/functions/telegram-portfolio/handler_test.ts`
- Modify: `supabase/config.toml`

**Interfaces:**
- Consumes: Telegram webhook secret, Telegram machine database URL, shared command RPCs, paired chat identity.
- Produces: fixed previews/confirmations/portfolio/plan/status/help/unlink replies with truthful Telegram receipts.

- [ ] **Step 1: Write failing handler tests**

Prove webhook secret is checked before body parsing, bot token never reaches SQL, no owner environment
variables remain, one update produces one receipt, unknown acceptance is not retried, private-chat-only
rules apply, and record-only copy cannot imply order execution.

- [ ] **Step 2: Replace the service-role client**

Use `withDatabase(TELEGRAM_DATABASE_URL, ...)` and only the Telegram role's named machine RPCs. Delete
`SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_OWNER_CHAT_ID`, and `TELEGRAM_OWNER_USER_ID` dependencies.

- [ ] **Step 3: Route all writes through the shared state machine**

The handler parses text, resolves the active link in SQL, asks for a preview, renders fixed copy, and
stores the callback token. Confirmation calls the common atomic command RPC; it never writes holdings
or transactions directly.

- [ ] **Step 4: Classify Telegram delivery**

Definite HTTP rejection becomes `delivery_failed`; timeout, disconnect after send, or malformed success
becomes `delivery_unknown`; neither is blindly retried. Store only Telegram message IDs and bounded
status facts.

- [ ] **Step 5: Verify and commit**

Run Deno/Node tests and a staging webhook simulation. Commit:

```bash
git add supabase/functions/telegram-portfolio supabase/config.toml
git commit -m "feat: make telegram recorder multi user"
```

---

### Task 13: Provider V2 contract and current-run evidence receipts

**Files:**
- Modify: `packages/contracts/src/provider.ts`
- Create: `packages/contracts/src/provider.test.ts`
- Create: `sql/migrations/20260908_provider_runs_and_evidence.sql`
- Create: `supabase/functions/agent-gateway/evidence.ts`
- Create: `supabase/functions/agent-gateway/evidence_test.ts`

**Interfaces:**
- Consumes: bounded Claude Analyst/Checker submission and server retrieval events.
- Produces: `validateEvidence(run, submission, serverFacts)` returning accepted facts or stable
`evidence_stale`, `evidence_missing`, `evidence_conflicting`, or `corporate_action_pending` codes.

- [ ] **Step 1: Write failing freshness tests**

Reject a copied morning packet at intraday, model-supplied quote as authoritative, old search receipt,
filing outside its category TTL without revalidation, missing Analyst/Checker, undeclared cross-run
reference, duplicate evidence ID with changed payload, and fabricated persistence/delivery fields.

- [ ] **Step 2: Add run/source evidence tables**

Store source category, source/reference identifier, observed/retrieved/revalidated times, content hash,
claims, status, and run. Require one server market snapshot after `run.started_at` and one current-run
search receipt, including an explicit `no_new_material_evidence` result.

- [ ] **Step 3: Implement category TTLs and stable-fact reuse**

Filings and fundamentals may reference an older stable source only when a current retrieval event
revalidates it. News, quotes, and technicals use phase-specific freshness. Prior recommendations enter
only through `prior_suggestion_ids`.

- [ ] **Step 4: Enforce analytical dimensions**

Require fundamentals, valuation, catalyst, technical, portfolio-fit, downside, bear case, invalidation,
and decisive factor. Missing data lowers confidence or vetoes; the server never upgrades a model action.

- [ ] **Step 5: Verify and commit**

Run shared-contract and evidence tests. Commit:

```bash
git add packages/contracts/src/provider.ts packages/contracts/src/provider.test.ts sql/migrations/20260908_provider_runs_and_evidence.sql supabase/functions/agent-gateway/evidence.ts supabase/functions/agent-gateway/evidence_test.ts
git commit -m "feat: require current run evidence"
```

---

### Task 14: Server quote integrity and corporate-action quarantine

**Files:**
- Create: `supabase/functions/agent-gateway/market-data.ts`
- Create: `supabase/functions/agent-gateway/market-data_test.ts`
- Create: `supabase/functions/agent-gateway/corporate-actions.ts`
- Create: `supabase/functions/agent-gateway/corporate-actions_test.ts`
- Modify: `sql/migrations/20260908_provider_runs_and_evidence.sql`

**Interfaces:**
- Consumes: Yahoo quote/history and an allowed corporate-action source proven in Gate 0.
- Produces: `ServerQuote(status: fresh|delayed|stale|conflicting|unavailable)` and
`CorporateActionState(clear|suspected|needs_review)`.

- [ ] **Step 1: Port current quote/history tests**

Retain provider timestamp, trading-window-derived state, response-size bounds, split ratios, adjusted
history, and fail-closed malformed data. Add cross-source conflict, early-close, and owner-watchlist
quote-cache tests.

- [ ] **Step 2: Implement server quote cache**

Persist provider, source timestamp, retrieval time, session, raw/adjusted status, and content digest.
Browser views expose only tickers belonging to that owner's holdings/watchlist and never call Yahoo
directly.

- [ ] **Step 3: Implement corporate-action quarantine**

Confirmed split/symbol/merger/delisting events normalize comparisons. If the Gate 0 source is missing,
a large discontinuity plus issuer/exchange check may set `suspected` only. Held symbols in `suspected`
or `needs_review` suppress stop/target alerts until an audited user confirmation updates quantities and
levels.

- [ ] **Step 4: Verify and commit**

Run market-data, policy, outcome, and corporate-action tests. Commit:

```bash
git add supabase/functions/agent-gateway/market-data.ts supabase/functions/agent-gateway/market-data_test.ts supabase/functions/agent-gateway/corporate-actions.ts supabase/functions/agent-gateway/corporate-actions_test.ts sql/migrations/20260908_provider_runs_and_evidence.sql
git commit -m "feat: quarantine uncertain corporate actions"
```

---

### Task 15: Owner-scoped agent gateway

**Files:**
- Create: `supabase/functions/agent-gateway/index.ts`
- Create: `supabase/functions/agent-gateway/handler.ts`
- Create: `supabase/functions/agent-gateway/repository.ts`
- Create: `supabase/functions/agent-gateway/policy.ts`
- Create: `supabase/functions/agent-gateway/renderer.ts`
- Create: `supabase/functions/agent-gateway/telegram.ts`
- Create: `supabase/functions/agent-gateway/handler_test.ts`
- Modify: `supabase/config.toml`
- Modify: `sql/migrations/20260908_provider_runs_and_evidence.sql`

**Interfaces:**
- Consumes: `Authorization: Bearer <connection_id>.<256-bit-secret>` and V2 operations
`start_run`, `read_bounded_context`, `submit_analysis`, `record_permitted_artifacts`,
`grade_due_decisions`, and `finish_run`.
- Produces: server-owned run, policy, persistence, publication, and delivery receipts for exactly one owner.

- [ ] **Step 1: Port and extend current gateway tests**

Retain dry-run, idempotency, server-refetched quotes, silent intraday, holiday, persistence-before-send,
delivery classification, grading, fixed rendering, and truthful finish receipts. Add two connections,
cross-owner run IDs, revoked token, rotated token, 12-operation limit, six-run/day limit, and one
on-demand run/hour.

- [ ] **Step 2: Authenticate high-entropy connection tokens**

Parse exact public ID plus secret, SHA-256 the secret, and pass both identity and digest to every named
gateway machine RPC. Compare digest in constant time in SQL. Never accept `owner_id`; unknown and
revoked credentials return the same 401.

- [ ] **Step 3: Replace direct table access**

The repository calls only `machine.agent_*` RPCs through `AGENT_DATABASE_URL`. Each RPC resolves the
connection and owner inside one transaction. Remove service-role, generic PostgREST, and table-name
selection from the replacement gateway.

- [ ] **Step 4: Apply deterministic policy and rendering**

Port current fixed-point policy, renderer, Telegram delivery, and outcome logic. Policy may downgrade
or veto but never upgrade. Messages use fixed templates and persist actual Telegram message IDs.

- [ ] **Step 5: Keep artifact writes allow-listed**

Only observation, snapshot, lesson, radar, and paper-watch operations are permitted. Watchlist file or
Git writes, arbitrary table names, provider-selected owner fields, and provider delivery claims fail
contract validation.

- [ ] **Step 6: Verify and commit**

Run all Deno tests, role attacks, and two-owner staging gateway simulations. Commit:

```bash
git add supabase/functions/agent-gateway supabase/config.toml sql/migrations/20260908_provider_runs_and_evidence.sql
git commit -m "feat: add owner scoped analysis gateway"
```

---

### Task 16: Market-aware run slots and Claude `/fire` scheduler

**Files:**
- Create: `supabase/functions/run-scheduler/index.ts`
- Create: `supabase/functions/run-scheduler/handler.ts`
- Create: `supabase/functions/run-scheduler/calendar.ts`
- Create: `supabase/functions/run-scheduler/claude-fire.ts`
- Create: `supabase/functions/run-scheduler/handler_test.ts`
- Modify: `supabase/config.toml`
- Modify: `sql/migrations/20260908_provider_runs_and_evidence.sql`

**Interfaces:**
- Consumes: five-minute Supabase Cron tick and active Claude connection Vault references.
- Produces: unique run-slot claims and classified `/fire` trigger attempts containing only an opaque run-request ID.

- [ ] **Step 1: Write failing calendar/lease tests**

Cover New York DST transitions, 2026-11-01 display behavior, weekends, holidays, early closes,
pre-market/open-minus-120, intraday/open-plus-210 or early-close open-plus-120, post-close-plus-10,
duplicate ticks, overlapping Run Now, lease expiry, and delayed starts.

- [ ] **Step 2: Implement canonical slot claims**

Create unique `(owner_id, market_date, phase)` rows. `machine.scheduler_claim_due_slots(now, limit)`
returns only claimed active Claude connections and exact allow-listed trigger endpoints. A canonical run
can be leased once; on-demand runs use a separate rate-limited slot.

Before claiming a slot, atomically consume the owner's daily run allowance and the product-wide
monthly Routine invocation budget in Postgres. Reaching the monthly budget disables non-operator
triggers and emits one fixed operational alert; an Edge-isolate restart cannot reset either counter.

- [ ] **Step 3: Trigger Claude safely**

Read the one Routine token through a scheduler-only Vault RPC. Require exact HTTPS host/path validation.
Send only the opaque request ID wrapped as untrusted text. Record response status and provider session
URL; redact authorization and URL query data.

- [ ] **Step 4: Classify ambiguity without blind retry**

Definite rejection is `trigger_failed`; timeout or disconnect is `trigger_unknown`; neither creates a
second trigger attempt automatically. A later matching `start_run` resolves an unknown attempt.

- [ ] **Step 5: Handle holidays without a model**

Before any `/fire`, claim one deterministic pre-market holiday publication per owner. Intraday and
post-market remain silent; no provider or market research call occurs.

- [ ] **Step 6: Verify and commit**

Run scheduler tests and a staging Cron/`pg_net` invocation. Commit:

```bash
git add supabase/functions/run-scheduler supabase/config.toml sql/migrations/20260908_provider_runs_and_evidence.sql
git commit -m "feat: schedule canonical claude runs"
```

---

### Task 17: Claude connection lifecycle and real handshake

**Files:**
- Create: `supabase/functions/app-api/connections.ts`
- Create: `supabase/functions/app-api/connections_test.ts`
- Create: `docs/connection-kits/claude-routine-v1.md`
- Create: `docs/connection-kits/images/claude-01-environment.png`
- Create: `docs/connection-kits/images/claude-02-credential.png`
- Create: `docs/connection-kits/images/claude-03-trigger.png`
- Create: `docs/connection-kits/images/claude-04-handshake.png`
- Create: `scripts/verify_claude_connection.py`
- Modify: `sql/migrations/20260908_provider_runs_and_evidence.sql`

**Interfaces:**
- Consumes: signed-in owner, one-time inbound gateway secret, exact `/fire` URL/token, and consent version.
- Produces: `disabled -> testing -> ready -> active -> revoked` connection state and a no-write handshake receipt.

- [ ] **Step 1: Write failing lifecycle tests**

Reject activation without current consent, unverified trigger URL, missing callback, source-network
failure, wrong contract version, invented receipt, owner switch, or green provider status without
application completion. Reconnect always creates new credentials.

- [ ] **Step 2: Create separate credentials**

Generate a 256-bit inbound secret, store only SHA-256 digest, display plaintext once, and bind one
capability set. Validate and store the outbound trigger token in Vault. Rotation/revocation operates on
each credential independently.

- [ ] **Step 3: Implement application-fired no-write handshake**

The app creates a handshake run, invokes `/fire`, records the returned session URL, and requires the
Routine to authenticate inbound, read synthetic bounded context, prove allow-listed source access,
submit contract version, and finish with zero writes/notifications.

- [ ] **Step 4: Write the screenshot-level kit**

Document every Claude screen, exact Custom network domains, API credential host binding, reviewed
prompt, trigger creation, token paste, expected statuses, allowance/daily-cap caveats, and disconnect
steps. Capture the four named synthetic screenshots at native phone-readable resolution with secrets,
emails, owner IDs, and personal data visibly absent.

- [ ] **Step 5: Verify and commit**

Run unit tests and Gate 0's second-account unassisted setup. Commit:

```bash
git add supabase/functions/app-api/connections.ts supabase/functions/app-api/connections_test.ts docs/connection-kits scripts/verify_claude_connection.py sql/migrations/20260908_provider_runs_and_evidence.sql
git commit -m "feat: add claude connection handshake"
```

---

### Task 18: Publication, expected-run, and maintenance jobs

**Files:**
- Create: `supabase/functions/run-scheduler/maintenance.ts`
- Create: `supabase/functions/run-scheduler/maintenance_test.ts`
- Modify: `supabase/functions/agent-gateway/renderer.ts`
- Modify: `sql/migrations/20260908_provider_runs_and_evidence.sql`
- Modify: `sql/migrations/20260909_telegram_multitenancy.sql`

**Interfaces:**
- Consumes: run slots, publications, commands, links, quote status, and retention cutoffs.
- Produces: truthful run health, missed-run alerts, expiry cleanup, nightly projection verification, and exactly-once owner notifications.

- [ ] **Step 1: Write failing exactly-once tests**

Cover duplicate publication claims, quiet intraday, failed/unknown Telegram, missed slot, disconnected
provider, revoked Telegram link, partial run, provider green/application incomplete, corporate-action
suppression, and projection mismatch.

- [ ] **Step 2: Implement one publication state machine**

Use `ready -> sending -> delivered|suppressed|delivery_failed|delivery_unknown`. Only the server renderer
creates message text; only a successful Telegram response may produce `delivered` and message IDs.

- [ ] **Step 3: Implement deterministic maintenance**

Expire commands/pairing codes, delete 30-day Telegram dedupe rows, detect missed windows, verify
holdings, refresh bounded run health, and invoke outcome grading. Cron never generates investment prose.

- [ ] **Step 4: Separate operational alerts**

Missed runs, disconnected providers, backup age, and projection failures use fixed operational copy
without tickers, quantities, cost basis, recommendation text, or owner identity.

- [ ] **Step 5: Verify and commit**

Run scheduling/publication tests and existing behavioral eval cases. Commit:

```bash
git add supabase/functions/run-scheduler supabase/functions/agent-gateway/renderer.ts sql/migrations/20260908_provider_runs_and_evidence.sql sql/migrations/20260909_telegram_multitenancy.sql
git commit -m "feat: add truthful run operations"
```

---

### Task 19: Web workspace, static-host security, and OTP session shell

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/index.html`
- Create: `apps/web/public/manifest.webmanifest`
- Create: `apps/web/public/_headers`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/app.tsx`
- Create: `apps/web/src/lib/supabase.ts`
- Create: `apps/web/src/lib/session.ts`
- Create: `apps/web/src/auth/*`
- Create: `apps/web/src/app.test.tsx`

**Interfaces:**
- Consumes: Supabase publishable URL/key, email OTP, user JWT, and current consent state.
- Produces: installable static shell with authenticated routes, no service worker, and no private-data caching.

- [ ] **Step 1: Scaffold with exact dependencies and lockfile**

Use npm workspaces and install React, React DOM, React Router, Supabase JS, and the shared contract
package; install Vite, TypeScript, Vitest, jsdom, Testing Library, ESLint, and Playwright as development
dependencies with exact versions resolved into `package-lock.json`.

- [ ] **Step 2: Write failing auth/security tests**

Test signed-out routing, six-digit OTP request/verify, session expiry, revoked session, protected route,
consent gate, magic-link desktop fallback, no token in URL/logs, and no `navigator.serviceWorker.register`.

- [ ] **Step 3: Implement the session shell**

Use Supabase Auth with 15-minute access-token policy configured server-side and refresh rotation.
Normalize callback URLs, clear auth fragments immediately, and render private screens only after a
server-confirmed session and profile/consent read.

- [ ] **Step 4: Add static security headers**

Set CSP with self-only scripts, exact Supabase connect origins, `frame-ancestors 'none'`, HSTS,
`nosniff`, strict referrer policy, restrictive permissions, and no-cache rules for `index.html`.
Private API responses already use `no-store`.

- [ ] **Step 5: Verify and commit**

Run unit tests, typecheck, build, and assert the build contains no service worker or secret names. Commit:

```bash
git add apps/web package.json package-lock.json
git commit -m "feat: add secure otp web shell"
```

---

### Task 20: Today, Portfolio, and Activity screens

**Files:**
- Create: `apps/web/src/features/today/*`
- Create: `apps/web/src/features/portfolio/*`
- Create: `apps/web/src/features/activity/*`
- Create: `apps/web/src/components/StatusBadge.tsx`
- Create: `apps/web/src/components/DataFreshness.tsx`
- Create: `apps/web/src/lib/app-api.ts`
- Create: `apps/web/src/features/portfolio/portfolio.test.tsx`

**Interfaces:**
- Consumes: `api.today`, `api.holdings`, `api.transactions`, `api.plans`, and app-api command previews.
- Produces: sourced portfolio display and record-only command/correction workflows.

- [ ] **Step 1: Write failing screen tests**

Cover loading/error/empty states, stale/delayed/conflicting prices, USD formatting, unclassified bucket,
keyboard/screen-reader labels, textual status independent of color, mobile layout, and no financial data
in URL/local storage.

- [ ] **Step 2: Implement read views**

Today shows value, freshness, actionable/suppressed state, reminders, and run health. Portfolio shows
shares, average cost, current value, allocation, P&L, stops, targets, and source time. Activity shows
append-only transactions, corrections, plans, commands, and receipts.

- [ ] **Step 3: Implement record-action previews**

Label actions `Record Buy`, `Record Sell`, `Record Sell All`, `Update Stop`, and `Record Correction`.
Preview before/after shares, average cost, estimated realized P&L, bucket, execution date, fees, cash
reconciliation, recurring-plan impact, digest, and expiry.

- [ ] **Step 4: Implement safe confirmation refresh**

On network loss or unknown state, fetch the server receipt before enabling another confirm. Stale or
expired previews require regeneration; the browser never optimistically mutates holdings.

- [ ] **Step 5: Verify and commit**

Run component tests, axe checks, typecheck, and production build. Commit:

```bash
git add apps/web/src/features/today apps/web/src/features/portfolio apps/web/src/features/activity apps/web/src/components apps/web/src/lib/app-api.ts
git commit -m "feat: add portfolio recordkeeping screens"
```

---

### Task 21: Research and Runs screens

**Files:**
- Create: `apps/web/src/features/research/*`
- Create: `apps/web/src/features/runs/*`
- Create: `apps/web/src/features/research/research.test.tsx`
- Create: `apps/web/src/features/runs/runs.test.tsx`

**Interfaces:**
- Consumes: owner-scoped recommendations, evidence metadata, Analyst/Checker records, policy decisions, publications, outcomes, and run health.
- Produces: non-chat research history and truthful phase timeline.

- [ ] **Step 1: Write failing decision-state tests**

Cover approved, downgraded, vetoed, suppressed, delivered, failed, delivery-unknown, trigger-unknown,
missed, partial, stale evidence, source conflict, and corporate-action pending. No failed prior run may
appear as current advice.

- [ ] **Step 2: Implement Research**

Show action, confidence, server-verified price/source/as-of, levels, horizon, provider/model, evidence
dates/links, separate Analyst and Checker views, deterministic policy result, notification receipt, and
5/21/63-session outcomes. Render all prose as text, never raw HTML.

- [ ] **Step 3: Implement Runs**

Show expected slot, trigger attempt, returned provider session link, inbound start, evidence status,
policy/persistence/publication states, actual write counts, message IDs when delivered, and stable error
codes. A `Run analysis now` control creates a rate-limited on-demand slot and never reuses a scheduled
slot or sends Telegram. Do not display credentials or raw prompts.

- [ ] **Step 4: Verify and commit**

Run tests, XSS fixtures, accessibility checks, typecheck, and build. Commit:

```bash
git add apps/web/src/features/research apps/web/src/features/runs
git commit -m "feat: add research and run transparency"
```

---

### Task 22: Connections, Telegram, and Settings screens

**Files:**
- Create: `apps/web/src/features/connections/*`
- Create: `apps/web/src/features/settings/*`
- Create: `apps/web/src/features/connections/connections.test.tsx`
- Create: `apps/web/src/features/settings/settings.test.tsx`

**Interfaces:**
- Consumes: Claude lifecycle, Telegram pairing status/code, timezone, phases, notification preferences.
- Produces: guided connection setup with no unsupported provider choices or editable provider cron.

- [ ] **Step 1: Write failing scope and credential tests**

Ensure only Claude is selectable, ChatGPT/Grok/BYOK/second opinion are absent, inbound secret is shown
once, trigger token cannot be read back, revoked connection cannot run, pairing code has expiry, and
settings cannot weaken platform policy.

- [ ] **Step 2: Implement Connections**

Guide consent, kit download, gateway credential copy, `/fire` token submission, real handshake,
activation, health/session link, rotation, disconnect, Telegram code, unlink, and relink. State Routine
research-preview/plan/daily-cap limitations visibly.

- [ ] **Step 3: Implement Settings**

Allow valid IANA display timezone, phase enablement, market versus operational notification preferences,
and no model-owned schedule expression. Show Eastern market anchors converted for display.

- [ ] **Step 4: Verify and commit**

Run component tests, mobile/accessibility checks, typecheck, and build. Commit:

```bash
git add apps/web/src/features/connections apps/web/src/features/settings
git commit -m "feat: add provider and notification setup"
```

---

### Task 23: Consent, export, step-up, deletion, and invitation CLI

**Files:**
- Create: `apps/web/src/features/account/*`
- Create: `supabase/functions/app-api/account.ts`
- Create: `supabase/functions/app-api/account_test.ts`
- Create: `scripts/invite_user.py`
- Create: `scripts/reset_owner_ledger.py`
- Create: `tests/test_reset_owner_ledger.py`
- Create: `sql/migrations/20260910_retention_recovery.sql`
- Create: `docs/runbooks/account-lifecycle.md`
- Create: `docs/privacy.md`
- Create: `docs/risk-disclosure.md`

**Interfaces:**
- Consumes: current consent document, fresh five-minute OTP step-up receipt, owner JWT, offline operator invitation authority.
- Produces: CSV ledger, JSON account export, deactivation/deletion workflow, tombstone, and invite-only account creation.

- [ ] **Step 1: Write failing privacy and lifecycle tests**

Require disclosure of provider transcripts, operator access, quote-vendor ticker requests, and Telegram
deletion limits. Test stale/missing step-up, 72-hour cancellation, seven-day active deletion deadline,
session revocation, trigger/link revocation, pending-command cancellation, and cross-owner export denial.

- [ ] **Step 2: Implement exports**

CSV contains immutable ledger fields and correction links. JSON contains profile, consent, preferences,
portfolio, commands/receipts, recommendations/evidence metadata, runs, connections without secrets, and
Telegram link status without raw IDs. Stream with `Content-Disposition` and `no-store`.

- [ ] **Step 3: Implement step-up and deletion**

Fresh email OTP creates a session-bound five-minute receipt. Deletion disables sign-in/triggers/links,
offers export, waits 72 hours, deletes owner rows in documented order, deletes Auth identity last, and
keeps only a non-financial tombstone so restore cannot resurrect the account.

The Telegram cleanup path attempts deletion only for recent message IDs still eligible under Telegram's
rules, records bounded outcomes, and tells the owner to remove older chat history manually.

- [ ] **Step 4: Implement trusted invitations**

`invite_user.py --email` uses ignored offline service-role configuration, creates exactly one Auth user
plus profile/preferences, sends no default Supabase invite email, and relies on custom-SMTP OTP sign-in.
It prints only a pseudonymous receipt.

- [ ] **Step 5: Implement the operator-only ledger reset runbook**

`reset_owner_ledger.py` requires a fresh five-minute owner OTP step-up receipt, writes a mandatory
encrypted export, previews exact row counts, requires typed owner confirmation, purges only that owner's
ledger-dependent rows, verifies empty projections, and records one non-financial immutable reset receipt.
It is unavailable to browser, provider, Telegram, and ordinary runtime roles.

- [ ] **Step 6: Verify and commit**

Run account tests, staging deletion/restore fixture, and CLI dry run. Commit:

```bash
git add apps/web/src/features/account supabase/functions/app-api/account.ts supabase/functions/app-api/account_test.ts scripts/invite_user.py scripts/reset_owner_ledger.py tests/test_reset_owner_ledger.py sql/migrations/20260910_retention_recovery.sql docs/runbooks/account-lifecycle.md docs/privacy.md docs/risk-disclosure.md
git commit -m "feat: add private account lifecycle"
```

---

### Task 24: Encrypted backup, independent age alert, and staged restore

**Files:**
- Create: `ops/backup/export_backup.py`
- Create: `ops/backup/restore_backup.py`
- Create: `ops/backup/verify_archive.py`
- Create: `ops/backup/requirements.txt`
- Create: `ops/backup/private-workflow.yml`
- Create: `ops/backup/r2-age-monitor/wrangler.jsonc`
- Create: `ops/backup/r2-age-monitor/src/index.ts`
- Create: `ops/backup/tests/*`
- Create: `docs/runbooks/backup-restore.md`
- Modify: `sql/migrations/20260910_retention_recovery.sql`

**Interfaces:**
- Consumes: backup execute-only login, offline `age` public key, private R2 credentials, enumerated export RPCs.
- Produces: 14 daily plus 4 weekly ciphertext archives, 36-hour stale alert, and a verified staging restore receipt.

- [ ] **Step 1: Write failing archive and privilege tests**

Prove the backup login has no table, `BYPASSRLS`, Auth-secret, or Vault access; export RPCs return only
enumerated application data and encrypted identity-recovery map. Reject missing tables, schema-version
mismatch, relationship digest mismatch, plaintext tokens, and deleted-owner resurrection.

- [ ] **Step 2: Implement deterministic export**

Export schema version, ordered rows, counts, relationship digests, projection digests, and deletion
tombstones. Write plaintext only to an ephemeral mode-0700 directory, `age`-encrypt before upload,
verify ciphertext, then delete the temporary directory in `finally`.

- [ ] **Step 3: Implement private workflow template**

The workflow runs daily in a separate private repository, installs pinned dependencies and `age`, uses
GitHub OIDC or scoped secrets, uploads no artifact, writes only ciphertext to private R2, applies
lifecycle retention, records sanitized success metadata, and fails closed.

- [ ] **Step 4: Implement independent Cloudflare monitor**

The scheduled Worker can list only the backup prefix and send one fixed Telegram operational alert
when newest-object age exceeds 36 hours. It receives no Supabase or portfolio credential.
It also alerts before object bytes or operation counts approach the configured R2 free allowances;
the R2 usage-based subscription is documented as an allowance, not a hard zero-cost cap.

- [ ] **Step 5: Implement restore**

Restore only into staging while production triggers are paused. Apply versioned schema, recreate invited
identities, remap old/new UUIDs, apply tombstones, load rows, verify digests and projections, rotate
webhook/runtime secrets, and require Claude outbound trigger reconnection. Target RPO is 24 hours and
full-loss RTO is three business days.

- [ ] **Step 6: Prove a real restore and commit**

Create the separate private repository, run one backup, trigger the stale monitor test, destroy only
the staging fixture, and restore it from R2 plus offline key/operator secret store. Commit the public,
secret-free templates and runbook:

```bash
git add ops/backup docs/runbooks/backup-restore.md sql/migrations/20260910_retention_recovery.sql
git commit -m "feat: add encrypted disaster recovery"
```

---

### Task 25: Bounded observability, retention, and operator health

**Files:**
- Create: `supabase/functions/app-api/health.ts`
- Create: `supabase/functions/app-api/health_test.ts`
- Create: `supabase/functions/run-scheduler/retention.ts`
- Create: `supabase/functions/run-scheduler/retention_test.ts`
- Create: `ops/health-monitor/wrangler.jsonc`
- Create: `ops/health-monitor/src/index.ts`
- Create: `ops/health-monitor/src/index.test.ts`
- Modify: `sql/migrations/20260910_retention_recovery.sql`
- Create: `docs/runbooks/incident-response.md`
- Create: `docs/runbooks/credential-rotation.md`

**Interfaces:**
- Consumes: bounded operational tables, admin membership, retention policy.
- Produces: aggregate health only; deterministic cleanup; credential and wrong-tenant incident procedures.

- [ ] **Step 1: Write failing disclosure tests**

Assert operator health excludes email, Telegram IDs, tickers, positions, quantities, cost basis,
recommendation text, rendered messages, prompts, tokens, and per-owner missed-run listings. Verify logs
exclude bodies and authorization headers.

- [ ] **Step 2: Implement aggregate health**

Return component status, deployed versions, provider-adapter state, aggregate missed-run counts, quota
pressure, backup age, restore age, and projection-check state. No admin mutation or impersonation route
exists.

Deploy an independent Cloudflare scheduled monitor that checks a public redacted Supabase health
endpoint and sends a fixed operational alert on unavailability. It must not carry a Supabase key,
portfolio data, or generate synthetic traffic intended to defeat free-project pausing.

- [ ] **Step 3: Implement retention**

Delete expired pairing codes after 24 hours, Telegram update IDs after 30 days, cancelled/expired
commands after 90 days while preserving bounded audit facts, and backup-deleted data by 35 days. Keep
decision records and active-account ledger/history. Compact full evidence only when its first row nears
12 months, preserving citations/hashes/decisions.

- [ ] **Step 4: Document incident and rotation paths**

Cover each Claude credential separately, Telegram bot/webhook, SMTP, R2, runtime DB roles, wrong-tenant
disclosure, operator-account compromise, provider-account loss, trigger pause, rollback, and user notice
within 72 hours for confirmed disclosure.

- [ ] **Step 5: Verify and commit**

Run redaction, retention, and authorization tests. Commit:

```bash
git add supabase/functions/app-api/health.ts supabase/functions/app-api/health_test.ts supabase/functions/run-scheduler/retention.ts supabase/functions/run-scheduler/retention_test.ts ops/health-monitor sql/migrations/20260910_retention_recovery.sql docs/runbooks/incident-response.md docs/runbooks/credential-rotation.md
git commit -m "feat: add privacy bounded operations"
```

---

### Task 26: CI, staging deployment, Cloudflare static hosting, and production gates

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy-staging.yml`
- Create: `.github/workflows/deploy-production.yml`
- Create: `apps/web/wrangler.jsonc`
- Create: `scripts/deploy_and_verify.py`
- Create: `docs/runbooks/deployment.md`
- Create: `docs/runbooks/rollback.md`

**Interfaces:**
- Consumes: committed lockfiles, staging/production environments, protected GitHub environments.
- Produces: deterministic CI, automatic staging deployment, manual protected production deployment, and rollback evidence.

- [ ] **Step 1: Write workflow policy tests**

Add a Python YAML test asserting pinned action major versions, least-privilege permissions, no pull-request
access to production secrets, no model API keys, no public backup artifacts, staging-before-production,
manual production environment, and explicit migration/Edge/web verification.

- [ ] **Step 2: Implement CI**

Run secret scan, dependency audit, Python/Node/Deno/web tests, Edge checks, SQL lint, migration-from-current
fixture, fresh-schema equivalence, exposed-surface allow-list, and build-output secret/service-worker scan.

- [ ] **Step 3: Implement staging deployment**

Apply migrations to staging, provision/revoke test runtime roles, deploy four Edge Functions, publish
static assets, create synthetic users, run PostgREST attacks, execute Claude no-write handshake when
credentials are available, and run browser smoke tests.

- [ ] **Step 4: Implement protected production deployment**

Require GitHub environment approval, fresh encrypted backup under 36 hours, successful restore under 30
days, clean staging acceptance, explicit trigger pause, migration verifier, deploy, owner-only smoke,
and rollback command. It cannot enable friend invitations.

- [ ] **Step 5: Verify and commit**

Run workflow tests and a staging deployment. Commit:

```bash
git add .github/workflows apps/web/wrangler.jsonc scripts/deploy_and_verify.py docs/runbooks/deployment.md docs/runbooks/rollback.md
git commit -m "ci: gate staging and production delivery"
```

---

### Task 27: End-to-end browser, tenant, provider, and recovery acceptance

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/auth.spec.ts`
- Create: `apps/web/e2e/portfolio.spec.ts`
- Create: `apps/web/e2e/research.spec.ts`
- Create: `apps/web/e2e/connections.spec.ts`
- Create: `apps/web/e2e/tenant-isolation.spec.ts`
- Create: `tests/security/test_release_acceptance.py`
- Create: `docs/runbooks/release-acceptance.md`

**Interfaces:**
- Consumes: fully deployed staging, two synthetic owners, synthetic Telegram updates, test Claude connection, and latest restore receipt.
- Produces: one machine-readable Gate A-G acceptance report with evidence hashes and no private data.

- [ ] **Step 1: Encode every release criterion**

Map each bullet in spec section 23 to a named automated or signed-manual assertion. The release report
must fail for any missing result; `skipped`, `unknown`, and stale evidence are not passes.

- [ ] **Step 2: Exercise two-owner browser flows**

Sign in with OTP fixtures, accept consent, read each screen, record/cancel/correct portfolio events,
manage plan/settings, pair/unlink Telegram, connect/revoke Claude, export, step up, and request/cancel
deletion. Attempt every cross-owner ID through browser and raw PostgREST.

- [ ] **Step 3: Exercise scheduling and provider failures**

Simulate holiday, early close, DST, duplicate tick, Run Now overlap, trigger rejection/unknown, stale
evidence, copied morning packet, quote conflict, split quarantine, quiet intraday, delivery unknown, and
missed completion. Verify no duplicate writes or stale-current advice.

- [ ] **Step 4: Exercise recovery and rollback**

Use the latest encrypted R2 archive to restore staging, verify identities/ledger/projections/tombstones,
reconnect provider triggers, restore Telegram webhook with rotated secrets, and roll back one staged
application release while triggers remain paused.

- [ ] **Step 5: Verify and commit**

Run Playwright on desktop/mobile projects, full security suite, and `npm run test:all`. Commit:

```bash
git add apps/web/playwright.config.ts apps/web/e2e tests/security/test_release_acceptance.py docs/runbooks/release-acceptance.md
git commit -m "test: prove complete product acceptance"
```

---

### Task 28: Owner cutover and controlled soak

**Files:**
- Create: `scripts/cutover_owner.py`
- Create: `tests/test_cutover_owner.py`
- Create: `docs/runbooks/owner-cutover.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: passing Gate 0-E report, fresh backup, paused legacy Routines/webhook mutations, owner Auth identity.
- Produces: reversible owner migration receipt and an owner-only complete scheduled-cycle soak.

- [ ] **Step 1: Write failing precondition tests**

Reject cutover for stale backup, old restore drill, failed capability, unpaused legacy routines,
unverified owner identity, count/digest mismatch, active old webhook mutation path, or missing rollback
version.

- [ ] **Step 2: Implement the guarded cutover controller**

Sequence: pause triggers/mutations; backup and verify; migrate/backfill; provision scoped roles; deploy;
register new webhook; connect one Claude Routine; run no-write handshake; verify all views; record one web
and one Telegram mutation with explicit confirmation; resume one phase at a time.

After replacement paths pass, revoke and remove the old runtime service-role secrets, static Telegram
owner IDs, legacy broad Routine gateway secret, three legacy schedules, and obsolete public RPC grants.
Keep the reviewed previous function bundle available only as rollback code, not as a reachable endpoint.

- [ ] **Step 3: Preserve immediate rollback**

On any failure, pause new triggers, restore reviewed previous functions, retain additive owner columns,
and never destructively down-migrate. Data-integrity uncertainty keeps all mutation paths paused.

- [ ] **Step 4: Observe a complete market cycle**

Verify pre-market, quiet/active intraday, post-market, maintenance, backup, and operational alerts.
Compare actual writes, publications, message IDs, and run summaries. Keep invitations disabled for the
documented owner soak.

- [ ] **Step 5: Verify and commit**

Store only sanitized receipt hashes in docs, update roadmap/handoff with exact live status, and commit:

```bash
git add scripts/cutover_owner.py tests/test_cutover_owner.py docs/runbooks/owner-cutover.md docs/ROADMAP.md docs/HANDOFF.md
git commit -m "ops: complete owner only cutover"
```

---

### Task 29: One-friend onboarding gate and final documentation

**Files:**
- Create: `docs/runbooks/friend-onboarding.md`
- Modify: `docs/privacy.md`
- Modify: `docs/risk-disclosure.md`
- Modify: `README.md`
- Modify: `routines/README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `sql/schema.sql`

**Interfaces:**
- Consumes: successful owner soak, backup under 36 hours, restore under 30 days, unassisted Claude setup proof, custom SMTP, and passing acceptance report.
- Produces: one trusted-friend onboarding receipt, canonical fresh-install schema, and complete operator/user documentation.

- [ ] **Step 1: Make fresh schema match migration history**

Regenerate/review `sql/schema.sql` from the applied staging schema, strip passwords/secret values and
platform-owned definitions, apply it to a fresh disposable database, and compare catalogs against the
ordered migration result.

- [ ] **Step 2: Write the onboarding checklist**

Require phone OTP, disclosures/consent, no brokerage capability, separate Claude account/allowance,
unassisted kit, real no-write handshake, Telegram private pairing, export, deletion explanation,
support limits, and single-operator availability limitation.

- [ ] **Step 3: Invite synthetic then one trusted friend**

Run the synthetic account through isolation/deletion/export first. Invite one friend only when every
Gate G prerequisite is current. Never batch multiple owners into a model prompt, credential, run, or
support screenshot.

- [ ] **Step 4: Review the first friend cycle**

Check provider usage, source health, notification noise, support burden, tenant isolation, ledger
projection, backup age, and absence of private data in operations. Any isolation, recovery, SMTP,
provider, or ledger failure disables further invitations.

- [ ] **Step 5: Final full verification**

Run:

```bash
npm run test:all
python scripts/verify_multitenancy_migration.py --staging
.venv/bin/python -m pytest tests/security/test_release_acceptance.py -q
npm --workspace apps/web run test:e2e
git diff --check
git status --short
```

Expected: all automated tests pass, Gate A-G report is complete, worktree contains only intentional
documentation status updates, and no broker/model API/service-role runtime secret exists.

- [ ] **Step 6: Commit the release documentation**

```bash
git add README.md routines/README.md docs/ROADMAP.md docs/runbooks/friend-onboarding.md docs/privacy.md docs/risk-disclosure.md sql/schema.sql
git commit -m "docs: complete invite only product launch"
```

---

## Execution checkpoints

1. **After Task 2:** Gate 0 live capabilities must pass before any promise of Claude triggering, SMTP,
   R2 recovery, corporate-action normalization, or free-tier runtime behavior is treated as proven.
   Code and local/staging work may continue, but production/friend activation remains blocked.
2. **After Task 7:** review the schema, RLS attack matrix, and machine grants before ledger or provider
   code depends on them.
3. **After Task 12:** prove web and Telegram reach the identical command state machine.
4. **After Task 18:** prove one full synthetic scheduled lifecycle, including failure and unknown states.
5. **After Task 23:** complete the seven-screen product and phone-auth/account lifecycle review.
6. **After Task 24:** perform a real encrypted backup and restore; backup creation without restore is a
   failed gate.
7. **After Task 27:** freeze feature changes; only acceptance defects may change release scope.
8. **After Task 28:** owner soak comes before any friend data.
9. **After Task 29:** mark the goal complete only when every approved acceptance criterion is evidenced
   and no required work remains.

## Specification coverage matrix

| Approved specification area | Implemented and proved by |
|---|---|
| Product principles, record-only boundary, no brokerage | Global constraints; Tasks 9, 12, 15, 20, 27, 29 |
| Invite-only Auth, SMTP/OTP, consent, one USD portfolio | Tasks 2, 4, 19, 23, 27, 29 |
| Ledger, fees, precision, corrections, plans, projections | Tasks 3, 8, 9, 20, 27 |
| Provider-neutral contract with Claude-only launch | Tasks 3, 13, 15, 17, 22 |
| Fresh intraday analysis and server evidence | Tasks 13-16, 18, 21, 27 |
| Market data, policy, corporate actions, outcomes | Tasks 13-15, 18, 21, 27 |
| Market-aware scheduling, DST, holidays, run exclusivity | Tasks 16, 18, 21, 27 |
| Multi-user Telegram pairing and confirmation | Tasks 9, 11, 12, 18, 22, 27 |
| Seven-screen static web product and browser security | Tasks 10, 19-23, 27 |
| Private schema, RLS, machine roles, no runtime service role | Tasks 4-7, 10, 12, 15-16, 24, 27 |
| Rate limits, invocation budget, truthful receipts | Tasks 7, 10, 15-18, 27 |
| Export, deletion, reset, retention, privacy | Tasks 23, 25, 27, 29 |
| Encrypted off-site backup and tested recovery | Tasks 2, 24-27 |
| Observability, operations, incident response, cost limits | Tasks 18, 24-26, 29 |
| Existing-data migration, rollback, owner soak, friend gate | Tasks 5-6, 26-29 |

## Implementation order and rollback rule

Execute Tasks 1-29 in order. Each commit must leave all prior tests green. Staging is the only target
through Task 27. Production changes occur only through the guarded Task 28 controller after a fresh
backup and passing restore. If a Gate 0 platform assumption fails, update the approved design and obtain
new approval instead of silently substituting a paid API, public backup, weaker auth, always-on Mac, or
broader credential.
