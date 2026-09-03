import type { AlertEvaluation, AlertRuleSnapshot, PolicyContext } from "./contracts.ts";
import type { PolicyEvaluation } from "./policy.ts";
import { FORBIDDEN_DECISION_TEXT, renderAlertV3, renderPublication } from "./renderer.ts";

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
    rendered.body.startsWith("<b>🌙 CLOSING BRIEF — 2026-09-03</b>"),
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

function alertRule(overrides: Partial<AlertRuleSnapshot> = {}): AlertRuleSnapshot {
  return {
    rule_id: "7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7",
    version: 3,
    state: "active",
    ticker: "ABC",
    profile: "balanced",
    severity: "review",
    session: "regular",
    confirmation: "two_quote",
    conditions: [{ kind: "price_zone", operator: "inside", left: "41.8", right: "42.3", timeframe: "quote" }],
    cooldown_seconds: 14_400,
    fire_limit: 3,
    valid_until: "2026-09-09T21:00:00.000Z",
    owner_note: "Owner-reviewed setup",
    ...overrides,
  };
}

function alertEvaluation(overrides: Partial<AlertEvaluation> = {}): AlertEvaluation {
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
  const sections = ["🟠 REVIEW • ABC • BALANCED", "Triggered 12:15:04 PM CT", "Conditions 1/1", "Why now:", "Risk:", "Confidence MEDIUM", "Receipt AL-7F2C"];
  for (let index = 1; index < sections.length; index += 1) {
    assert(rendered.body.indexOf(sections[index - 1]) < rendered.body.indexOf(sections[index]), `${sections[index - 1]} did not precede ${sections[index]}`);
  }
  assert(rendered.body.includes("suggestion only; no order was placed"), "suggestion-only boundary absent");
  assert(rendered.body.includes("quote 12:14:51 PM CT • age 13s • REGULAR"), "receipt timing absent");
  assert(rendered.body.includes("inside $41.80–$42.30"), "condition facts absent");
  assert(rendered.body.includes("invalidation below $39.90 • policy-approved stop $39.75 • target $47.20"), "approved stop/target absent");
  assert(rendered.body.includes("evidence 1/1 • valid through 2026-09-09"), "coverage or horizon absent");
  assertEquals(rendered.template_version, 3);
  assertEquals(rendered.status, "ready");
  assertEquals(rendered.parts, [rendered.body]);
  assertEquals(rendered.reply_markup.inline_keyboard.map((row) => row.map((button) => button.text)), [["Acknowledge", "Snooze 1d", "Dismiss"]]);
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
      assert(button.callback_data.length <= 64, "callback exceeds Telegram limit");
      assert(!/buy|sell|order/i.test(button.text), "execution-like button leaked");
    }
  }
});

Deno.test("critical recorded-stop alert uses recorded levels and manual-review language", async () => {
  const rule = alertRule({
    version: 2,
    severity: "critical",
    conditions: [{ kind: "recorded_stop", operator: "below", left: "45.58", right: null, timeframe: "quote" }],
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
      condition_results: [{ condition: rule.conditions[0], passed: true, observed_value: "45.4", evidence_ids: ["quote-1"] }],
    }),
    source_evaluation: source,
    context: context({ holdings: [{ ...context().holdings[0], ticker: "ABC", stop: "44", target: "52" }] }),
  });
  assert(rendered.body.startsWith("<b>🔴 RISK REVIEW • ABC • BALANCED</b>"), "critical heading absent");
  assert(rendered.body.includes("review manually"), "manual review boundary absent");
  assert(rendered.body.includes("recorded stop $45.58"), "immutable rule stop absent");
  assert(!rendered.body.includes("recorded stop $44.00"), "new holding stop replaced the fired rule version");
  assert(rendered.body.includes("The bot did not sell and cannot access a brokerage."), "broker boundary absent");
  assertEquals(rendered.reply_markup.inline_keyboard[0][1].text, "Snooze 20m");
});

Deno.test("alert v3 renders inert drafts and unsafe evaluations without inventing a decision", async () => {
  const draftRule = alertRule({ state: "draft", version: 1, severity: "watch" });
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
  assert(draft.body.includes("inert until you arm it"), "draft lifecycle boundary absent");
  assert(draft.body.includes("Proposed from quote"), "draft source timing absent");
  assert(!draft.body.includes("Triggered"), "inert draft was called triggered");
  assert(draft.body.includes("Proposed conditions 1: inside $41.80–$42.30"), "draft condition wording absent");
  assert(!draft.body.includes("Conditions 1/1"), "draft claimed an evaluated pass count");
  assert(draft.body.includes("evidence 2/2"), "draft source evidence coverage absent");
  assertEquals(draft.reply_markup.inline_keyboard[0].map((button) => button.text), ["Arm", "Dismiss"]);

  const unsafeRule = alertRule({ severity: "system" });
  const unsafe = await renderAlertV3({
    event_id: "aa2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation({
      rule: unsafeRule,
      status: "unsafe_to_evaluate",
      reason_codes: ["EVIDENCE_STALE", "SESSION_MISMATCH"],
      condition_results: [{ condition: unsafeRule.conditions[0], passed: null, observed_value: null, evidence_ids: [] }],
    }),
    source_evaluation: null,
    context: null,
  });
  assert(unsafe.body.includes("was not evaluated safely"), "unsafe label absent");
  assert(unsafe.body.includes("Unavailable"), "unavailable condition absent");
  assert(unsafe.body.includes("No safe conclusion was produced because required evidence was unavailable."), "unsafe explanation absent");
  assert(!unsafe.body.includes("conditions passed"), "unsafe evaluation claimed conditions passed");
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
  const draftRule = alertRule({ state: "draft", version: 1, severity: "watch" });
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
  assert(draft.body.includes("evidence 2/3"), "draft source evidence coverage is inaccurate");

  const event = await renderAlertV3({
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: alertEvaluation(),
    source_evaluation: source,
    context: context(),
  });
  assert(event.body.includes("evidence 1/1"), "event coverage included evidence unrelated to the trigger");
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
  assert(safe.body.includes("Deterministic conditions passed; no safe thesis summary was available."), "safe fallback absent");
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
