import {
  mapCompanionResponse,
  mapIdea,
  mapPortfolio,
  mapPublicationReceipt,
  mapRun,
} from "./mappers.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

Deno.test("sent requires delivered receipt and Telegram message id", () => {
  const base = {
    id: "alert-1",
    kind: "brief",
    phase: "post-market",
    rendered_body: "safe stored text",
    rendered_hash: "a".repeat(64),
    template_version: 3,
    attempt_count: 1,
    created_at: "2026-09-03T20:01:00.000Z",
    delivered_at: "2026-09-03T20:02:00.000Z",
  };
  assertEquals(mapPublicationReceipt({ ...base, status: "delivered", telegram_message_ids: [] }).state, "incomplete");
  assertEquals(mapPublicationReceipt({ ...base, status: "delivered", telegram_message_ids: [42] }).state, "delivered");
  const suppressed = mapPublicationReceipt({ ...base, status: "suppressed", telegram_message_ids: [42] });
  assertEquals(suppressed.state, "suppressed");
  assertEquals(suppressed.telegram_message_ids, []);
});

Deno.test("companion maps only structured analysis and never presentation text", () => {
  const response = {
    preview: "VXUS is definitely the winner with 99% future returns",
    companion_analysis: {
      qualification_status: "qualified",
      baseline_ticker: "VTI",
      companion_ticker: "VXUS",
      role: "diversifier",
      thesis: "Adds non-U.S. exposure.",
      risk_note: "Currency risk can produce long periods of lagging.",
      recurring_plan_review_eligible: true,
      horizons: [{
        years: 3,
        baseline_annualized_return_pct: "7",
        companion_annualized_return_pct: "5",
        baseline_max_drawdown_pct: "20",
        companion_max_drawdown_pct: "25",
        daily_return_correlation: "0.7",
      }],
      rolling_one_year: {
        total_contributed_usd: "1200",
        weak_ending_value_usd: "980",
        middle_ending_value_usd: "1240",
        strong_ending_value_usd: "1410",
        sample_windows: 50,
      },
    },
  };
  const mapped = mapCompanionResponse(response);
  assertEquals(mapped?.companion_ticker, "VXUS");
  assertEquals(mapped?.plan_unchanged, true);
  assertEquals(JSON.stringify(mapped).includes("99%"), false);
  assertEquals(mapCompanionResponse({ preview: response.preview }), null);
});

Deno.test("idea approval is tied to its persisted evaluation policy version", () => {
  const idea = mapIdea({
    id: "12",
    ticker: "MSFT",
    action: "buy",
    evaluation_id: "eval-1",
    policy_status: "approved",
    policy_version: 17,
    analyst: { verdict: "complete" },
    checker: { verdict: "pass" },
  });
  assertEquals(idea.policy_status, "approved");
  assertEquals(idea.policy_version, 17);
  assertEquals(mapIdea({ id: "13", ticker: "OLD", action: "watch" }).policy_status, "legacy_unverified");
});

Deno.test("portfolio omits derived totals when any required price is missing or stale", () => {
  const holdings = [{ ticker: "VTI", shares: "2", avg_cost: "100", bucket: "core", price: "110", price_as_of: "2026-09-03T18:00:00.000Z", price_freshness: "fresh" }];
  assertEquals(mapPortfolio(holdings, [], []).totals.value, "220");
  assertEquals(mapPortfolio([{ ...holdings[0], price_freshness: "stale" }], [], []).totals.value, null);
  assertEquals(mapPortfolio([{ ...holdings[0], price: null }], [], []).totals.unrealized_amount, null);
});

Deno.test("publication errors are never forwarded as suppression copy", () => {
  const mapped = mapPublicationReceipt({
    id: "alert-1",
    kind: "brief",
    phase: "intraday",
    status: "suppressed",
    rendered_body: "No trigger.",
    rendered_hash: "a".repeat(64),
    template_version: "3",
    telegram_message_ids: [],
    attempt_count: 0,
    created_at: "2026-09-03T18:00:00.000Z",
    event_status: "not_triggered",
    error: "https://api.telegram.org/bot-secret/sendMessage?chat_id=123",
  });
  assertEquals(mapped.suppression_reason, "conditions_not_met");
  assertEquals(JSON.stringify(mapped).includes("bot-secret"), false);
});

Deno.test("suppression copy is not inferred from a triggered event alone", () => {
  const mapped = mapPublicationReceipt({
    id: "alert-2", kind: "alert", phase: "intraday", status: "suppressed",
    rendered_body: "Review only.", rendered_hash: "b".repeat(64), template_version: "3",
    telegram_message_ids: [], attempt_count: 0, created_at: "2026-09-03T18:00:00.000Z",
    event_status: "triggered",
  });
  assertEquals(mapped.suppression_reason, null);
});

Deno.test("unknown run values remain unknown rather than inventing receipt attributes", () => {
  const mapped = mapRun({
    id: "7d834dbd-75bb-4313-931f-09732f003932",
    kind: "future-phase",
    status: "future-status",
    started_at: "2026-09-03T18:00:00.000Z",
  });
  assertEquals(mapped.kind, "unknown");
  assertEquals(mapped.status, "unknown");
});
