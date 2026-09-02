import type { PolicyEvaluation } from "./policy.ts";
import { FORBIDDEN_DECISION_TEXT, renderPublication } from "./renderer.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

async function assertRejects(callback: () => Promise<unknown>, message: string): Promise<void> {
  try {
    await callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(message)) return;
    throw new Error(`expected rejection containing ${message}, got ${String(error)}`);
  }
  throw new Error(`expected rejection containing ${message}`);
}

function evaluation(ticker = "CENX", overrides: Partial<PolicyEvaluation> = {}): PolicyEvaluation {
  const candidate = {
    candidate_id: `00000000-0000-4000-8000-${ticker.padEnd(12, "0").slice(0, 12)}`,
    ticker,
    phase: "pre-market" as const,
    action: "buy" as const,
    notification_kind: "brief" as const,
    decision_mode: "discretionary" as const,
    bucket: "growth" as const,
    depth: "full" as const,
    confidence: "medium" as const,
    confidence_reason: "not rendered",
    health_score: "72",
    observed_price: "47.02",
    observed_quote_as_of: "2026-09-02T11:00:00.000Z",
    proposed_amount: "470.20",
    proposed_shares: "10",
    entry_zone_low: "45",
    entry_zone_high: "47.02",
    stop: "42",
    target: "58",
    invalidation_price: "42",
    valid_until: "2026-09-09",
    evidence: [{
      id: "quote-1",
      kind: "quote" as const,
      source: "Yahoo <raw>",
      status: "fresh" as const,
      observed_at: "2026-09-02T11:00:00.000Z",
      retrieved_at: "2026-09-02T11:01:00.000Z",
      reference: "https://evil.invalid/click-me",
      claims: ["not rendered"],
    }],
    factors: [{
      kind: "risk" as const,
      stance: "neutral" as const,
      text: "Margins < peers & improving.",
      evidence_ids: ["quote-1"],
    }],
    analyst: { completed: true, action: "buy" as const, confidence: "medium" as const, reason: "not rendered" },
    checker: { completed: true, verdict: "approve" as const, reason_codes: [], reason: "not rendered" },
    decisive_factor: "not rendered",
    invalidation: "not rendered",
    prior_suggestion_ids: [],
  };
  return {
    evaluation_id: "00000000-0000-4000-8000-000000000099",
    candidate_id: candidate.candidate_id,
    raw_action: "buy",
    final_action: "buy",
    status: "approved",
    reason_codes: [],
    explanations: [],
    normalized: {
      ticker,
      verified_price: "47.02",
      quote_as_of: "2026-09-02T11:00:00.000Z",
      quote_source: "yahoo-chart",
      position_value_after: "470.2",
      total_investable_value: "40500",
      dollars_at_risk: "50.2",
      reward_risk_milli: "2187",
    },
    holding_state_change: null,
    candidate,
    ...overrides,
  };
}

Deno.test("renderer escapes eligible prose, omits URLs and audit-only narrative", async () => {
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-02",
    evaluations: [evaluation()],
  });
  assert(rendered.body.includes("Margins &lt; peers &amp; improving."), "factor was not escaped");
  assert(rendered.body.includes("Yahoo &lt;raw&gt;"), "source was not escaped");
  assert(!rendered.body.includes("evil.invalid"), "caller URL became visible");
  assert(!rendered.body.includes("not rendered"), "audit-only narrative leaked");
  assertEquals(rendered.status, "ready");
  assertEquals(rendered.template_version, 1);
});

Deno.test("renderer is deterministic, sorts names, and hashes the exact body", async () => {
  const input = { phase: "pre-market" as const, market_date: "2026-09-02", evaluations: [evaluation("ZZZ"), evaluation("AAA")] };
  const first = await renderPublication(input);
  const second = await renderPublication(input);
  assertEquals(first, second);
  assert(first.body.indexOf("AAA") < first.body.indexOf("ZZZ"), "tickers were not sorted");
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(first.body))))
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  assertEquals(first.hash, digest);
});

Deno.test("renderer bounds Telegram parts and splits only between candidate blocks", async () => {
  const evaluations = Array.from({ length: 12 }, (_, index) => {
    const ticker = `T${String(index).padStart(2, "0")}`;
    const item = evaluation(ticker);
    item.candidate.factors = Array.from({ length: 3 }, (__, factor) => ({
      kind: "risk" as const,
      stance: "neutral" as const,
      text: `Factor ${factor} ${"x".repeat(400)}`,
      evidence_ids: ["quote-1"],
    }));
    return item;
  });
  const rendered = await renderPublication({ phase: "pre-market", market_date: "2026-09-02", evaluations });
  assert(rendered.parts.length <= 4, "too many Telegram parts");
  assert(rendered.parts.every((part) => part.length <= 3500), "Telegram part too long");
  assert(rendered.parts.every((part) => !part.startsWith("• ")), "split started inside a candidate block");
});

Deno.test("holiday and quiet intraday states are server-owned", async () => {
  const holiday = await renderPublication({ phase: "pre-market", market_date: "2026-09-07", evaluations: [], holiday: true });
  assertEquals(holiday.body, "🏛 Market closed today — US public holiday. No brief.");
  assertEquals(holiday.kind, "holiday");
  assertEquals(holiday.parts, [holiday.body]);

  const quiet = await renderPublication({ phase: "intraday", market_date: "2026-09-02", evaluations: [evaluation()] });
  assertEquals(quiet.status, "suppressed");
  assertEquals(quiet.body, "");
  assertEquals(quiet.parts, []);
});

Deno.test("on-demand output remains session-only and labels conditional closes", async () => {
  const live = await renderPublication({ phase: "on-demand", market_date: "2026-09-02", evaluations: [evaluation()] });
  assertEquals(live.status, "suppressed");
  assertEquals(live.parts, []);
  assert(live.body.includes("LIVE REGULAR SESSION"), "live label absent");

  const conditionalEval = evaluation("VTI", { reason_codes: ["OUTSIDE_SESSION_CONDITIONAL"] });
  const conditional = await renderPublication({ phase: "on-demand", market_date: "2026-09-02", evaluations: [conditionalEval] });
  assert(conditional.body.includes("CONDITIONAL — LATEST OFFICIAL CLOSE"), "conditional label absent");
});

Deno.test("downgraded results omit prose and use server-owned reason labels", async () => {
  const item = evaluation("CENX", {
    status: "downgraded",
    final_action: "watch",
    reason_codes: ["QUOTE_STALE"],
  });
  const rendered = await renderPublication({ phase: "pre-market", market_date: "2026-09-02", evaluations: [item] });
  assert(!rendered.body.includes("Margins"), "downgraded prose leaked");
  assert(rendered.body.includes("Quote is stale"), "server reason label absent");
  assert(rendered.body.includes("risk/neutral"), "factor metadata absent");
});

Deno.test("directive validator rejects trade language, quantities, dollars, and levels", async () => {
  for (const text of ["Buy this now", "10 shares looks right", "$500 allocation", "stop: 42"]) {
    assert(FORBIDDEN_DECISION_TEXT.test(text), `fixture did not match directive regex: ${text}`);
    const item = evaluation();
    item.candidate.factors[0].text = text;
    await assertRejects(
      () => renderPublication({ phase: "pre-market", market_date: "2026-09-02", evaluations: [item] }),
      "forbidden decision directive",
    );
  }
});

Deno.test("intraday uses one publication and fixed trigger priority", async () => {
  const near = evaluation("AAA");
  near.candidate.notification_kind = "stop_near";
  const breach = evaluation("BBB");
  breach.candidate.notification_kind = "stop_breach";
  const rendered = await renderPublication({ phase: "intraday", market_date: "2026-09-02", evaluations: [near, breach] });
  assertEquals(rendered.kind, "stop_breach");
  assertEquals(rendered.parts.length, 1);
  assert(rendered.body.includes("AAA") && rendered.body.includes("BBB"), "lower-priority trigger omitted");
});

