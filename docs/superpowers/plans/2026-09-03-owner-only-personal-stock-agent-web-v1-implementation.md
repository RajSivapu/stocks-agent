# Owner-Only Personal Stock Agent Web v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, secure, verify, and deploy the approved seven-page owner-only Personal Stock Agent
Web v1 without adding financial writes, run triggers, friend access, brokerage authority, or
unsupported financial claims.

**Architecture:** A static React/Vite application authenticates the one pre-created owner through
Supabase Auth and calls a separate `owner-dashboard-api` Edge Function. The function performs exact
owner JWT verification and reads fixed allowlisted columns through a dedicated PostgreSQL
`NOBYPASSRLS` login with direct `SELECT` privileges only. Versioned view models are shared from one
canonical contracts package; the browser never receives database credentials or raw gateway rows.

**Tech Stack:** React 19, TypeScript 6, Vite 8, React Router 7, Supabase JS 2, Vitest 4, Testing
Library, axe-core, Playwright, Deno 2.9, jose 6.2, postgres.js 3.4, PostgreSQL/Supabase, Python 3
deployment verifiers, Cloudflare Pages static hosting.

**Spec:** `docs/design/2026-09-03-owner-only-personal-stock-agent-web-app-plan.md`

## Global Constraints

- Personal and single-owner; public sign-up and friend invitations remain disabled.
- Suggestion-only; no autonomous execution, brokerage credentials, links, orders, or authority.
- No web financial mutation and no route that triggers a Routine, provider request, Telegram send,
  or gateway operation.
- Browser and Edge Function receive no service-role, provider, Telegram, or gateway secret.
- Dashboard database access is direct `SELECT` only through `stock_agent_dashboard`; zero
  application RPC calls.
- Missing owner configuration fails closed with `503` before database setup.
- Every displayed write/send/current/policy claim is derived from a persisted receipt and timestamp.
- Wider portfolio comparisons remain unavailable unless structured details were persisted; never
  parse presentation text or recompute the gateway's conclusion.
- Theme defaults to browser/device `system`, with owner-selectable Light and Dark overrides.
- WCAG 2.2 AA contrast takes precedence over exact visual token values.
- No production fixture writes and no duplicate scheduled market run for dashboard verification.

---

### Task 1: Workspace, canonical contracts, and production security shell

**Files:**
- Create: `package.json`
- Create: `package-lock.json`
- Create: `packages/dashboard-contracts/package.json`
- Create: `packages/dashboard-contracts/tsconfig.json`
- Create: `packages/dashboard-contracts/src/index.ts`
- Create: `packages/dashboard-contracts/src/index.test.ts`
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/index.html`
- Create: `apps/web/public/theme-bootstrap.js`
- Create: `apps/web/public/_headers`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/styles.css`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/src/security-config.test.ts`

**Interfaces:**
- Produces: `DashboardEnvelope<T>`, `DashboardErrorEnvelope`, `Freshness`, `ReceiptStatus`,
  `TodayView`, `PortfolioView`, `IdeasView`, `CompanionView`, `AlertsView`, `RunsView`, `RunDetailView`,
  and `SystemView` from `@stocks-agent/dashboard-contracts`.
- Produces: production `_headers` with exact Supabase/API origins substituted at build time.
- Consumes: no production data or credentials.

- [x] **Step 1: Write failing contract and security configuration tests**

```ts
import { describe, expect, it } from "vitest";
import { parseDashboardEnvelope } from "./index";

describe("dashboard contracts", () => {
  it("rejects an unknown contract version", () => {
    expect(() => parseDashboardEnvelope({ contract_version: 2 })).toThrow("contract_version");
  });

  it("requires an explicit freshness state", () => {
    expect(() => parseDashboardEnvelope({ contract_version: 1, data: {} })).toThrow("freshness");
  });
});
```

```ts
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("ships a CSP without inline or third-party scripts", () => {
  const headers = readFileSync(new URL("../public/_headers", import.meta.url), "utf8");
  expect(headers).toContain("default-src 'none'");
  expect(headers).toContain("script-src 'self'");
  expect(headers).not.toContain("'unsafe-inline'");
});
```

- [x] **Step 2: Run the tests and verify RED**

Run: `npm test --workspace packages/dashboard-contracts -- --run`

Expected: FAIL because the workspace, parser, and security files do not exist.

- [x] **Step 3: Add the pinned workspace and minimal canonical contracts**

```ts
export type Freshness = "fresh" | "stale" | "partial" | "unavailable";

export interface DashboardEnvelope<T> {
  contract_version: 1;
  request_id: string;
  generated_at: string;
  data_as_of: string | null;
  freshness: Freshness;
  market_state: string;
  data: T;
  next_cursor?: string | null;
}

export function parseDashboardEnvelope(value: unknown): DashboardEnvelope<unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("dashboard envelope must be an object");
  }
  const row = value as Record<string, unknown>;
  if (row.contract_version !== 1) throw new Error("unsupported contract_version");
  if (!["fresh", "stale", "partial", "unavailable"].includes(String(row.freshness))) {
    throw new Error("invalid freshness");
  }
  return row as unknown as DashboardEnvelope<unknown>;
}
```

Pin the exact dependency versions in the task header and commit the generated lockfile. Configure
Vite to reject non-canonical production Supabase/API origins and replace static `_headers` markers
at build time. Load `/theme-bootstrap.js` synchronously before the application module; it may read
only `personal-stock-agent-theme` and set `document.documentElement.dataset.theme`.

- [x] **Step 4: Run contract, header, type, and build checks and verify GREEN**

Run: `npm test --workspace packages/dashboard-contracts -- --run && npm test --workspace @stocks-agent/web -- --run src/security-config.test.ts && npm run typecheck --workspace @stocks-agent/web && VITE_SUPABASE_URL=https://test-project.supabase.co VITE_DASHBOARD_API_URL=https://test-project.supabase.co/functions/v1/owner-dashboard-api VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test npm run build --workspace @stocks-agent/web`

Expected: all commands exit 0; `apps/web/dist/_headers` has exact origins and no marker or
`unsafe-inline`.

- [x] **Step 5: Commit Task 1**

```bash
git add package.json package-lock.json packages/dashboard-contracts apps/web
git commit -m "feat: scaffold secure owner dashboard"
```

### Task 2: Structurally read-only dashboard database role

**Files:**
- Create: `sql/migrations/20260906_owner_dashboard_read_role.sql`
- Modify: `sql/schema.sql`
- Create: `scripts/provision_dashboard_runtime_role.py`
- Create: `scripts/verify_owner_dashboard_role.py`
- Create: `tests/test_provision_dashboard_runtime_role.py`
- Create: `tests/test_verify_owner_dashboard_role.py`
- Modify: `tests/test_security_invariants.py`
- Modify: `supabase/.env.example`

**Interfaces:**
- Produces: NOLOGIN role `stock_agent_dashboard` and LOGIN role
  `stock_agent_dashboard_runtime`.
- Produces: `DASHBOARD_DATABASE_URL` using the Supavisor session endpoint on port 5432.
- Produces: `verify_dashboard_role(connection) -> dict[str, object]` with explicit privilege
  evidence.
- Consumes: the tables and columns enumerated in spec section 12.

- [x] **Step 1: Write failing migration/provisioning/verifier tests**

```python
def test_dashboard_migration_is_select_only():
    sql = MIGRATION.read_text()
    assert "CREATE ROLE stock_agent_dashboard" in sql
    assert "NOBYPASSRLS" in sql
    assert "FOR SELECT TO stock_agent_dashboard" in sql
    assert "GRANT SELECT" in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT EXECUTE" not in sql

def test_verifier_rejects_write_privilege(fake_connection):
    fake_connection.table_grants = [{"privilege_type": "UPDATE"}]
    with pytest.raises(RuntimeError, match="unexpected privilege"):
        verify_dashboard_role(fake_connection)
```

- [x] **Step 2: Run the focused Python tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_provision_dashboard_runtime_role.py tests/test_verify_owner_dashboard_role.py tests/test_security_invariants.py -q`

Expected: FAIL because the migration and scripts do not exist.

- [x] **Step 3: Add the role migration and schema mirror**

The migration must create the NOLOGIN privilege role idempotently, revoke inherited schema/object
privileges, grant `USAGE` on `public`, grant only required columns on the 14 dashboard source tables,
and add named `FOR SELECT TO stock_agent_dashboard USING (true)` RLS policies. It must not grant
access to `auth.users`, `portfolio_commands`, `telegram_updates`, decrypted secrets, or any function.

```sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_agent_dashboard') THEN
    CREATE ROLE stock_agent_dashboard NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;
REVOKE ALL ON SCHEMA public FROM stock_agent_dashboard;
GRANT USAGE ON SCHEMA public TO stock_agent_dashboard;
GRANT SELECT (ticker, shares, avg_cost, bucket, opened_at, stop, target)
  ON public.holdings TO stock_agent_dashboard;
CREATE POLICY owner_dashboard_select_holdings ON public.holdings
  FOR SELECT TO stock_agent_dashboard USING (true);
```

The complete allowlist covers 15 dashboard source tables: `holdings` plus `transactions`,
`owner_investment_plans`,
`analysis_runs`, `market_gateway_requests`, `decision_evaluations`, `suggestions`,
`suggestion_grades`, `market_publications`, `market_policy_config`, `market_alert_drafts`,
`market_alert_rules`, `market_alert_rule_versions`, `market_alert_events`, and
`market_alert_actions`. Mirror the migration exactly in `sql/schema.sql`.

- [x] **Step 4: Implement credential provisioning and structural verification**

Adapt only the URL-validation and secret-publication mechanics from the deferred branch. Create one
random 36-byte-or-longer password, one runtime login, and publish only `DASHBOARD_DATABASE_URL`.
The verifier must check `rolsuper`, `rolcreatedb`, `rolcreaterole`, `rolbypassrls`, memberships,
schema privileges, table/column grants, application-function execution, and object ownership.

- [x] **Step 5: Run focused and full Python security tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_provision_dashboard_runtime_role.py tests/test_verify_owner_dashboard_role.py tests/test_security_invariants.py -q`

Expected: all tests pass with no secret value in captured output.

- [x] **Step 6: Commit Task 2**

```bash
git add sql scripts tests supabase/.env.example
git commit -m "feat: add read-only dashboard database role"
```

### Task 3: Owner-only API boundary, JWT, CORS, and routes

**Files:**
- Create: `supabase/functions/deno.json`
- Create: `supabase/functions/owner-dashboard-api/index.ts`
- Create: `supabase/functions/owner-dashboard-api/auth.ts`
- Create: `supabase/functions/owner-dashboard-api/cors.ts`
- Create: `supabase/functions/owner-dashboard-api/errors.ts`
- Create: `supabase/functions/owner-dashboard-api/routes.ts`
- Create: `supabase/functions/owner-dashboard-api/handler.ts`
- Create: `supabase/functions/owner-dashboard-api/auth_test.ts`
- Create: `supabase/functions/owner-dashboard-api/handler_test.ts`
- Modify: `supabase/config.toml`

**Interfaces:**
- Produces: `verifyOwnerRequest(request, jwks, ownerUserId, projectUrl)`.
- Produces: `resolveDashboardRoute(method, pathname, searchParams)` for the documented GET routes.
- Produces: `createOwnerDashboardHandler(dependencies): (request) => Promise<Response>`.
- Consumes: `DashboardRepository` from Task 4 through dependency injection.

- [x] **Step 1: Write failing authorization, preflight, and method tests**

```ts
Deno.test("missing owner configuration fails before repository access", async () => {
  let accessed = false;
  const handler = createOwnerDashboardHandler(testDeps({
    ownerUserId: "",
    repository: { read: async () => { accessed = true; return {}; } },
  }));
  const response = await handler(ownerRequest("/v1/meta"));
  assertEquals(response.status, 503);
  assertEquals(accessed, false);
});

Deno.test("exact-origin OPTIONS succeeds without authorization", async () => {
  const response = await handler(preflight("https://dashboard.example"));
  assertEquals(response.status, 204);
  assertEquals(response.headers.get("access-control-allow-origin"), "https://dashboard.example");
});

Deno.test("POST is unavailable", async () => {
  assertEquals((await handler(ownerRequest("/v1/portfolio", { method: "POST" }))).status, 405);
});
```

- [x] **Step 2: Run Deno API tests and verify RED**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api`

Expected: FAIL because the API modules do not exist.

- [x] **Step 3: Implement minimal fail-closed boundary**

Use asymmetric JWKS verification only (`ES256`, `RS256`, `EdDSA`), issuer
`${SUPABASE_URL}/auth/v1`, audience `authenticated`, canonical UUID subject, and a maximum 900-second
JWT lifetime. Validate owner configuration before constructing the database repository. Require the
owner JWT on `/meta` and every GET route. Return only the documented bounded error envelope.

- [x] **Step 4: Configure preflight-safe Edge behavior**

```toml
[functions."owner-dashboard-api"]
enabled = true
verify_jwt = false
entrypoint = "./functions/owner-dashboard-api/index.ts"
```

The handler accepts only exact configured origins. `OPTIONS` advertises `GET`; all non-OPTIONS calls
authenticate in-function. No wildcard origin or credential-reflecting fallback exists.

- [x] **Step 5: Run Deno API tests and check the entrypoint and verify GREEN**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api && npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/owner-dashboard-api/index.ts`

Expected: all tests and type checks exit 0.

- [x] **Step 6: Commit Task 3**

```bash
git add supabase/config.toml supabase/functions
git commit -m "feat: enforce owner-only dashboard API boundary"
```

### Task 4: Direct-SELECT repository, freshness, and receipt view models

**Files:**
- Create: `supabase/functions/owner-dashboard-api/database.ts`
- Create: `supabase/functions/owner-dashboard-api/repository.ts`
- Create: `supabase/functions/owner-dashboard-api/freshness.ts`
- Create: `supabase/functions/owner-dashboard-api/mappers.ts`
- Create: `supabase/functions/owner-dashboard-api/repository_test.ts`
- Create: `supabase/functions/owner-dashboard-api/freshness_test.ts`
- Create: `supabase/functions/owner-dashboard-api/mappers_test.ts`
- Modify: `supabase/functions/owner-dashboard-api/handler.ts`

**Interfaces:**
- Produces: `createDashboardRepository(databaseUrl, factory?)` with `meta`, `today`, `portfolio`,
  `transactions`, `ideas`, `companion`, `alerts`, `runs`, `runDetail`, and `system` SELECT methods.
- Produces: `classifyFreshness(input, calendar): FreshnessResult`.
- Produces: allowlisted contract mappers consumed by the API handler.
- Consumes: `@stocks-agent/dashboard-contracts` and no gateway/Telegram/provider module.

- [x] **Step 1: Write failing repository and freshness tests**

```ts
Deno.test("repository issues only fixed parameterized SELECT statements", async () => {
  const recorder = recordingSql();
  const repository = createDashboardRepository(TEST_DATABASE_URL, recorder.factory);
  await repository.portfolio(null);
  assert(recorder.statements.length > 0);
  assert(recorder.statements.every((statement) => /^SELECT\b/i.test(statement.trim())));
  assert(recorder.statements.every((statement) => !/\b(CALL|INSERT|UPDATE|DELETE|RPC)\b/i.test(statement)));
});

Deno.test("Friday close remains as-of-close over a weekend", () => {
  const result = classifyPrice(receipt("2026-09-04T20:00:00Z"), now("2026-09-06T15:00:00Z"), calendar);
  assertEquals(result.market_state, "as_of_close");
  assertEquals(result.data_as_of, "2026-09-04T20:00:00.000Z");
});
```

- [x] **Step 2: Run focused repository tests and verify RED**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api/repository_test.ts supabase/functions/owner-dashboard-api/freshness_test.ts supabase/functions/owner-dashboard-api/mappers_test.ts`

Expected: FAIL because repository, freshness, and mappers are missing.

- [x] **Step 3: Implement the bounded database adapter and repository**

Use `npm:postgres@3.4.9`, one connection, five-second connect/statement timeouts, TLS required,
prepared queries, and a validated Supavisor port-5432 URL. Write fixed tagged-template queries only;
allow cursor and enum parameters but never table, column, sort, or SQL-fragment input. Cap holdings
and plans at 100, list pages at 50, source arrays at 20, and raw text at contract limits.

- [x] **Step 4: Implement calendar-aware freshness and claim mappers**

Reuse the current NYSE holiday list and session helpers without calling the gateway. Apply the spec
section 13 rules. Map `market_publications.status='delivered'` to sent only when message IDs exist;
map `suppressed` to an explicit no-send state; show policy approval only from the evaluation's
specific policy version; map only structured `companion_analysis`; and omit portfolio totals when a
required price is absent or stale.

- [x] **Step 5: Wire all GET routes and verify GREEN**

Run: `npx --yes deno@2.9.6 test --config supabase/functions/deno.json supabase/functions/owner-dashboard-api && npx --yes deno@2.9.6 check --config supabase/functions/deno.json supabase/functions/owner-dashboard-api/index.ts`

Expected: all routes return contract version 1, bounded data, and `Cache-Control: no-store`; all tests
and checks exit 0.

- [x] **Step 6: Commit Task 4**

```bash
git add supabase/functions/owner-dashboard-api packages/dashboard-contracts
git commit -m "feat: add receipt-backed dashboard read models"
```

### Task 5: Owner authentication, theme system, and responsive application shell

**Files:**
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/app/App.tsx`
- Create: `apps/web/src/app/AppShell.tsx`
- Create: `apps/web/src/app/ErrorBoundary.tsx`
- Create: `apps/web/src/auth/AuthProvider.tsx`
- Create: `apps/web/src/auth/SignInPage.tsx`
- Create: `apps/web/src/auth/auth.test.tsx`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/api/client.test.ts`
- Create: `apps/web/src/theme/theme.ts`
- Create: `apps/web/src/theme/ThemeControl.tsx`
- Create: `apps/web/src/theme/theme.test.tsx`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/global.css`
- Create: `apps/web/src/app/AppShell.test.tsx`

**Interfaces:**
- Produces: `useAuth()` with `session`, `locked`, `sendOtp`, `verifyOtp`, `signOut`, and `unlock`.
- Produces: `dashboardClient.get<T>(path, token)` with timeout and contract validation.
- Produces: `ThemeMode = "system" | "light" | "dark"` and accessible application navigation.
- Consumes: canonical dashboard contracts and the public Supabase configuration only.

- [ ] **Step 1: Write failing auth, theme, and API-client tests**

```tsx
it("requests OTP with account creation disabled", async () => {
  await user.type(screen.getByLabelText(/email/i), "owner@example.com");
  await user.click(screen.getByRole("button", { name: /send code/i }));
  expect(signInWithOtp).toHaveBeenCalledWith(expect.objectContaining({
    options: expect.objectContaining({ shouldCreateUser: false }),
  }));
});

it("does not call the financial API before authentication", () => {
  render(<App />);
  expect(fetch).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run focused frontend tests and verify RED**

Run: `npm test --workspace @stocks-agent/web -- --run src/auth src/api src/theme src/app/AppShell.test.tsx`

Expected: FAIL because the application shell modules do not exist.

- [ ] **Step 3: Implement authentication and data clearing**

Create the Supabase client with a custom `sessionStorage` adapter, refresh rotation support, and no
financial persistence. Use neutral OTP UI copy. The 30-minute inactivity lock clears rendered
financial state and requires reauthentication; it is labeled as screen privacy. Explicit sign-out
calls `supabase.auth.signOut({ scope: "global" })` and fails closed locally even if the network is
unavailable.

- [ ] **Step 4: Implement the approved shell and theme pair**

Use the approved Midnight Navy/Warm Gold dark tokens and Warm Pearl/Midnight Navy/Gold light tokens.
Build a semantic sidebar/top-drawer navigation for Today, Portfolio, Ideas, Companion, Alerts, Runs,
and System. Provide System/Light/Dark radio controls, visible focus, reduced motion, status text plus
icons, and a persistent data-time bar after authentication.

- [ ] **Step 5: Run frontend tests, typecheck, and lint and verify GREEN**

Run: `npm test --workspace @stocks-agent/web -- --run && npm run typecheck --workspace @stocks-agent/web && npm run lint --workspace @stocks-agent/web`

Expected: all commands exit 0 with no console warning.

- [ ] **Step 6: Commit Task 5**

```bash
git add apps/web packages/dashboard-contracts
git commit -m "feat: add owner dashboard auth and visual shell"
```

### Task 6: Seven receipt-driven dashboard pages

**Files:**
- Create: `apps/web/src/components/AsyncView.tsx`
- Create: `apps/web/src/components/FreshnessBadge.tsx`
- Create: `apps/web/src/components/ReceiptTimeline.tsx`
- Create: `apps/web/src/components/SafeTelegramPreview.tsx`
- Create: `apps/web/src/components/SafeSourceLink.tsx`
- Create: `apps/web/src/features/today/TodayPage.tsx`
- Create: `apps/web/src/features/portfolio/PortfolioPage.tsx`
- Create: `apps/web/src/features/ideas/IdeasPage.tsx`
- Create: `apps/web/src/features/companion/CompanionPage.tsx`
- Create: `apps/web/src/features/alerts/AlertsPage.tsx`
- Create: `apps/web/src/features/runs/RunsPage.tsx`
- Create: `apps/web/src/features/runs/RunDetailPage.tsx`
- Create: `apps/web/src/features/system/SystemPage.tsx`
- Create: `apps/web/src/features/pages.test.tsx`
- Create: `apps/web/src/components/SafeTelegramPreview.test.tsx`
- Modify: `apps/web/src/app/App.tsx`
- Modify: `apps/web/src/styles/global.css`

**Interfaces:**
- Produces: seven lazy-loaded routes plus `/runs/:id`.
- Produces: safe text/link rendering without `dangerouslySetInnerHTML`.
- Consumes: only typed `dashboardClient` responses.

- [ ] **Step 1: Write failing page semantics and hostile-content tests**

```tsx
it("renders attention first and collapses empty optional blocks", async () => {
  render(<TodayPage data={todayFixture({ companion: null, entry_zones: [] })} />);
  expect(screen.getByRole("heading", { name: /needs attention/i })).toBeVisible();
  expect(screen.queryByRole("heading", { name: /entry zones/i })).not.toBeInTheDocument();
});

it("renders stored markup as text and disables unsafe links", () => {
  render(<SafeTelegramPreview text={'<img src=x onerror="alert(1)">'} links={[{ label: "bad", url: "javascript:alert(1)" }]} />);
  expect(screen.getByText(/<img src=x/)).toBeVisible();
  expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run page tests and verify RED**

Run: `npm test --workspace @stocks-agent/web -- --run src/features/pages.test.tsx src/components/SafeTelegramPreview.test.tsx`

Expected: FAIL because the pages and safe renderers do not exist.

- [ ] **Step 3: Implement Today, Portfolio, Ideas, and Companion**

Keep attention first; collapse empty optional blocks. Use tabular numerals for money but omit value
and P&L when price support is missing. Separate observed evidence, Analyst, Checker, deterministic
policy, and outcome grade. Use only past-tense historical wording; show no “best/top/winner” label.
Show wider comparisons unavailable when only count/coverage exists.

- [ ] **Step 4: Implement Alerts, Runs, Run Detail, and System**

Alerts show delivery/suppression state, hash, template, attempts, event/rule history, and message IDs
only from receipts. Runs connect stages by IDs and label missing stages incomplete. System shows
receipt-derived phase status and immutable owner-only/suggestion-only/friends-disabled/no-brokerage
boundaries; it never calls page-load success system health.

- [ ] **Step 5: Run page, accessibility-unit, type, lint, and build checks and verify GREEN**

Run: `npm test --workspace @stocks-agent/web -- --run && npm run typecheck --workspace @stocks-agent/web && npm run lint --workspace @stocks-agent/web && VITE_SUPABASE_URL=https://test-project.supabase.co VITE_DASHBOARD_API_URL=https://test-project.supabase.co/functions/v1/owner-dashboard-api VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test npm run build --workspace @stocks-agent/web`

Expected: all commands exit 0 and the compressed initial application JavaScript remains below the
250 KB target.

- [ ] **Step 6: Commit Task 6**

```bash
git add apps/web packages/dashboard-contracts
git commit -m "feat: add receipt-driven owner dashboard pages"
```

### Task 7: Browser, accessibility, supply-chain, and regression verification

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/dashboard.spec.ts`
- Create: `apps/web/e2e/live-readonly.spec.ts`
- Create: `scripts/check_dashboard_bundle.mjs`
- Create: `scripts/test_all.sh`
- Modify: `package.json`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Produces: deterministic desktop/mobile/light/dark browser evidence.
- Produces: built-asset secret/fixture/CSP scan and unified `npm run test:all`.
- Consumes: fixture-mode frontend and mocked/local API only unless `E2E_LIVE=1` is explicitly set.

- [ ] **Step 1: Write failing browser and bundle-security checks**

```ts
test("owner dashboard is keyboard usable in light and dark modes", async ({ page }) => {
  await page.goto("/?fixture=complete");
  await expect(page.getByRole("navigation")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus-visible")).toBeVisible();
  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
```

The bundle checker must fail on service-role/JWT/provider/Telegram secret patterns, owner UUIDs,
fixture ticker data in production assets, source maps, unresolved header markers, inline scripts, or
`unsafe-inline`.

- [ ] **Step 2: Run browser/security checks and verify RED**

Run: `npm run test:e2e --workspace @stocks-agent/web && node scripts/check_dashboard_bundle.mjs apps/web/dist`

Expected: FAIL because the browser configuration and bundle checker do not exist.

- [ ] **Step 3: Implement deterministic browser fixtures and full checks**

Exercise 320, 390, 768, 1024, and 1440 CSS-pixel layouts; keyboard navigation; visible focus;
System/Light/Dark persistence; hostile Telegram/source content; stale/partial/unavailable states;
owner-only denial; expired session; direct run-detail links; and suppressed/no-send alerts. Use
`axe-core` on every route and major state.

- [ ] **Step 4: Run the complete local verification suite and verify GREEN**

Run: `npm run test:all`

Expected: Python, Node, Deno, contracts, frontend unit, accessibility, type, lint, build, bundle scan,
and Playwright checks all exit 0.

- [ ] **Step 5: Commit Task 7**

```bash
git add apps/web scripts package.json README.md docs/ROADMAP.md
git commit -m "test: verify owner dashboard end to end"
```

### Task 8: Independent review, protected deployment, and receipt-backed canary

**Files:**
- Create: `docs/reviews/2026-09-03-owner-dashboard-code-review.md`
- Create: `docs/rollouts/2026-09-03-owner-dashboard-web-v1.md`
- Create: `scripts/deploy_owner_dashboard_api.py`
- Create: `scripts/verify_owner_dashboard_deployment.py`
- Create: `tests/test_deploy_owner_dashboard_api.py`
- Create: `tests/test_verify_owner_dashboard_deployment.py`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Produces: review disposition, protected deployment receipt, structural privilege evidence, HTTP
  header/auth evidence, and source-receipt reconciliation.
- Consumes: production deployment authority already available to the protected Stocks Agent path,
  plus an owner-controlled Cloudflare Pages project and the pre-created owner Auth account.

- [ ] **Step 1: Record an adversarial Claude code review against the exact commit range**

The review prompt must name `codex/owner-alert-v3`, the design, this plan, base SHA, head SHA, and the
ten targets in design section 25. Verify every finding against the current branch. Resolve Critical
and Important findings with new failing tests before deployment; record rejected findings with code
and test evidence.

- [ ] **Step 2: Write failing deployment-script tests**

Test that deployment refuses a non-canonical project reference, absent/malformed owner UUID,
non-Supavisor database URL, missing role verifier evidence, dirty or unpushed commit, service-role
secret in the function manifest, failed local suite, or non-exact origin.

- [ ] **Step 3: Run deployment-script tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_deploy_owner_dashboard_api.py tests/test_verify_owner_dashboard_deployment.py -q`

Expected: FAIL because the deployment scripts do not exist.

- [ ] **Step 4: Implement and verify the protected API deployment path**

Provision the runtime login, publish only reviewed dashboard secrets, deploy
`owner-dashboard-api`, and capture CLI version, project ref digest, git SHA, function version, and
timestamp without logging secret values. Verify exact CORS, unauthenticated and non-owner denial,
missing-owner kill switch in an isolated environment, JWT maximum lifetime, no service-role secret,
and database privilege structure.

- [ ] **Step 5: Deploy the immutable static build and verify browser security**

Deploy only after a Cloudflare Pages project/domain is explicitly available. Capture deployment ID,
commit SHA, HTTPS origin, `_headers`, asset hashes, and rollback target. Verify CSP, HSTS,
frame-ancestors, no-referrer, nosniff, permissions policy, no source maps, no fixture data, and exact
API/Supabase origins.

- [ ] **Step 6: Reconcile a GET-only production canary**

Authenticate as the pre-created owner. Read Today, Portfolio, Companion, Alerts, one completed Run,
and System. Compare each visible run/publication/write/send/suppression/policy/data-time claim with
its source database receipt. Attribute activity only to the dashboard role; do not use global table
counts and do not trigger a market run. Verify desktop/mobile Light/Dark/System behavior.

- [ ] **Step 7: Record receipts, update roadmap, run final verification, and commit**

Run: `npm run test:all`

Expected: the full suite exits 0. The rollout document states only evidenced deployment and canary
outcomes, identifies anything awaiting owner visual acceptance, and confirms friend invitations and
brokerage authority remain absent.

```bash
git add docs scripts tests
git commit -m "docs: record owner dashboard v1 rollout"
```
