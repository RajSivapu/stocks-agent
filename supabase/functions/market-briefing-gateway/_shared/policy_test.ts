import type {
  Action,
  DecisionCandidate,
  HoldingState,
  PolicyConfig,
  PolicyContext,
  VerifiedQuote,
} from "./contracts.ts";
import { draftFromEvaluation, evaluateCandidate } from "./policy.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

const NOW = new Date("2026-09-02T17:00:00.000Z");
const NEW_ID = () => "00000000-0000-4000-8000-000000000099";

function config(): PolicyConfig {
  return {
    version: 1,
    allocation_bps: { core: 7000, growth: 2000, speculative: 1000 },
    max_position_bps_of_bucket: { core: 2500, growth: 2000, speculative: 1000 },
    max_trade_risk_bps: { core: 100, growth: 100, speculative: 50 },
    min_reward_risk_milli: 2000,
    max_actionable_quote_age_minutes: 20,
    alert_near_bps: 400,
    daily_loss_limit_bps: 300,
    circuit_breaker_consecutive_losses: 3,
    speculative_go_live_bucket_micros: "500000000",
    monthly_investment_micros: "500000000",
    broad_core_etfs: ["SCHD", "VOO", "VTI", "VXUS"],
    self_tuning_enabled: false,
    market_calendar_year: 2026,
    nyse_holidays: ["2026-09-07"],
    request_limits: {
      max_body_bytes: 262144,
      max_candidates: { "pre-market": 80, intraday: 20, "post-market": 80, "on-demand": 10 },
      max_requests_per_run: 20,
      max_authenticated_requests_per_hour: 100,
    },
  };
}

function quote(ticker = "CENX", price = "47.02", asOf = "2026-09-02T16:55:00.000Z", marketState = "REGULAR"): VerifiedQuote {
  return { ticker, price, previous_close: "46", as_of: asOf, market_state: marketState, source: "yahoo-chart" };
}

function holding(overrides: Partial<HoldingState> = {}): HoldingState {
  return {
    ticker: "VTI",
    shares: "100",
    avg_cost: "380",
    bucket: "core",
    stop: "350",
    target: "450",
    high_water_price: "410",
    hold_override_until: null,
    stop_alert_active: false,
    stop_near_alert_active: false,
    target_near_alert_active: false,
    target_alert_active: false,
    ...overrides,
  };
}

function context(overrides: Partial<PolicyContext> = {}): PolicyContext {
  return {
    holdings: [holding()],
    holding_quotes: { VTI: quote("VTI", "400") },
    realized_pnl_today: "0",
    portfolio_command_coverage_complete: true,
    consecutive_completed_losses: 0,
    owner_plans: [],
    ...overrides,
  };
}

function candidate(overrides: Partial<DecisionCandidate> = {}): DecisionCandidate {
  return {
    candidate_id: "00000000-0000-4000-8000-000000000010",
    ticker: "CENX",
    phase: "intraday",
    action: "buy",
    notification_kind: "brief",
    decision_mode: "discretionary",
    bucket: "growth",
    depth: "full",
    confidence: "medium",
    confidence_reason: "Current evidence supports a bounded position.",
    health_score: "72",
    observed_price: "47.02",
    observed_quote_as_of: "2026-09-02T16:55:00.000Z",
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
      kind: "quote",
      source: "yahoo",
      status: "fresh",
      observed_at: "2026-09-02T16:55:00.000Z",
      retrieved_at: "2026-09-02T16:56:00.000Z",
      reference: null,
      claims: ["Current quote."],
    }],
    factors: [{ kind: "risk", stance: "neutral", text: "Risk is bounded.", evidence_ids: ["quote-1"] }],
    analyst: { completed: true, action: "buy", confidence: "medium", reason: "Pass." },
    checker: { completed: true, verdict: "approve", reason_codes: [], reason: "Pass." },
    decisive_factor: "Risk-adjusted setup.",
    invalidation: "Price breaks support.",
    prior_suggestion_ids: [],
    ...overrides,
  };
}

function evaluate(c = candidate(), ctx = context(), q: VerifiedQuote | null = quote(), cfg = config(), now = NOW) {
  return evaluateCandidate(c, ctx, cfg, q, now, NEW_ID);
}

Deno.test("non-actionable actions are never upgraded", () => {
  for (const action of ["watch", "hold", "avoid"] as Action[]) {
    const c = candidate({
      action,
      analyst: { completed: true, action, confidence: "high", reason: "Pass." },
      proposed_amount: null,
      proposed_shares: null,
      entry_zone_low: null,
      entry_zone_high: null,
      stop: null,
      target: null,
      invalidation_price: null,
    });
    assertEquals(evaluate(c).final_action, action);
  }
});

Deno.test("only approved fresh policy evaluations project inert alert drafts", () => {
  const enabled = config();
  enabled.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const approved = evaluate(candidate({ notification_kind: "entry_trigger" }));
  const projected = draftFromEvaluation(
    approved,
    context(),
    enabled,
    NOW,
    () => "00000000-0000-4000-8000-000000000123",
  );
  if (projected === null) throw new Error("approved trigger did not project a draft");
  assertEquals(projected.state, "draft");
  assertEquals(projected.profile, "balanced");
  assertEquals(projected.conditions, [{
    kind: "price_zone",
    operator: "inside",
    left: "45",
    right: "47.02",
    timeframe: "quote",
  }]);
  assertEquals(projected.confirmation, "two_quote");

  const stale = evaluate(candidate({ notification_kind: "entry_trigger" }), context(),
    quote("CENX", "47.02", "2026-09-02T16:30:00.000Z"));
  assertEquals(draftFromEvaluation(stale, context(), enabled, NOW), null);
  const vetoed = evaluate(candidate({
    notification_kind: "entry_trigger",
    checker: { completed: true, verdict: "veto", reason_codes: [], reason: "No." },
  }));
  assertEquals(draftFromEvaluation(vetoed, context(), enabled, NOW), null);
  enabled.alerts_v3.shadow = false;
  assertEquals(draftFromEvaluation(approved, context(), enabled, NOW), null);
});

Deno.test("enabled alert policy projects only its explicit canary class", () => {
  const enabled = config();
  enabled.alerts_v3 = {
    enabled: true,
    shadow: false,
    enabled_classes: ["stop_breach"],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const entry = evaluate(candidate({ notification_kind: "entry_trigger" }));
  assertEquals(draftFromEvaluation(entry, context(), enabled, NOW), null);

  const owned = holding({ ticker: "CENX", shares: "10", bucket: "growth", stop: "45" });
  const ctx = context({ holdings: [owned], holding_quotes: { CENX: quote("CENX", "44") } });
  const stopCandidate = candidate({
    action: "hold",
    notification_kind: "stop_breach",
    proposed_amount: null,
    proposed_shares: null,
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
    analyst: { completed: true, action: "hold", confidence: "high", reason: "Recorded stop breached." },
  });
  const stop = evaluate(stopCandidate, ctx, quote("CENX", "44"));
  const projected = draftFromEvaluation(stop, ctx, enabled, NOW);
  if (projected === null) throw new Error("allowlisted stop breach did not project");
  assertEquals(projected.conditions[0].kind, "recorded_stop");
});

Deno.test("new ideas do not create rules until screen evidence has a protected adapter", () => {
  const enabled = config();
  enabled.alerts_v3 = {
    enabled: false,
    shadow: true,
    enabled_classes: [],
    profile: "balanced",
    draft_ttl_hours: 24,
    drafts_per_hour: 5,
  };
  const idea = candidate({
    action: "watch",
    notification_kind: "new_idea",
    proposed_amount: null,
    proposed_shares: null,
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
    analyst: { completed: true, action: "watch", confidence: "low", reason: "Track only." },
  });
  const projected = draftFromEvaluation(evaluate(idea), context(), enabled, NOW);
  assertEquals(projected, null);
});

Deno.test("stale live quote downgrades Buy and on-demand cannot bypass it", () => {
  const stale = quote("CENX", "47.02", "2026-09-02T16:39:00.000Z");
  const intraday = evaluate(candidate(), context(), stale);
  assertEquals(intraday.final_action, "watch");
  assert(intraday.reason_codes.includes("QUOTE_STALE"), "missing stale reason");
  assertEquals(intraday.holding_state_change, null);

  const onDemand = evaluate(candidate({ phase: "on-demand" }), context(), stale);
  assertEquals(onDemand.final_action, "watch");
  assert(onDemand.reason_codes.includes("QUOTE_STALE"), "on-demand bypassed freshness");
});

Deno.test("outside-session research is conditional and cannot emit an entry trigger", () => {
  const close = quote("CENX", "47.02", "2026-09-02T20:00:00.000Z", "CLOSED");
  const afterClose = new Date("2026-09-02T22:00:00.000Z");
  const closedContext = context({ holding_quotes: { VTI: quote("VTI", "400", "2026-09-02T20:00:00.000Z", "CLOSED") } });
  const conditional = evaluate(candidate({ phase: "on-demand" }), closedContext, close, config(), afterClose);
  assertEquals(conditional.final_action, "buy");
  assert(conditional.reason_codes.includes("OUTSIDE_SESSION_CONDITIONAL"), "conditional metadata absent");

  const trigger = evaluate(candidate({ phase: "on-demand", notification_kind: "entry_trigger" }), closedContext, close, config(), afterClose);
  assertEquals(trigger.final_action, null);
  assert(trigger.reason_codes.includes("QUOTE_SESSION_MISMATCH"), "outside entry trigger was not vetoed");
});

Deno.test("calendar coverage and pre-market entry triggers fail closed", () => {
  const missingCalendar = config();
  missingCalendar.market_calendar_year = 2025;
  const result = evaluate(candidate(), context(), quote(), missingCalendar);
  assertEquals(result.final_action, "watch");
  assert(result.reason_codes.includes("CALENDAR_COVERAGE_MISSING"), "calendar gap absent");

  const pre = candidate({ phase: "pre-market", notification_kind: "entry_trigger" });
  const preNow = new Date("2026-09-02T11:00:00.000Z");
  const preContext = context({ holding_quotes: { VTI: quote("VTI", "400", "2026-09-01T20:00:00.000Z", "CLOSED") } });
  const preResult = evaluate(pre, preContext, quote("CENX", "47.02", "2026-09-01T20:00:00.000Z", "CLOSED"), config(), preNow);
  assertEquals(preResult.final_action, null);
  assert(preResult.reason_codes.includes("QUOTE_SESSION_MISMATCH"), "pre-market trigger was not vetoed");
});

Deno.test("prior plans need current evidence", () => {
  const staleEvidence = candidate({
    prior_suggestion_ids: ["123"],
    evidence: [{ ...candidate().evidence[0], status: "stale", observed_at: "2026-09-01T17:00:00.000Z" }],
  });
  const result = evaluate(staleEvidence);
  assertEquals(result.final_action, "watch");
  assert(result.reason_codes.includes("CURRENT_EVIDENCE_MISSING"), "stale morning plan was reused");
});

Deno.test("Analyst and Checker completion and verdicts remain distinct", () => {
  const analyst = evaluate(candidate({ analyst: { ...candidate().analyst, completed: false } }));
  assert(analyst.reason_codes.includes("ANALYST_INCOMPLETE"), "analyst reason absent");
  const checker = evaluate(candidate({ checker: { ...candidate().checker, completed: false } }));
  assert(checker.reason_codes.includes("CHECKER_INCOMPLETE"), "checker reason absent");
  const downgraded = evaluate(candidate({ checker: { completed: true, verdict: "downgrade", reason_codes: [], reason: "Weak." } }));
  assertEquals(downgraded.final_action, "watch");
  assert(downgraded.reason_codes.includes("CHECKER_DOWNGRADE"), "downgrade reason absent");
  const vetoed = evaluate(candidate({ checker: { completed: true, verdict: "veto", reason_codes: [], reason: "Unsafe." } }));
  assertEquals(vetoed.final_action, null);
  assert(vetoed.reason_codes.includes("CHECKER_VETO"), "veto reason absent");
});

Deno.test("ownership and sell quantity mismatches veto", () => {
  const owned = holding({ ticker: "CENX", shares: "20", bucket: "growth" });
  assertEquals(evaluate(candidate(), context({ holdings: [owned], holding_quotes: { CENX: quote() } })).final_action, null);
  for (const action of ["add", "reduce", "sell"] as Action[]) {
    const c = candidate({ action, analyst: { ...candidate().analyst, action } });
    assertEquals(evaluate(c).final_action, null);
  }
  const sell = candidate({ action: "sell", analyst: { ...candidate().analyst, action: "sell" }, proposed_amount: null, proposed_shares: "21" });
  const sold = evaluate(sell, context({ holdings: [owned], holding_quotes: { CENX: quote() } }));
  assertEquals(sold.final_action, null);
  assert(sold.reason_codes.includes("SELL_EXCEEDS_HOLDING"), "sell limit absent");
});

Deno.test("long price relationships are strict", () => {
  for (const fields of [
    { stop: "45" },
    { entry_zone_low: "48" },
    { target: "47.02" },
  ]) {
    const result = evaluate(candidate(fields));
    assertEquals(result.final_action, null);
    assert(result.reason_codes.includes("PRICE_RELATION_INVALID"), "invalid levels passed");
  }
});

Deno.test("fractional shares reconcile to amount within one cent", () => {
  const large = holding({ ticker: "VTI", shares: "1000" });
  const ctx = context({ holdings: [large], holding_quotes: { VTI: quote("VTI", "400") } });
  const result = evaluate(candidate({ proposed_amount: "2057.04", proposed_shares: "43.748192" }), ctx);
  assertEquals(result.final_action, "buy");
  assert(!result.reason_codes.includes("AMOUNT_SHARES_MISMATCH"), "cent reconciliation failed");
});

Deno.test("risk, reward-risk, and position limits only downgrade Buy", () => {
  const highRisk = evaluate(candidate({ stop: "1" }));
  assert(highRisk.reason_codes.includes("TRADE_RISK_EXCEEDED"), "risk cap absent");
  const lowReward = evaluate(candidate({ target: "50" }));
  assert(lowReward.reason_codes.includes("REWARD_RISK_TOO_LOW"), "reward-risk floor absent");
  const oversized = evaluate(candidate({ proposed_amount: "4702", proposed_shares: "100" }));
  assert(oversized.reason_codes.includes("POSITION_CAP_EXCEEDED"), "position cap absent");
  assertEquals(highRisk.final_action, "watch");
  assertEquals(lowReward.final_action, "watch");
  assertEquals(oversized.final_action, "watch");
});

Deno.test("Add uses the recorded holding stop and cannot substitute a model level", () => {
  const owned = holding({ ticker: "CENX", shares: "5", avg_cost: "44", bucket: "growth", stop: null });
  const ctx = context({ holdings: [owned], holding_quotes: { CENX: quote() } });
  const add = candidate({ action: "add", analyst: { ...candidate().analyst, action: "add" } });
  const result = evaluate(add, ctx);
  assertEquals(result.final_action, "watch");
  assert(result.reason_codes.includes("STOP_REQUIRED"), "missing authoritative Add stop passed");
});

Deno.test("portfolio completeness and loss locks block Buy", () => {
  const missing = evaluate(candidate(), context({ holding_quotes: {} }));
  assert(missing.reason_codes.includes("PORTFOLIO_VALUE_INCOMPLETE"), "missing holding quote passed");
  const uncovered = evaluate(candidate(), context({ portfolio_command_coverage_complete: false }));
  assert(uncovered.reason_codes.includes("PORTFOLIO_VALUE_INCOMPLETE"), "incomplete ledger passed");
  const daily = evaluate(candidate(), context({ realized_pnl_today: "-1300" }));
  assert(daily.reason_codes.includes("DAILY_LOSS_LOCKOUT"), "daily loss lock absent");
  const consecutive = evaluate(candidate(), context({ consecutive_completed_losses: 3 }));
  assert(consecutive.reason_codes.includes("CONSECUTIVE_LOSS_LOCKOUT"), "loss streak lock absent");
});

Deno.test("speculative learning threshold blocks real-money action", () => {
  const result = evaluate(candidate({ bucket: "speculative" }));
  assertEquals(result.final_action, "watch");
  assert(result.reason_codes.includes("SPECULATIVE_LEARNING_ONLY"), "speculative gate absent");
});

Deno.test("matching VTI owner plan skips stops but keeps core allocation checks", () => {
  const plan = {
    id: "00000000-0000-4000-8000-000000000020",
    ticker: "VTI",
    bucket: "core" as const,
    amount: "300",
    cadence: "monthly" as const,
    next_due_on: "2026-09-01",
    active: true,
    updated_at: "2026-08-19T12:00:00.000Z",
  };
  const voo = holding({ ticker: "VOO", shares: "100", avg_cost: "480", high_water_price: "510" });
  const ctx = context({ holdings: [voo], holding_quotes: { VOO: quote("VOO", "500") }, owner_plans: [plan] });
  const c = candidate({
    ticker: "VTI",
    action: "buy",
    decision_mode: "owner_plan",
    bucket: "core",
    proposed_amount: "300",
    proposed_shares: "0.75",
    observed_price: "400",
    entry_zone_low: null,
    entry_zone_high: null,
    stop: null,
    target: null,
    invalidation_price: null,
    analyst: { completed: true, action: "buy", confidence: "medium", reason: "Scheduled." },
  });
  const result = evaluate(c, ctx, quote("VTI", "400"));
  assertEquals(result.final_action, "buy");
  assert(!result.reason_codes.includes("STOP_REQUIRED"), "plan incorrectly required a stop");
  assert(!result.reason_codes.includes("REWARD_RISK_TOO_LOW"), "plan incorrectly required reward-risk");

  const bad = evaluate({ ...c, proposed_amount: "301" }, ctx, quote("VTI", "400"));
  assertEquals(bad.final_action, null);
  assert(bad.reason_codes.includes("OWNER_PLAN_MISMATCH"), "bad plan identity passed");
});

Deno.test("holding alert edges require thresholds, freshness, and inactive flags", () => {
  const owned = holding({ ticker: "CENX", shares: "10", bucket: "growth", stop: "45", target: "50" });
  const alert = (notification_kind: DecisionCandidate["notification_kind"]) => candidate({
    action: "hold", notification_kind, proposed_amount: null, proposed_shares: null,
    entry_zone_low: null, entry_zone_high: null, stop: null, target: null,
    invalidation_price: null,
    analyst: { completed: true, action: "hold", confidence: "medium", reason: "Threshold edge." },
  });
  for (const [kind, price, flag] of [
    ["stop_near", "46", "stop_near_alert_active"],
    ["stop_breach", "44", "stop_alert_active"],
    ["target_near", "49", "target_near_alert_active"],
    ["target_hit", "51", "target_alert_active"],
  ] as const) {
    const q = quote("CENX", price);
    const ctx = context({ holdings: [owned], holding_quotes: { CENX: q } });
    const result = evaluate(alert(kind), ctx, q);
    assertEquals(result.final_action, "hold");
    assert(result.holding_state_change?.[flag] === true, `${kind} did not set its edge flag`);
  }
  const active = context({ holdings: [{ ...owned, stop_near_alert_active: true }], holding_quotes: { CENX: quote("CENX", "46") } });
  const duplicate = evaluate(alert("stop_near"), active, quote("CENX", "46"));
  assertEquals(duplicate.final_action, null);
  assert(duplicate.reason_codes.includes("ALERT_ALREADY_ACTIVE"), "duplicate alert passed");
});

Deno.test("policy evaluation does not mutate the model candidate", () => {
  const input = candidate();
  const before = JSON.stringify(input);
  evaluate(input);
  assertEquals(JSON.stringify(input), before);
});

Deno.test("hold override suppresses mechanical stop but not evidenced thesis break", () => {
  const owned = holding({ ticker: "CENX", shares: "10", bucket: "growth", stop: "45", hold_override_until: "2026-09-05" });
  const ctx = context({ holdings: [owned], holding_quotes: { CENX: quote("CENX", "44") } });
  const stop = candidate({
    action: "hold", notification_kind: "stop_breach", proposed_amount: null, proposed_shares: null,
    entry_zone_low: null, entry_zone_high: null, stop: null, target: null, invalidation_price: null,
    analyst: { completed: true, action: "hold", confidence: "high", reason: "Stop breached." },
  });
  assertEquals(evaluate(stop, ctx, quote("CENX", "44")).final_action, null);
  const thesis = { ...stop, notification_kind: "thesis_break" as const, evidence: [{ ...stop.evidence[0], kind: "event" as const }] };
  assertEquals(evaluate(thesis, ctx, quote("CENX", "44")).final_action, "hold");
});

Deno.test("holding output contains only server-authorized high-water and edge fields", () => {
  const owned = holding({ ticker: "CENX", shares: "10", bucket: "growth", high_water_price: "48" });
  const ctx = context({ holdings: [owned], holding_quotes: { CENX: quote("CENX", "49") } });
  const c = candidate({ action: "hold", proposed_amount: null, proposed_shares: null, entry_zone_low: null, entry_zone_high: null, stop: null, target: null, invalidation_price: null, analyst: { completed: true, action: "hold", confidence: "medium", reason: "Hold." } });
  const change = evaluate(c, ctx, quote("CENX", "49")).holding_state_change;
  assert(change !== null, "holding state change absent");
  assertEquals(Object.keys(change!).sort(), ["high_water_price", "stop_alert_active", "stop_near_alert_active", "target_alert_active", "target_near_alert_active", "ticker"]);
  assert(!("stop" in change!), "model stop leaked into state change");
  assert(!("target" in change!), "model target leaked into state change");
  const staleChange = evaluate(c, ctx, quote("CENX", "49", "2026-09-02T16:39:00.000Z")).holding_state_change;
  assertEquals(staleChange, null);
});

Deno.test("increasing shares or widening stop cannot upgrade a failed proposal", () => {
  const failed = evaluate(candidate({ proposed_amount: "9404", proposed_shares: "200" }));
  const larger = evaluate(candidate({ proposed_amount: "18808", proposed_shares: "400" }));
  const wider = evaluate(candidate({ proposed_amount: "9404", proposed_shares: "200", stop: "1" }));
  assert(failed.status !== "approved", "baseline should fail");
  assert(larger.status !== "approved", "larger trade upgraded failure");
  assert(wider.status !== "approved", "wider stop upgraded failure");
});
