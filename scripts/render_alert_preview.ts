import type {
  AlertEvaluation,
  AlertRuleSnapshot,
  PolicyContext,
} from "../supabase/functions/market-briefing-gateway/_shared/contracts.ts";
import type { PolicyEvaluation } from "../supabase/functions/market-briefing-gateway/_shared/policy.ts";
import {
  type RenderAlertV3Input,
  renderAlertV3,
} from "../supabase/functions/market-briefing-gateway/_shared/renderer.ts";

type FixtureName = "draft" | "entry-review" | "stop-breach" | "data-warning";

function rule(overrides: Partial<AlertRuleSnapshot> = {}): AlertRuleSnapshot {
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
    owner_note: "Review the full evidence before making your own decision.",
    ...overrides,
  };
}

function source(overrides: Partial<PolicyEvaluation> = {}): PolicyEvaluation {
  const candidate = {
    candidate_id: "00000000-0000-4000-8000-000000000123",
    ticker: "ABC",
    phase: "intraday" as const,
    action: "buy" as const,
    notification_kind: "entry_trigger" as const,
    decision_mode: "discretionary" as const,
    bucket: "growth" as const,
    depth: "full" as const,
    confidence: "medium" as const,
    confidence_reason: "Multiple fresh evidence types agree.",
    health_score: "74",
    observed_price: "42.1",
    observed_quote_as_of: "2026-09-03T17:14:51.000Z",
    proposed_amount: "500",
    proposed_shares: "11.876484",
    entry_zone_low: "41.8",
    entry_zone_high: "42.3",
    stop: "39.75",
    target: "47.2",
    invalidation_price: "39.9",
    valid_until: "2026-09-09",
    evidence: [{
      id: "quote-1",
      kind: "quote" as const,
      source: "yahoo-chart",
      status: "fresh" as const,
      observed_at: "2026-09-03T17:14:51.000Z",
      retrieved_at: "2026-09-03T17:15:00.000Z",
      reference: "https://finance.yahoo.com/quote/ABC",
      claims: ["Fictional preview quote only"],
    }],
    factors: [{
      kind: "technicals" as const,
      stance: "bull" as const,
      text: "Price and participation confirm the previously reviewed setup.",
      evidence_ids: ["quote-1"],
    }],
    analyst: { completed: true, action: "buy" as const, confidence: "medium" as const, reason: "Fixture" },
    checker: { completed: true, verdict: "approve" as const, reason_codes: [], reason: "Fixture" },
    decisive_factor: "Fixture only",
    invalidation: "Fixture only",
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
      ticker: "ABC",
      verified_price: "42.1",
      quote_as_of: "2026-09-03T17:14:51.000Z",
      quote_source: "yahoo-chart",
      position_value_after: "500",
      total_investable_value: "12000",
      dollars_at_risk: "27.91",
      reward_risk_milli: "2150",
    },
    holding_state_change: null,
    candidate,
    ...overrides,
  };
}

function evaluation(alertRule: AlertRuleSnapshot, overrides: Partial<AlertEvaluation> = {}): AlertEvaluation {
  return {
    rule: alertRule,
    status: "triggered",
    reason_codes: [],
    observed_at: "2026-09-03T17:14:51.000Z",
    evaluated_at: "2026-09-03T17:15:04.000Z",
    market_session: "regular",
    condition_results: [{
      condition: alertRule.conditions[0],
      passed: true,
      observed_value: "42.1",
      evidence_ids: ["quote-1"],
    }],
    ...overrides,
  };
}

function holdingContext(): PolicyContext {
  return {
    holdings: [{
      ticker: "ABC",
      shares: "25",
      avg_cost: "43.2",
      bucket: "growth",
      stop: "39.75",
      target: "47.2",
      high_water_price: "44",
      hold_override_until: null,
      stop_alert_active: false,
      stop_near_alert_active: false,
      target_near_alert_active: false,
      target_alert_active: false,
    }],
    holding_quotes: {},
    realized_pnl_today: null,
    portfolio_command_coverage_complete: true,
    consecutive_completed_losses: 0,
    owner_plans: [],
  };
}

function fixture(name: FixtureName): RenderAlertV3Input {
  if (name === "draft") {
    const draftRule = rule({ state: "draft", version: 1, severity: "watch" });
    return {
      event_id: draftRule.rule_id,
      evaluation: evaluation(draftRule, { status: "not_triggered" }),
      source_evaluation: source(),
      context: null,
      mode: "draft",
    };
  }
  if (name === "stop-breach") {
    const stopRule = rule({
      version: 2,
      severity: "critical",
      conditions: [{ kind: "recorded_stop", operator: "below", left: "39.75", right: null, timeframe: "quote" }],
    });
    const stopSource = source();
    stopSource.raw_action = "hold";
    stopSource.final_action = "hold";
    stopSource.candidate.action = "hold";
    stopSource.candidate.notification_kind = "stop_breach";
    stopSource.candidate.stop = null;
    stopSource.candidate.target = null;
    stopSource.normalized.verified_price = "39.68";
    stopSource.normalized.quote_as_of = "2026-09-03T15:42:01.000Z";
    return {
      event_id: "91d070bf-5cec-4f1e-9de8-ec8823d99fc7",
      evaluation: evaluation(stopRule, {
        observed_at: "2026-09-03T15:42:01.000Z",
        evaluated_at: "2026-09-03T15:42:18.000Z",
        condition_results: [{ condition: stopRule.conditions[0], passed: true, observed_value: "39.68", evidence_ids: ["quote-1"] }],
      }),
      source_evaluation: stopSource,
      context: holdingContext(),
    };
  }
  if (name === "data-warning") {
    const unsafeRule = rule({ severity: "system" });
    return {
      event_id: "aa2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
      evaluation: evaluation(unsafeRule, {
        status: "unsafe_to_evaluate",
        reason_codes: ["EVIDENCE_STALE"],
        condition_results: [{ condition: unsafeRule.conditions[0], passed: null, observed_value: null, evidence_ids: [] }],
      }),
      source_evaluation: null,
      context: null,
    };
  }
  return {
    event_id: "7f2c70bf-5cec-4f1e-9de8-ec8823d99fc7",
    evaluation: evaluation(rule()),
    source_evaluation: source(),
    context: holdingContext(),
  };
}

const fixtureFlag = Deno.args.indexOf("--fixture");
const fixtureName = fixtureFlag >= 0 ? Deno.args[fixtureFlag + 1] : "entry-review";
if (!["draft", "entry-review", "stop-breach", "data-warning"].includes(fixtureName)) {
  throw new Error("fixture must be draft, entry-review, stop-breach, or data-warning");
}
const rendered = await renderAlertV3(fixture(fixtureName as FixtureName));
console.log(JSON.stringify({
  fixture: fixtureName,
  status: rendered.status,
  template_version: rendered.template_version,
  hash: rendered.hash,
  body: rendered.body,
  reply_markup: rendered.reply_markup,
}, null, 2));
