import type {
  AlertEvaluation,
  AlertRuleSnapshot,
  PolicyContext,
} from "./contracts.ts";
import type { PolicyEvaluation } from "./policy.ts";
import type { LongTermCompanionAnalysis } from "./companion.ts";
import {
  FORBIDDEN_DECISION_TEXT,
  renderAlertV3,
  renderPublication,
} from "./renderer.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

async function assertRejects(
  callback: () => Promise<unknown>,
  message: string,
): Promise<void> {
  try {
    await callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(message)) return;
    throw new Error(
      `expected rejection containing ${message}, got ${String(error)}`,
    );
  }
  throw new Error(`expected rejection containing ${message}`);
}

function evaluation(
  ticker = "PLTR",
  overrides: Partial<PolicyEvaluation> = {},
): PolicyEvaluation {
  const candidate = {
    candidate_id: `00000000-0000-4000-8000-${
      ticker.padEnd(12, "0").slice(0, 12)
    }`,
    ticker,
    phase: "pre-market" as const,
    action: "buy" as const,
    notification_kind: "brief" as const,
    decision_mode: "discretionary" as const,
    bucket: "growth" as const,
    depth: "full" as const,
    confidence: "medium" as const,
    confidence_reason: "audit-only confidence detail",
    health_score: "72",
    observed_price: "47.02",
    observed_quote_as_of: "2026-09-03T11:00:00.000Z",
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
      observed_at: "2026-09-03T11:00:00.000Z",
      retrieved_at: "2026-09-03T11:01:00.000Z",
      reference: "https://evil.invalid/click-me",
      claims: ["audit-only evidence claim"],
    }, {
      id: "filing-1",
      kind: "fundamentals" as const,
      source: "sec-edgar",
      status: "fresh" as const,
      observed_at: "2026-08-31T12:00:00.000Z",
      retrieved_at: "2026-09-03T10:30:00.000Z",
      reference: "https://www.sec.gov/Archives/edgar/data/1/report.htm?x=1&y=2",
      claims: ["audit-only filing claim"],
    }],
    factors: [{
      kind: "macro" as const,
      stance: "neutral" as const,
      text: "Index futures are mixed & Treasury yields are steady.",
      evidence_ids: ["quote-1"],
    }, {
      kind: "fundamentals" as const,
      stance: "bull" as const,
      text: "Margins < peers but are improving.",
      evidence_ids: ["filing-1"],
    }],
    analyst: {
      completed: true,
      action: "buy" as const,
      confidence: "medium" as const,
      reason: "audit-only analyst detail",
    },
    checker: {
      completed: true,
      verdict: "approve" as const,
      reason_codes: [],
      reason: "audit-only checker detail",
    },
    decisive_factor: "audit-only decisive factor",
    invalidation: "audit-only invalidation prose",
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
      quote_as_of: "2026-09-03T11:00:00.000Z",
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

function context(overrides: Partial<PolicyContext> = {}): PolicyContext {
  return {
    holdings: [{
      ticker: "CENX",
      shares: "43.748192",
      avg_cost: "47.02",
      bucket: "speculative",
      stop: "45.58",
      target: null,
      high_water_price: "49",
      hold_override_until: null,
      stop_alert_active: false,
      stop_near_alert_active: true,
      target_near_alert_active: false,
      target_alert_active: false,
    }, {
      ticker: "VTI",
      shares: "0.789142",
      avg_cost: "380.16",
      bucket: "core",
      stop: "375.17",
      target: null,
      high_water_price: "382",
      hold_override_until: null,
      stop_alert_active: false,
      stop_near_alert_active: false,
      target_near_alert_active: false,
      target_alert_active: false,
    }],
    holding_quotes: {
      CENX: {
        ticker: "CENX",
        price: "49",
        previous_close: "48.5",
        as_of: "2026-09-03T11:02:00.000Z",
        market_state: "PRE",
        source: "yahoo-chart",
      },
      VTI: {
        ticker: "VTI",
        price: "382",
        previous_close: "381",
        as_of: "2026-09-03T11:01:00.000Z",
        market_state: "PRE",
        source: "yahoo-chart",
      },
    },
    realized_pnl_today: null,
    portfolio_command_coverage_complete: true,
    consecutive_completed_losses: 0,
    owner_plans: [],
    ...overrides,
  };
}

Deno.test("morning brief renders the approved compact section order from verified context", async () => {
  const watch = evaluation("CRWD", {
    status: "downgraded",
    final_action: "watch",
    reason_codes: ["LOW_CONFIDENCE"],
  });
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [watch, evaluation("PLTR")],
    context: context(),
  });

  const sections = [
    "📊 YOUR PORTFOLIO",
    "🌎 MARKET",
    "🎯 OPEN ENTRY ZONES",
    "⚠️ PORTFOLIO RISKS",
    "👀 WATCHING",
    "📚 READ MORE",
  ];
  for (let index = 1; index < sections.length; index += 1) {
    assert(
      rendered.body.indexOf(sections[index - 1]) <
        rendered.body.indexOf(sections[index]),
      `${sections[index - 1]} did not precede ${sections[index]}`,
    );
  }
  assert(
    rendered.body.startsWith("<b>🌅 MORNING BRIEF — 2026-09-03</b>"),
    "morning heading absent",
  );
  assert(
    rendered.body.includes("Data through 2026-09-03T11:02:00.000Z"),
    "latest data timestamp absent",
  );
  assert(
    rendered.body.includes(
      "CENX · $49.00 · +4.2% since avg $47.02 · Stop $45.58 (7.0% below)",
    ),
    "portfolio performance or stop distance absent",
  );
  assert(
    rendered.body.includes(
      "PLTR · $45.00–$47.02 · Stop $42.00 · Target $58.00 · Valid through 2026-09-09 · MEDIUM",
    ),
    "complete approved entry zone absent",
  );
  assert(
    rendered.body.includes(
      "Index futures are mixed &amp; Treasury yields are steady.",
    ),
    "market prose was not escaped",
  );
  assert(
    rendered.body.includes(
      "CRWD · WATCH · Confidence is below the action threshold",
    ),
    "compact watch row absent",
  );
  assert(
    rendered.body.includes("CENX: verified price is near its recorded stop."),
    "material portfolio risk absent",
  );
  assert(
    rendered.body.includes("Yahoo Finance"),
    "canonical market source absent",
  );
  assert(
    rendered.body.includes(
      "https://www.sec.gov/Archives/edgar/data/1/report.htm?x=1&amp;y=2",
    ),
    "approved source link absent",
  );
  for (
    const internal of [
      "quote-1",
      "filing-1",
      "Yahoo <raw>",
      "audit-only",
      "checker detail",
      "evil.invalid",
    ]
  ) {
    assert(
      !rendered.body.includes(internal),
      `internal detail leaked: ${internal}`,
    );
  }
  assertEquals(rendered.status, "ready");
  assertEquals(rendered.parts.length, 1);
  assertEquals(rendered.template_version, 2);
});

Deno.test("brief explicitly says when no policy-approved entry zone exists", async () => {
  const item = evaluation("CRWD", {
    status: "downgraded",
    final_action: "watch",
    reason_codes: ["QUOTE_STALE"],
  });
  item.candidate.factors[1].text =
    "This private model explanation must not appear.";
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [item],
    context: context(),
  });
  assert(
    rendered.body.includes("No active policy-approved entry zones today."),
    "empty-zone state absent",
  );
  assert(
    rendered.body.includes("CRWD · WATCH · Quote is stale"),
    "server-owned downgrade reason absent",
  );
  assert(
    !rendered.body.includes("private model explanation"),
    "downgraded prose leaked",
  );
});

Deno.test("renderer is deterministic, sorts names, and hashes the exact body", async () => {
  const input = {
    phase: "pre-market" as const,
    market_date: "2026-09-03",
    evaluations: [evaluation("ZZZ"), evaluation("AAA")],
    context: context(),
  };
  const first = await renderPublication(input);
  const second = await renderPublication(input);
  assertEquals(first, second);
  assert(
    first.body.indexOf("AAA · $45.00") < first.body.indexOf("ZZZ · $45.00"),
    "entry zones were not sorted",
  );
  const digest = Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(first.body),
      ),
    ),
  ).map((byte) => byte.toString(16).padStart(2, "0")).join("");
  assertEquals(first.hash, digest);
});

Deno.test("renderer bounds Telegram parts without splitting a portfolio row", async () => {
  const holdings = Array.from({ length: 60 }, (_, index) => ({
    ticker: `T${String(index).padStart(2, "0")}`,
    shares: "1",
    avg_cost: "100",
    bucket: "growth" as const,
    stop: "90",
    target: null,
    high_water_price: "105",
    hold_override_until: null,
    stop_alert_active: false,
    stop_near_alert_active: false,
    target_near_alert_active: false,
    target_alert_active: false,
  }));
  const holdingQuotes = Object.fromEntries(
    holdings.map((holding) => [holding.ticker, {
      ticker: holding.ticker,
      price: "105",
      previous_close: "104",
      as_of: "2026-09-03T11:00:00.000Z",
      market_state: "PRE",
      source: "yahoo-chart" as const,
    }]),
  );
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [],
    context: context({ holdings, holding_quotes: holdingQuotes }),
  });
  assert(rendered.parts.length <= 4, "too many Telegram parts");
  assert(
    rendered.parts.every((part) => part.length <= 3500),
    "Telegram part too long",
  );
  assert(
    rendered.body.includes("Portfolio list limited"),
    "bounded-list disclosure absent",
  );
  assert(
    rendered.parts.every((part) => !part.startsWith("T")),
    "split started inside a portfolio row",
  );
});

Deno.test("holiday and quiet intraday states are server-owned", async () => {
  const holiday = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-07",
    evaluations: [],
    holiday: true,
  });
  assertEquals(
    holiday.body,
    "🏛 Market closed today — US public holiday. No brief.",
  );
  assertEquals(holiday.kind, "holiday");
  assertEquals(holiday.parts, [holiday.body]);
  assertEquals(holiday.template_version, 2);

  const quiet = await renderPublication({
    phase: "intraday",
    market_date: "2026-09-03",
    evaluations: [evaluation()],
    context: context(),
  });
  assertEquals(quiet.status, "suppressed");
  assertEquals(quiet.body, "");
  assertEquals(quiet.parts, []);
});

Deno.test("on-demand output remains session-only and labels conditional closes", async () => {
  const live = await renderPublication({
    phase: "on-demand",
    market_date: "2026-09-03",
    evaluations: [evaluation()],
    context: context(),
  });
  assertEquals(live.status, "suppressed");
  assertEquals(live.parts, []);
  assert(live.body.includes("LIVE REGULAR SESSION"), "live label absent");

  const conditionalEval = evaluation("VTI", {
    reason_codes: ["OUTSIDE_SESSION_CONDITIONAL"],
  });
  const conditional = await renderPublication({
    phase: "on-demand",
    market_date: "2026-09-03",
    evaluations: [conditionalEval],
    context: context(),
  });
  assert(
    conditional.body.includes("CONDITIONAL — LATEST OFFICIAL CLOSE"),
    "conditional label absent",
  );
});

Deno.test("post-market uses the compact closing brief instead of audit blocks", async () => {
  const item = evaluation("PLTR");
  item.candidate.phase = "post-market";
  const rendered = await renderPublication({
    phase: "post-market",
    market_date: "2026-09-03",
    evaluations: [item],
    context: context(),
  });
  assert(
    rendered.body.startsWith("<b>🌙 EOD — Sep 03</b>"),
    "closing heading absent",
  );
  assert(
    rendered.body.includes("📊 YOUR PORTFOLIO"),
    "grouped portfolio section absent",
  );
  assert(
    !rendered.body.includes("Status: APPROVED"),
    "legacy audit block leaked",
  );
});

Deno.test("post-market restores the owner-approved visual portfolio hierarchy", async () => {
  const cenx = evaluation("CENX", { raw_action: "hold", final_action: "hold" });
  cenx.candidate.action = "hold";
  cenx.candidate.phase = "post-market";
  const vti = evaluation("VTI", { raw_action: "hold", final_action: "hold" });
  vti.candidate.action = "hold";
  vti.candidate.phase = "post-market";
  vti.candidate.factors[0].text =
    "Broad tape was mixed: SPY +0.3%, QQQ -0.5%, and IWM -1.5%.";
  vti.candidate.evidence.push({
    id: "news-gap",
    kind: "news",
    source: "finnhub-company-news",
    status: "missing",
    observed_at: null,
    retrieved_at: "2026-09-03T20:15:10.000Z",
    reference: null,
    claims: ["No company-news response was available."],
  });
  const closingContext = context({
    holdings: context().holdings.map((holding) =>
      holding.ticker === "VTI"
        ? { ...holding, stop_near_alert_active: true }
        : { ...holding, stop_near_alert_active: false }
    ),
    holding_quotes: {
      CENX: {
        ticker: "CENX",
        price: "47.51",
        previous_close: "45.89",
        as_of: "2026-09-03T20:00:01.000Z",
        market_state: "POST",
        source: "yahoo-chart",
      },
      VTI: {
        ticker: "VTI",
        price: "376.94",
        previous_close: "380.63",
        as_of: "2026-09-03T20:00:00.000Z",
        market_state: "POST",
        source: "yahoo-chart",
      },
    },
  });
  const rendered = await renderPublication({
    phase: "post-market",
    market_date: "2026-09-03",
    evaluations: [cenx, vti],
    context: closingContext,
  });

  for (
    const expected of [
      "<b>🟢 CENX</b> $47.51 · avg $47.02 · 43.7482 shares",
      "📈 +$21.44 (+1.0%) · invested $2057.04 → now $2078.48",
      "<b>Stop $45.58</b> · $1.93 gap (4.1%)",
      "<b>🔴 VTI</b> $376.94 · avg $380.16 · 0.7891 shares",
      "📉 -$2.54 (-0.8%) · invested $300.00 → now $297.46",
      "⚠️ <b>Stop $375.17</b> · $1.77 gap (0.5%) — watch open",
      "<b>➡️ NEXT REVIEW</b>",
      "VTI: recorded stop $375.17 remains flagged for follow-up.",
      "Data gap: VTI · news evidence unavailable from Finnhub.",
    ]
  ) {
    assert(
      rendered.body.includes(expected),
      `owner-style field absent: ${expected}`,
    );
  }
  assert(
    !rendered.body.includes("Stock Agent app"),
    "unsupported app availability claim remained",
  );
  assert(
    !rendered.body.includes("👀 WATCHING"),
    "empty watching section added noise to the close",
  );
});

Deno.test("post-market supports the policy contract's eight-decimal holdings", async () => {
  const rendered = await renderPublication({
    phase: "post-market",
    market_date: "2026-09-03",
    evaluations: [],
    context: context({
      holdings: [{
        ...context().holdings[1],
        shares: "0.12345678",
        avg_cost: "100",
        stop: "90",
      }],
      holding_quotes: {
        VTI: {
          ticker: "VTI",
          price: "110",
          previous_close: "109",
          as_of: "2026-09-03T20:00:00.000Z",
          market_state: "POST",
          source: "yahoo-chart",
        },
      },
    }),
  });
  assert(
    rendered.body.includes("0.1235 shares"),
    "eight-decimal shares were not rounded safely for display",
  );
  assert(
    rendered.body.includes("📈 +$1.23 (+10.0%) · invested $12.35 → now $13.58"),
    "eight-decimal holding valuation was not computed safely",
  );
});

Deno.test("post-market keeps one complete market takeaway instead of duplicate truncation", async () => {
  const a = evaluation("AAA");
  a.candidate.phase = "post-market";
  a.candidate.factors[0].text =
    "Broad tape was mixed and narrow at the close; large caps held up while small caps lagged.";
  const b = evaluation("BBB");
  b.candidate.phase = "post-market";
  b.candidate.factors[0].text =
    "Broad tape was mixed and narrow at the close; this second redundant paragraph must not appear.";
  const rendered = await renderPublication({
    phase: "post-market",
    market_date: "2026-09-03",
    evaluations: [a, b],
    context: context(),
  });
  assert(
    rendered.body.includes(
      "Broad tape was mixed and narrow at the close; large caps held up while small caps lagged.",
    ),
    "complete market takeaway absent",
  );
  assert(
    !rendered.body.includes("second redundant paragraph"),
    "redundant market paragraph remained",
  );
  assert(!rendered.body.includes("…"), "market takeaway was visibly truncated");
});

Deno.test("pre-market comparison separates matched history from the forward evidence view", async () => {
  const vti = evaluation("VTI", { raw_action: "hold", final_action: "hold" });
  vti.candidate.action = "hold";
  const itot = evaluation("ITOT", {
    raw_action: "watch",
    final_action: "watch",
  });
  itot.candidate.action = "watch";
  itot.candidate.evidence.push({
    id: "itot-profile",
    kind: "fundamentals",
    source: "ishares-fund-profile",
    status: "fresh",
    observed_at: "2026-09-03T10:00:00.000Z",
    retrieved_at: "2026-09-03T10:01:00.000Z",
    reference:
      "https://www.ishares.com/us/products/239724/ishares-core-sp-total-us-stock-market-etf",
    claims: ["Broad U.S. stock-market exposure."],
  });
  itot.candidate.factors.push({
    kind: "fundamentals",
    stance: "neutral",
    text: "ITOT covers the broad U.S. stock market.",
    evidence_ids: ["itot-profile"],
  });
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [vti, itot],
    context: context({
      owner_plans: [{
        id: "00000000-0000-4000-8000-000000000088",
        ticker: "VTI",
        bucket: "core",
        amount: "300",
        cadence: "monthly",
        next_due_on: "2026-09-21",
        active: true,
        updated_at: "2026-09-03T12:00:00.000Z",
      }],
    }),
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "ITOT",
      relationship: "like_for_like",
      prospective_view: "similar",
      reason:
        "Both cover the broad U.S. equity market; current evidence shows no durable forward edge.",
      evidence_ids: ["itot-profile"],
      coverage_status: "complete",
      period_start: "2025-09-03",
      period_end: "2026-09-02",
      common_sessions: 252,
      contribution_count: 12,
      baseline_lump_sum_return_pct: "12.5",
      alternative_lump_sum_return_pct: "12.7",
      lump_sum_excess_pct: "0.2",
      baseline_monthly_return_pct: "6.1",
      alternative_monthly_return_pct: "6.2",
      monthly_excess_pct: "0.1",
      baseline_max_drawdown_pct: "14.2",
      alternative_max_drawdown_pct: "14.1",
    }],
  });
  for (
    const expected of [
      "<b>🔄 CURRENT VS ALTERNATIVES</b>",
      "<b>VTI ↔ ITOT · LIKE-FOR-LIKE</b>",
      "Past year, equal monthly contributions: VTI +6.1% · ITOT +6.2% · ITOT ahead by 0.1 points.",
      "Max drawdown: VTI 14.2% · ITOT 14.1%.",
      "Forward evidence: SIMILAR — Both cover the broad U.S. equity market; current evidence shows no durable forward edge.",
      "Your recorded VTI monthly plan is unchanged.",
      "Hypothetical history is not a forecast.",
      "<b>🧠 LONG-TERM COMPANION</b>",
      "No additive companion cleared current evidence. Your recorded plan is unchanged.",
      '<a href="https://www.ishares.com/us/products/239724/ishares-core-sp-total-us-stock-market-etf">iShares fund profile</a>',
    ]
  ) {
    assert(
      rendered.body.includes(expected),
      `comparison field absent: ${expected}`,
    );
  }
});

function companionAnalysis(
  overrides: Partial<LongTermCompanionAnalysis> = {},
): LongTermCompanionAnalysis {
  return {
    baseline_ticker: "VTI",
    companion_ticker: "VXUS",
    role: "diversifier",
    thesis: "Non-U.S. exposure adds a distinct geographic role.",
    risk_note:
      "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
    evidence_ids: ["vxus-profile"],
    qualification_status: "qualified",
    qualification_reason:
      "Gateway role policy accepted the candidate for long-term research.",
    recurring_plan_review_eligible: true,
    horizons: [3, 5, 10].map((years) => ({
      years: years as 3 | 5 | 10,
      period_start: `${2026 - years}-09-01`,
      period_end: "2026-09-01",
      common_sessions: years * 252,
      baseline_annualized_return_pct: "8",
      companion_annualized_return_pct: "6",
      baseline_max_drawdown_pct: "20",
      companion_max_drawdown_pct: "24",
      daily_return_correlation: "0.72",
    })),
    rolling_one_year: {
      monthly_contribution_usd: "100",
      total_contributed_usd: "1200",
      sample_windows: 109,
      weak_ending_value_usd: "980",
      middle_ending_value_usd: "1260",
      strong_ending_value_usd: "1490",
    },
    ...overrides,
  };
}

Deno.test("long-term companion renders the core, role, long horizons, scenario, and unchanged plan", async () => {
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [evaluation("VTI"), evaluation("VXUS")],
    context: context({
      owner_plans: [{
        id: "00000000-0000-4000-8000-000000000088",
        ticker: "VTI",
        bucket: "core",
        amount: "300",
        cadence: "monthly",
        next_due_on: "2026-09-21",
        active: true,
        updated_at: "2026-09-03T12:00:00.000Z",
      }],
    }),
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "VXUS",
      relationship: "diversifier",
      prospective_view: "similar",
      reason: "The candidate adds a distinct geographic role.",
      evidence_ids: ["vxus-profile"],
      coverage_status: "complete",
      period_start: "2025-09-03",
      period_end: "2026-09-02",
      common_sessions: 252,
      contribution_count: 12,
      baseline_lump_sum_return_pct: "10",
      alternative_lump_sum_return_pct: "9",
      lump_sum_excess_pct: "-1",
      baseline_monthly_return_pct: "5",
      alternative_monthly_return_pct: "4",
      monthly_excess_pct: "-1",
      baseline_max_drawdown_pct: "12",
      alternative_max_drawdown_pct: "14",
    }],
    companion: companionAnalysis(),
  });
  for (
    const expected of [
      "<b>🧠 LONG-TERM COMPANION</b>",
      "Core stays: <b>VTI</b> · $300.00/month reminder",
      "Research candidate: <b>VXUS</b> · DIVERSIFIER",
      "Why it adds something: Non-U.S. exposure adds a distinct geographic role.",
      "Main risk: Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
      "3Y annualized: VTI +8.0% · VXUS +6.0% · corr 0.72 · drawdown 20.0% / 24.0%.",
      "5Y annualized: VTI +8.0% · VXUS +6.0% · corr 0.72 · drawdown 20.0% / 24.0%.",
      "10Y annualized: VTI +8.0% · VXUS +6.0% · corr 0.72 · drawdown 20.0% / 24.0%.",
      "Per $100.00/month, rolling 1Y history: $1200.00 contributed → weak $980.00 · middle $1260.00 · strong $1490.00 (109 windows).",
      "Plan status: eligible for owner review; no reminder was added or changed.",
      "Historical scenarios are not forecasts. Future loss is possible.",
    ]
  ) {
    assert(
      rendered.body.includes(expected),
      `companion field absent: ${expected}`,
    );
  }
});

Deno.test("satellite companion remains research-only and unsafe proposal prose is rejected", async () => {
  const comparison = {
    baseline_ticker: "VTI",
    alternative_ticker: "MSFT",
    relationship: "satellite" as const,
    prospective_view: "similar" as const,
    reason: "This is a concentrated company-specific research sleeve.",
    evidence_ids: ["msft-filing"],
    coverage_status: "complete" as const,
    period_start: "2025-09-03",
    period_end: "2026-09-02",
    common_sessions: 252,
    contribution_count: 12,
    baseline_lump_sum_return_pct: "10",
    alternative_lump_sum_return_pct: "12",
    lump_sum_excess_pct: "2",
    baseline_monthly_return_pct: "5",
    alternative_monthly_return_pct: "6",
    monthly_excess_pct: "1",
    baseline_max_drawdown_pct: "12",
    alternative_max_drawdown_pct: "18",
  };
  const base = {
    phase: "pre-market" as const,
    market_date: "2026-09-03",
    evaluations: [evaluation("VTI"), evaluation("MSFT")],
    context: context(),
    comparisons: [comparison],
  };
  const satellite = companionAnalysis({
    companion_ticker: "MSFT",
    role: "satellite",
    recurring_plan_review_eligible: false,
  });
  const rendered = await renderPublication({ ...base, companion: satellite });
  assert(
    rendered.body.includes(
      "Plan status: research-only; not eligible for a recurring core reminder.",
    ),
    "satellite recurring-plan restriction absent",
  );

  for (
    const unsafe of [
      "Allocate more money here.",
      "This will outperform VTI.",
      "This is certain to outperform VTI.",
      "Profits are assured.",
      "Risk-free returns are expected.",
      "Returns are assured.",
      "This cannot lose money.",
      "It is certain to beat VTI.",
      "Gains are expected next year.",
      "Guaranteed profit next year.",
      "$500 per month is appropriate.",
      "Buy 10 shares.",
    ]
  ) {
    await assertRejects(
      () =>
        renderPublication({
          ...base,
          companion: companionAnalysis({ thesis: unsafe }),
        }),
      "forbidden decision directive",
    );
  }
});

Deno.test("an insufficient companion never presents the nominated role as qualified", async () => {
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [evaluation("VTI"), evaluation("VT")],
    context: context(),
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "VT",
      relationship: "diversifier",
      prospective_view: "similar",
      reason: "The candidate includes international holdings.",
      evidence_ids: ["vt-profile"],
      coverage_status: "complete",
      period_start: "2025-09-03",
      period_end: "2026-09-02",
      common_sessions: 252,
      contribution_count: 12,
      baseline_lump_sum_return_pct: "10",
      alternative_lump_sum_return_pct: "9",
      lump_sum_excess_pct: "-1",
      baseline_monthly_return_pct: "5",
      alternative_monthly_return_pct: "4",
      monthly_excess_pct: "-1",
      baseline_max_drawdown_pct: "12",
      alternative_max_drawdown_pct: "14",
    }],
    companion: companionAnalysis({
      companion_ticker: "VT",
      qualification_status: "insufficient",
      qualification_reason:
        "VT is a global-core replacement with overlapping U.S. exposure, not a companion beside VTI.",
      recurring_plan_review_eligible: false,
      horizons: [],
      rolling_one_year: null,
    }),
  });

  assert(
    rendered.body.includes(
      "Research candidate: <b>VT</b> · REVIEW NOT QUALIFIED",
    ),
    "rejected proposal should be visibly unqualified",
  );
  assert(
    !rendered.body.includes("Research candidate: <b>VT</b> · DIVERSIFIER"),
    "caller-supplied role leaked as a qualified label",
  );
});

Deno.test("comparison rejects plan-switch prose and does not invent a winner on a tie", async () => {
  const tied = {
    baseline_ticker: "VTI",
    alternative_ticker: "ITOT",
    relationship: "like_for_like" as const,
    prospective_view: "similar" as const,
    reason: "Both cover the same broad role.",
    evidence_ids: ["e"],
    coverage_status: "complete" as const,
    period_start: "2025-09-03",
    period_end: "2026-09-02",
    common_sessions: 252,
    contribution_count: 12,
    baseline_lump_sum_return_pct: "10",
    alternative_lump_sum_return_pct: "10",
    lump_sum_excess_pct: "0",
    baseline_monthly_return_pct: "5",
    alternative_monthly_return_pct: "5",
    monthly_excess_pct: "0",
    baseline_max_drawdown_pct: "12",
    alternative_max_drawdown_pct: "12",
  };
  const rendered = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [evaluation("VTI"), evaluation("ITOT")],
    context: context(),
    comparisons: [tied],
  });
  assert(
    rendered.body.includes("matched to 0.0 points"),
    "a tied comparison invented a leader",
  );
  const roundedTie = await renderPublication({
    phase: "pre-market",
    market_date: "2026-09-03",
    evaluations: [evaluation("VTI"), evaluation("ITOT")],
    context: context(),
    comparisons: [{ ...tied, monthly_excess_pct: "0.01" }],
  });
  assert(
    roundedTie.body.includes("difference under 0.1 point"),
    "a sub-display-precision comparison invented a visible winner",
  );
  assert(
    !roundedTie.body.includes("ahead by 0.0 points"),
    "a rounded comparison claimed a zero-point lead",
  );
  for (
    const reason of [
      "Buy ITOT immediately.",
      "I recommend buying ITOT now.",
      "Consider replacing VTI with ITOT.",
      "Move the monthly plan to ITOT.",
      "Reallocate the contribution to ITOT.",
    ]
  ) {
    await assertRejects(
      () =>
        renderPublication({
          phase: "pre-market",
          market_date: "2026-09-03",
          evaluations: [evaluation("VTI"), evaluation("ITOT")],
          context: context(),
          comparisons: [{ ...tied, reason }],
        }),
      "forbidden decision directive",
    );
  }
});

Deno.test("approved held-position reductions remain visible in portfolio risks", async () => {
  const item = evaluation("CENX", {
    raw_action: "sell",
    final_action: "sell",
  });
  item.candidate.action = "sell";
  const rendered = await renderPublication({
    phase: "post-market",
    market_date: "2026-09-03",
    evaluations: [item],
    context: context(),
  });
  assert(
    rendered.body.includes(
      "CENX: policy-approved SELL review; open the full analysis before acting.",
    ),
    "approved held-position action disappeared from the grouped brief",
  );
});

Deno.test("directive validator rejects trade language, quantities, dollars, and levels", async () => {
  for (
    const text of [
      "Buy this now",
      "10 shares looks right",
      "$500 allocation",
      "stop: 42",
    ]
  ) {
    assert(
      FORBIDDEN_DECISION_TEXT.test(text),
      `fixture did not match directive regex: ${text}`,
    );
    const item = evaluation();
    item.candidate.factors[0].text = text;
    await assertRejects(
      () =>
        renderPublication({
          phase: "pre-market",
          market_date: "2026-09-03",
          evaluations: [item],
          context: context(),
        }),
      "forbidden decision directive",
    );
  }
});

Deno.test("intraday uses one concise publication and fixed trigger priority", async () => {
  const near = evaluation("AAA");
  near.candidate.notification_kind = "stop_near";
  const breach = evaluation("BBB");
  breach.candidate.notification_kind = "stop_breach";
  const rendered = await renderPublication({
    phase: "intraday",
    market_date: "2026-09-03",
    evaluations: [near, breach],
    context: context(),
  });
  assertEquals(rendered.kind, "stop_breach");
  assertEquals(rendered.parts.length, 1);
  assert(
    rendered.body.includes("AAA") && rendered.body.includes("BBB"),
    "lower-priority trigger omitted",
  );
  assert(
    !rendered.body.includes("evidence:"),
    "audit evidence leaked into intraday alert",
  );
  assert(
    !rendered.body.includes("📊 YOUR PORTFOLIO"),
    "full brief leaked into intraday alert",
  );
});

Deno.test("intraday entry and new-idea alerts retain complete policy levels", async () => {
  for (const notification of ["entry_trigger", "new_idea"] as const) {
    const item = evaluation("PLTR");
    item.candidate.notification_kind = notification;
    const rendered = await renderPublication({
      phase: "intraday",
      market_date: "2026-09-03",
      evaluations: [item],
      context: context(),
    });
    assert(
      rendered.body.includes(
        "Entry zone $45.00–$47.02 · Stop $42.00 · Target $58.00 · Valid through 2026-09-09 · Confidence MEDIUM",
      ),
      `complete levels absent for ${notification}`,
    );
  }
});

Deno.test("intraday watch idea explains missing levels instead of sending a hollow alert", async () => {
  const item = evaluation("HOOD", {
    raw_action: "watch",
    final_action: "watch",
  });
  item.candidate.phase = "intraday";
  item.candidate.action = "watch";
  item.candidate.notification_kind = "new_idea";
  item.candidate.confidence = "low";
  item.candidate.entry_zone_low = null;
  item.candidate.entry_zone_high = null;
  item.candidate.stop = null;
  item.candidate.target = null;
  item.candidate.valid_until = null;
  item.candidate.factors[1].text =
    "Confirmed catalysts surfaced, but price is extended and an entry cannot be defended.";
  item.normalized.verified_price = "123.17";
  item.normalized.quote_as_of = "2026-09-03T17:09:11.000Z";

  const rendered = await renderPublication({
    phase: "intraday",
    market_date: "2026-09-03",
    evaluations: [item],
    context: context(),
  });
  for (
    const expected of [
      "<b>👀 HOOD — WATCH IDEA</b>",
      "$123.17 · 12:09:11 PM CT",
      "No policy-approved entry, stop, or target — watch only.",
      "Why now: Confirmed catalysts surfaced, but price is extended and an entry cannot be defended.",
      "Confidence LOW",
      "Suggestion only — no order was placed.",
    ]
  ) {
    assert(
      rendered.body.includes(expected),
      `useful watch detail absent: ${expected}`,
    );
  }
  assert(
    !rendered.body.includes("— NEW IDEA"),
    "watch was mislabeled as an entry idea",
  );
  assert(
    !rendered.body.includes("2026-09-03T17:09:11.000Z"),
    "raw UTC timestamp made the alert hard to scan",
  );
});

Deno.test("non-holiday rendering fails closed without authoritative portfolio context", async () => {
  await assertRejects(
    () =>
      renderPublication({
        phase: "pre-market",
        market_date: "2026-09-03",
        evaluations: [evaluation()],
      }),
    "render context is required",
  );
});

function alertRule(
  overrides: Partial<AlertRuleSnapshot> = {},
): AlertRuleSnapshot {
  return {
    rule_id: "7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7",
    version: 3,
    state: "active",
    ticker: "ABC",
    profile: "balanced",
    severity: "review",
    session: "regular",
    confirmation: "two_quote",
    conditions: [{
      kind: "price_zone",
      operator: "inside",
      left: "41.8",
      right: "42.3",
      timeframe: "quote",
    }],
    cooldown_seconds: 14_400,
    fire_limit: 3,
    valid_until: "2026-09-09T21:00:00.000Z",
    owner_note: "Owner-reviewed setup",
    ...overrides,
  };
}

function alertEvaluation(
  overrides: Partial<AlertEvaluation> = {},
): AlertEvaluation {
  const rule = overrides.rule ?? alertRule();
  return {
    rule,
    status: "triggered",
    reason_codes: [],
    observed_at: "2026-09-03T17:14:51.000Z",
    evaluated_at: "2026-09-03T17:15:04.000Z",
    market_session: "regular",
    condition_results: [{
      condition: rule.conditions[0],
      passed: true,
      observed_value: "42.1",
      evidence_ids: ["quote-1"],
    }],
    ...overrides,
  };
}

function alertSource(): PolicyEvaluation {
  const source = evaluation("ABC");
  source.candidate.phase = "intraday";
  source.candidate.notification_kind = "entry_trigger";
  source.candidate.entry_zone_low = "41.8";
  source.candidate.entry_zone_high = "42.3";
  source.candidate.stop = "39.75";
  source.candidate.invalidation_price = "39.9";
  source.candidate.target = "47.2";
  source.candidate.valid_until = "2026-09-09";
  source.candidate.factors = [{
    kind: "technicals",
    stance: "bull",
    text: "Price and participation confirm the previously reviewed setup.",
    evidence_ids: ["quote-1"],
  }];
  source.normalized.verified_price = "42.1";
  source.normalized.quote_as_of = "2026-09-03T17:14:51.000Z";
  return source;
}

Deno.test("alert v3 renders trigger-first details, approved levels, receipts, and inert owner actions", async () => {
  const rendered = await renderAlertV3({
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation(),
    source_evaluation: alertSource(),
    context: context(),
  });
  const sections = [
    "🟠 REVIEW • ABC • BALANCED",
    "Triggered 12:15:04 PM CT",
    "Conditions 1/1",
    "Why now:",
    "Risk:",
    "Confidence MEDIUM",
    "Receipt AL-7F2C",
  ];
  for (let index = 1; index < sections.length; index += 1) {
    assert(
      rendered.body.indexOf(sections[index - 1]) <
        rendered.body.indexOf(sections[index]),
      `${sections[index - 1]} did not precede ${sections[index]}`,
    );
  }
  assert(
    rendered.body.includes("suggestion only; no order was placed"),
    "suggestion-only boundary absent",
  );
  assert(
    rendered.body.includes("quote 12:14:51 PM CT • age 13s • REGULAR"),
    "receipt timing absent",
  );
  assert(
    rendered.body.includes("inside $41.80–$42.30"),
    "condition facts absent",
  );
  assert(
    rendered.body.includes(
      "invalidation below $39.90 • policy-approved stop $39.75 • target $47.20",
    ),
    "approved stop/target absent",
  );
  assert(
    rendered.body.includes("evidence 1/1 • valid through 2026-09-09"),
    "coverage or horizon absent",
  );
  assertEquals(rendered.template_version, 3);
  assertEquals(rendered.status, "ready");
  assertEquals(rendered.parts, [rendered.body]);
  assertEquals(
    rendered.reply_markup.inline_keyboard.map((row) =>
      row.map((button) => button.text)
    ),
    [["Acknowledge", "Snooze 1d", "Dismiss"]],
  );
  assertEquals(
    rendered.reply_markup.inline_keyboard[0][0].callback_data,
    "al:ack:7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
  );
  assertEquals(
    rendered.reply_markup.inline_keyboard[0][2].callback_data,
    "al:dismiss:7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
  );
  for (const row of rendered.reply_markup.inline_keyboard) {
    for (const button of row) {
      assert(
        button.callback_data.length <= 64,
        "callback exceeds Telegram limit",
      );
      assert(
        !/buy|sell|order/i.test(button.text),
        "execution-like button leaked",
      );
    }
  }
});

Deno.test("critical recorded-stop alert uses recorded levels and manual-review language", async () => {
  const rule = alertRule({
    version: 2,
    severity: "critical",
    conditions: [{
      kind: "recorded_stop",
      operator: "below",
      left: "45.58",
      right: null,
      timeframe: "quote",
    }],
  });
  const source = alertSource();
  source.final_action = "hold";
  source.candidate.action = "hold";
  source.candidate.notification_kind = "stop_breach";
  source.candidate.stop = null;
  source.candidate.target = null;
  source.normalized.verified_price = "45.4";
  source.normalized.quote_as_of = "2026-09-03T15:42:01.000Z";
  const rendered = await renderAlertV3({
    event_id: "91d070bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation({
      rule,
      observed_at: "2026-09-03T15:42:01.000Z",
      evaluated_at: "2026-09-03T15:42:18.000Z",
      condition_results: [{
        condition: rule.conditions[0],
        passed: true,
        observed_value: "45.4",
        evidence_ids: ["quote-1"],
      }],
    }),
    source_evaluation: source,
    context: context({
      holdings: [{
        ...context().holdings[0],
        ticker: "ABC",
        stop: "44",
        target: "52",
      }],
    }),
  });
  assert(
    rendered.body.startsWith("<b>🔴 RISK REVIEW • ABC • BALANCED</b>"),
    "critical heading absent",
  );
  assert(
    rendered.body.includes("review manually"),
    "manual review boundary absent",
  );
  assert(
    rendered.body.includes("recorded stop $45.58"),
    "immutable rule stop absent",
  );
  assert(
    !rendered.body.includes("recorded stop $44.00"),
    "new holding stop replaced the fired rule version",
  );
  assert(
    rendered.body.includes(
      "The bot did not sell and cannot access a brokerage.",
    ),
    "broker boundary absent",
  );
  assertEquals(rendered.reply_markup.inline_keyboard[0][1].text, "Snooze 20m");
});

Deno.test("alert v3 renders inert drafts and unsafe evaluations without inventing a decision", async () => {
  const draftRule = alertRule({
    state: "draft",
    version: 1,
    severity: "watch",
  });
  const draft = await renderAlertV3({
    event_id: draftRule.rule_id,
    evaluation: alertEvaluation({
      rule: draftRule,
      status: "not_triggered",
      condition_results: [{
        condition: draftRule.conditions[0],
        passed: null,
        observed_value: null,
        evidence_ids: [],
      }],
    }),
    source_evaluation: alertSource(),
    context: null,
    mode: "draft",
  });
  assert(draft.body.includes("DRAFT • ABC • BALANCED"), "draft label absent");
  assert(
    draft.body.includes("inert until you arm it"),
    "draft lifecycle boundary absent",
  );
  assert(
    draft.body.includes("Proposed from quote"),
    "draft source timing absent",
  );
  assert(!draft.body.includes("Triggered"), "inert draft was called triggered");
  assert(
    draft.body.includes("Proposed conditions 1: inside $41.80–$42.30"),
    "draft condition wording absent",
  );
  assert(
    !draft.body.includes("Conditions 1/1"),
    "draft claimed an evaluated pass count",
  );
  assert(
    draft.body.includes("evidence 2/2"),
    "draft source evidence coverage absent",
  );
  assertEquals(
    draft.reply_markup.inline_keyboard[0].map((button) => button.text),
    ["Arm", "Dismiss"],
  );

  const unsafeRule = alertRule({ severity: "system" });
  const unsafe = await renderAlertV3({
    event_id: "aa2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation({
      rule: unsafeRule,
      status: "unsafe_to_evaluate",
      reason_codes: ["EVIDENCE_STALE", "SESSION_MISMATCH"],
      condition_results: [{
        condition: unsafeRule.conditions[0],
        passed: null,
        observed_value: null,
        evidence_ids: [],
      }],
    }),
    source_evaluation: null,
    context: null,
  });
  assert(
    unsafe.body.includes("was not evaluated safely"),
    "unsafe label absent",
  );
  assert(unsafe.body.includes("Unavailable"), "unavailable condition absent");
  assert(
    unsafe.body.includes(
      "No safe conclusion was produced because required evidence was unavailable.",
    ),
    "unsafe explanation absent",
  );
  assert(
    !unsafe.body.includes("conditions passed"),
    "unsafe evaluation claimed conditions passed",
  );
  assert(!/\bHOLD\b/.test(unsafe.body), "unsafe evaluation invented Hold");
});

Deno.test("alert v3 draft coverage counts its source packet without widening event evidence", async () => {
  const source = alertSource();
  source.candidate.evidence[1].status = "stale";
  source.candidate.evidence.push({
    ...source.candidate.evidence[0],
    id: "fallback-1",
    status: "fallback",
  });
  const draftRule = alertRule({
    state: "draft",
    version: 1,
    severity: "watch",
  });
  const draft = await renderAlertV3({
    event_id: draftRule.rule_id,
    evaluation: alertEvaluation({
      rule: draftRule,
      status: "not_triggered",
      condition_results: [{
        condition: draftRule.conditions[0],
        passed: null,
        observed_value: null,
        evidence_ids: [],
      }],
    }),
    source_evaluation: source,
    context: null,
    mode: "draft",
  });
  assert(
    draft.body.includes("evidence 2/3"),
    "draft source evidence coverage is inaccurate",
  );

  const event = await renderAlertV3({
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation(),
    source_evaluation: source,
    context: context(),
  });
  assert(
    event.body.includes("evidence 1/1"),
    "event coverage included evidence unrelated to the trigger",
  );
});

Deno.test("alert v3 suppresses unmet rules and omits unsafe model narrative", async () => {
  const source = alertSource();
  source.candidate.factors[0].text = "Buy now at $42 and sell at target";
  source.candidate.decisive_factor = "Buy immediately";
  const safe = await renderAlertV3({
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation(),
    source_evaluation: source,
    context: null,
  });
  assert(!safe.body.includes("Buy now"), "unsafe factor leaked");
  assert(
    safe.body.includes(
      "Deterministic conditions passed; no safe thesis summary was available.",
    ),
    "safe fallback absent",
  );
  assert(safe.body.length <= 3500, "alert exceeds Telegram bound");

  const unmet = await renderAlertV3({
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation({ status: "not_triggered" }),
    source_evaluation: source,
    context: null,
  });
  assertEquals(unmet.status, "suppressed");
  assertEquals(unmet.parts, []);
  assertEquals(unmet.reply_markup.inline_keyboard, []);
});
