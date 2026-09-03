import type { Page, Route } from "@playwright/test";

export const OWNER_A = "11111111-1111-4111-8111-111111111111";
export const OWNER_B = "22222222-2222-4222-8222-222222222222";
const COMMAND_ID = "33333333-3333-4333-8333-333333333333";
const CONNECTION_ID = "44444444-4444-4444-8444-444444444444";
const CONNECTION_PUBLIC_ID = "55555555-5555-4555-8555-555555555555";
const CHALLENGE_ID = "66666666-6666-4666-8666-666666666666";
const RECEIPT_ID = "77777777-7777-4777-8777-777777777777";
const SLOT_ID = "88888888-8888-4888-8888-888888888888";
const DELETION_ID = "99999999-9999-4999-8999-999999999999";
const PROJECT_URL = "https://test-project.supabase.co";

export type MockState = {
  calls: Array<{ path: string; body: Record<string, unknown> }>;
  consented: boolean;
  profileStatus: "invited" | "active" | "deletion_pending";
  signedIn: boolean;
  connectionStatus: "none" | "disabled" | "ready" | "active" | "revoked";
  telegramActive: boolean;
};

type BackendOptions = Partial<Omit<MockState, "calls">>;

function base64url(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function accessToken(ownerId = OWNER_A): string {
  return `${base64url({ alg: "none", typ: "JWT" })}.${base64url({
    aud: "authenticated",
    exp: Math.floor(Date.now() / 1000) + 3600,
    iat: Math.floor(Date.now() / 1000),
    iss: `${PROJECT_URL}/auth/v1`,
    role: "authenticated",
    sub: ownerId,
    email: "owner-a@example.com",
  })}.test-signature`;
}

function user(ownerId = OWNER_A) {
  return {
    id: ownerId,
    aud: "authenticated",
    role: "authenticated",
    email: "owner-a@example.com",
    email_confirmed_at: "2026-09-01T12:00:00Z",
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    identities: [],
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:00:00Z",
  };
}

function session(ownerId = OWNER_A) {
  return {
    access_token: accessToken(ownerId),
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: `refresh-${ownerId}`,
    user: user(ownerId),
  };
}

export async function installStoredSession(page: Page, ownerId = OWNER_A): Promise<void> {
  await page.addInitScript(({ value }) => {
    window.localStorage.setItem("sb-test-project-auth-token", JSON.stringify(value));
  }, { value: session(ownerId) });
}

function json(route: Route, value: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: { "cache-control": "no-store", ...extraHeaders },
    body: JSON.stringify(value),
  });
}

function holdings() {
  return [{
    ticker: "NVDA", shares: "2.00000000", avg_cost: "100.00", bucket: "growth",
    opened_at: "2026-08-01", stop: "90.0000", target: "140.0000",
    high_water_price: "125.0000", hold_override_until: null, projection_sequence: "1",
  }];
}

function quotes() {
  return [{
    ticker: "NVDA", price: "120.0000", previous_close: "118.0000", provider: "test-feed",
    as_of: "2026-09-03T17:00:00Z", retrieved_at: "2026-09-03T17:00:10Z",
    session: "REGULAR", adjustment_status: "adjusted", status: "fresh",
    conflict_basis_points: null, corporate_action_state: "clear", alerts_suppressed: false,
  }];
}

function plans() {
  return [{
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", ticker: "VTI", bucket: "core",
    amount: "300.00", cadence: "monthly", next_due_on: "2026-09-21", due_day: 21,
    active: true, created_at: "2026-08-19T12:00:00Z", updated_at: "2026-08-19T12:00:00Z",
  }];
}

function runTimeline() {
  return [{
    slot_id: SLOT_ID, market_date: "2026-09-03", phase: "intraday", purpose: "scheduled",
    expected_at: "2026-09-03T17:00:00Z", window_ends_at: "2026-09-03T17:20:00Z",
    holiday: false, slot_status: "completed", trigger_status: "triggered",
    trigger_response_status: 200, provider_session_url: "https://claude.ai/code/session_TEST123",
    trigger_started_at: "2026-09-03T17:00:00Z", trigger_finished_at: "2026-09-03T17:00:01Z",
    run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", started_at: "2026-09-03T17:00:02Z",
    finished_at: "2026-09-03T17:02:00Z", run_status: "completed",
    data_as_of: "2026-09-03T17:01:00Z", source_status: { market: "fresh" },
    symbols: ["NVDA"], write_counts: { suggestions: 1 }, summary: "One bounded watch result.",
    provider: "claude", model: "subscription", submission_status: "accepted",
    policy_states: ["approved"], evidence_status: "fresh", publication_kind: "alert",
    publication_status: "delivered", delivered_at: "2026-09-03T17:02:01Z",
    telegram_message_ids: ["101"], error_code: null,
  }];
}

function research() {
  return [{
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    market_date: "2026-09-03", phase: "intraday", run_status: "completed", provider: "claude",
    model: "subscription", created_at: "2026-09-03T17:01:00Z", ticker: "NVDA", action: "Watch",
    raw_action: "Buy", policy_status: "downgraded", policy_reason_codes: ["POSITION_LIMIT"],
    policy_explanations: ["The deterministic position limit prevents a Buy result."],
    analyst: { thesis: "Demand is durable but valuation is elevated." },
    checker: { reason: "Current risk/reward does not clear the gate." }, confidence: "medium",
    verified_price: "120.0000", evidence_as_of: "2026-09-03T17:00:00Z",
    entry_zone_low: "115.0000", entry_zone_high: "118.0000", stop: "105.0000",
    target: "135.0000", invalidation_price: "104.0000", valid_until: "2026-09-03",
    horizon: "swing", bucket: "growth", risk_verdict: "bounded", decisive_factor: "valuation",
    reason: "Wait for a better entry.", bull_case: "Demand persists.", bear_case: "Multiple contracts.",
    evidence_status: "fresh", evidence: [{
      evidence_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", category: "filing", source: "SEC",
      reference: "https://www.sec.gov/", observed_at: "2026-09-03T16:58:00Z",
      retrieved_at: "2026-09-03T16:59:00Z", revalidated_at: "2026-09-03T17:00:00Z",
      claims: ["Current filing checked"], status: "fresh",
    }], publication_kind: "alert", notification_status: "delivered",
    delivered_at: "2026-09-03T17:02:01Z", telegram_message_ids: ["101"],
    delivery_error_code: null, outcomes: [],
  }];
}

function settings() {
  return [{
    display_name: "Owner A", timezone: "America/Chicago", notify_pre_market: true,
    notify_intraday: true, notify_post_market: true, notify_operational: true,
    primary_connection_id: null, schedule_timezone: "America/Chicago",
    schedule_pre_market: true, schedule_intraday: true, schedule_post_market: true,
  }];
}

function connectionRows(state: MockState) {
  return state.connectionStatus === "none" ? [] : [{
    id: CONNECTION_ID, public_id: CONNECTION_PUBLIC_ID, provider: "claude",
    credential_type: "claude_routine_v1", capabilities: { research: true }, contract_version: 2,
    status: state.connectionStatus, last_handshake_at: state.connectionStatus === "disabled"
      ? null : "2026-09-03T17:00:00Z", created_at: "2026-09-03T16:00:00Z",
    updated_at: "2026-09-03T17:00:00Z",
  }];
}

function restRows(table: string, state: MockState): unknown {
  if (table === "profile") {
    return { display_name: "Owner A", status: state.profileStatus };
  }
  if (table === "consents") {
    return state.consented
      ? [{ document_version: "provider-data-v1", accepted_at: "2026-09-03T16:00:00Z" }]
      : [];
  }
  if (table === "holdings") return holdings();
  if (table === "market_quotes") return quotes();
  if (table === "plans") return plans();
  if (table === "today") return [{
    run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", kind: "intraday",
    started_at: "2026-09-03T17:00:02Z", finished_at: "2026-09-03T17:02:00Z",
    status: "completed", data_as_of: "2026-09-03T17:01:00Z", source_status: { market: "fresh" },
    symbols: ["NVDA"], write_counts: { suggestions: 1 }, summary: "One bounded watch result.",
  }];
  if (table === "recommendations") return [{
    id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ticker: "NVDA", action: "Watch", confidence: "medium", valid_until: "2026-09-03",
    evidence_as_of: "2026-09-03T17:00:00Z",
  }];
  if (table === "transactions") return [{
    id: "ffffffff-ffff-4fff-8fff-ffffffffffff", created_at: "2026-08-01T12:00:00Z",
    ticker: "NVDA", event_type: "trade", side: "buy", qty: "2.00000000", price: "100.0000",
    fees: "0.00", executed_on: "2026-08-01", ledger_sequence: "1", bucket: "growth",
    source_channel: "web", corrects_transaction_id: null,
  }];
  if (table === "commands") return [];
  if (table === "research") return research();
  if (table === "run_timeline") return runTimeline();
  if (table === "connections") return connectionRows(state);
  if (table === "telegram_status") return state.telegramActive
    ? [{ status: "active", linked_at: "2026-09-03T16:00:00Z", revoked_at: null }]
    : [];
  if (table === "settings") return settings();
  return [];
}

function apiData(path: string, body: Record<string, unknown>, state: MockState): unknown {
  state.calls.push({ path, body });
  if (Object.hasOwn(body, "owner_id")) throw new Error("owner authority reached mock API");
  if (path === "/consents/accept") {
    state.consented = true;
    state.profileStatus = "active";
    return { status: "accepted", document_version: "provider-data-v1" };
  }
  if (["/portfolio/preview", "/portfolio/correction/preview", "/plans/preview"].includes(path)) {
    const command = body.command as Record<string, unknown>;
    return {
      command_id: COMMAND_ID, status: "previewed", preview_digest: "a".repeat(64),
      expires_at: "2027-09-03T18:00:00Z", operation: command.operation,
      before: { shares: "0.00000000", avg_cost: "0.00" },
      after: {
        shares: command.quantity ?? "0.00000000", avg_cost: command.fill_price ?? "0.00",
        fill_price: command.fill_price ?? "0.00", fees: command.fees ?? "0.00",
        cash_total: command.cash_total ?? null, expected_cash_total: command.cash_total ?? "0.00",
        executed_on: command.executed_on ?? "2026-09-03", bucket: command.bucket ?? "unclassified",
        cash_reconciled: command.cash_total !== null,
      }, warnings: ["Recordkeeping only; no brokerage order is placed."],
    };
  }
  if (["/portfolio/confirm", "/portfolio/correction/confirm", "/plans/confirm"].includes(path)) {
    return { command_id: COMMAND_ID, status: "applied", result: { ledger_sequence: "2" } };
  }
  if (path === "/runs/on-demand") return {
    status: "queued", slot_id: SLOT_ID, phase: "on-demand", market_date: "2026-09-03",
    expected_by: "2026-09-03T18:10:00Z", telegram: "suppressed",
  };
  if (path === "/connections/create") {
    state.connectionStatus = "disabled";
    return {
      connection_id: CONNECTION_ID, public_id: CONNECTION_PUBLIC_ID, provider: "claude",
      status: "disabled", contract_version: 2,
      gateway_credential: `${CONNECTION_PUBLIC_ID}.${"x".repeat(43)}`, credential_display: "once",
    };
  }
  if (path === "/connections/handshake") {
    state.connectionStatus = "ready";
    return {
      connection_id: CONNECTION_ID, status: "testing", handshake_id: SLOT_ID,
      trigger_request_id: "abababab-abab-4bab-8bab-abababababab", duplicate: false,
    };
  }
  if (path === "/connections/activate") {
    state.connectionStatus = "active";
    return { connection_id: CONNECTION_ID, status: "active" };
  }
  if (path === "/connections/revoke") {
    state.connectionStatus = "revoked";
    return { connection_id: CONNECTION_ID, status: "revoked" };
  }
  if (path === "/telegram/pairing-code") return {
    pairing_id: SLOT_ID, status: "issued", code: "ABCD234567",
    expires_at: "2027-09-03T18:00:00Z",
  };
  if (path === "/telegram/unlink") {
    state.telegramActive = false;
    return { status: "unlinked" };
  }
  if (path === "/settings") return { status: "updated" };
  if (path === "/account/step-up/challenge") return {
    status: "challenge_created", challenge_id: CHALLENGE_ID,
    expires_at: "2027-09-03T18:00:00Z",
  };
  if (path === "/account/step-up/complete") return {
    status: "verified", step_up_receipt_id: RECEIPT_ID, expires_at: "2027-09-03T18:00:00Z",
  };
  if (path === "/account/delete/request") return {
    deletion_request_id: DELETION_ID, status: "confirmation_pending",
    confirmation_phrase: "DELETE MY ACCOUNT", confirmation_expires_at: "2027-09-03T18:00:00Z",
  };
  if (path === "/account/delete/confirm") {
    state.profileStatus = "deletion_pending";
    return {
      deletion_request_id: DELETION_ID, status: "pending",
      cancel_until: "2027-09-06T18:00:00Z", delete_by: "2027-09-10T18:00:00Z",
    };
  }
  if (path === "/account/delete/cancel") {
    state.profileStatus = "active";
    return { deletion_request_id: DELETION_ID, status: "cancelled" };
  }
  if (path === "/account/status") return {
    account_status: state.profileStatus, deletion_status: state.profileStatus === "deletion_pending"
      ? "pending" : null, requested_at: state.profileStatus === "deletion_pending"
      ? "2026-09-03T17:00:00Z" : null, cancel_until: state.profileStatus === "deletion_pending"
      ? "2027-09-06T18:00:00Z" : null, delete_by: state.profileStatus === "deletion_pending"
      ? "2027-09-10T18:00:00Z" : null,
    telegram_cleanup_status: { older_history_requires_manual_removal: true },
  };
  throw new Error(`unhandled app API path: ${path}`);
}

export async function installMockBackend(page: Page, options: BackendOptions = {}): Promise<MockState> {
  const state: MockState = {
    calls: [], consented: true, profileStatus: "active", signedIn: true,
    connectionStatus: "none", telegramActive: false, ...options,
  };
  await page.route(`${PROJECT_URL}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/auth/v1/otp") return json(route, {});
    if (url.pathname === "/auth/v1/verify" || url.pathname === "/auth/v1/token") {
      state.signedIn = true;
      return json(route, session());
    }
    if (url.pathname === "/auth/v1/user") {
      return state.signedIn ? json(route, user()) : json(route, { message: "unauthorized" }, 401);
    }
    if (url.pathname === "/auth/v1/logout") {
      state.signedIn = false;
      return route.fulfill({ status: 204, headers: { "cache-control": "no-store" }, body: "" });
    }
    if (url.pathname.startsWith("/rest/v1/")) {
      const table = url.pathname.split("/").at(-1) ?? "";
      return json(route, restRows(table, state), 200, { "content-range": "0-0/*" });
    }
    if (url.pathname.startsWith("/functions/v1/app-api")) {
      if (!request.headers().authorization?.startsWith("Bearer ")) {
        return json(route, { ok: false, error: { code: "UNAUTHORIZED" } }, 401);
      }
      const path = url.pathname.slice("/functions/v1/app-api".length);
      if (path === "/export/ledger.csv") {
        return route.fulfill({
          status: 200,
          headers: {
            "cache-control": "no-store", "content-type": "text/csv; charset=utf-8",
            "content-disposition": 'attachment; filename="stock-agent-ledger.csv"',
            "access-control-expose-headers": "Content-Disposition",
          },
          body: "ticker,side,qty\nNVDA,buy,2.00000000\n",
        });
      }
      if (path === "/export/account.json") {
        return route.fulfill({
          status: 200,
          headers: {
            "cache-control": "no-store", "content-type": "application/json; charset=utf-8",
            "content-disposition": 'attachment; filename="stock-agent-account.json"',
            "access-control-expose-headers": "Content-Disposition",
          },
          body: JSON.stringify({ version: 1 }),
        });
      }
      const body = request.postDataJSON() as Record<string, unknown> | null;
      return json(route, { ok: true, data: apiData(path, body ?? {}, state) });
    }
    return json(route, { message: "not found" }, 404);
  });
  return state;
}
