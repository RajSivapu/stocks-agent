import {
  alertFingerprint,
  alertRuleFingerprint,
  evaluateAlertRule,
  parseAlertDraft,
  shouldPublishAlert,
} from "./alerts.ts";
import type {
  AlertConditionEvidence,
  AlertRuleSnapshot,
} from "./contracts.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function assertThrows(fn: () => unknown, includes: string): void {
  let thrown = false;
  try {
    fn();
  } catch (error) {
    thrown = true;
    assert(error instanceof Error && error.message.includes(includes),
      `expected error containing ${includes}, got ${String(error)}`);
  }
  assert(thrown, `expected error containing ${includes}`);
}

const RULE_ID = "11111111-1111-4111-8111-111111111111";

function rule(overrides: Partial<AlertRuleSnapshot> = {}): AlertRuleSnapshot {
  return {
    rule_id: RULE_ID,
    version: 3,
    state: "active",
    ticker: "ABC",
    profile: "balanced",
    severity: "review",
    session: "regular",
    confirmation: "bar_close",
    conditions: [{
      kind: "price_zone",
      operator: "inside",
      left: "41.8",
      right: "42.3",
      timeframe: "15m",
    }],
    cooldown_seconds: 14_400,
    fire_limit: 3,
    valid_until: "2026-09-09T21:00:00.000Z",
    owner_note: "Wait for participation before review.",
    ...overrides,
  };
}

function evidence(
  overrides: Partial<AlertConditionEvidence> = {},
): AlertConditionEvidence[] {
  return [{
    condition_index: 0,
    status: "fresh",
    market_session: "regular",
    evidence_ids: ["quote-1"],
    points: [{
      value: "42",
      comparison_value: null,
      observed_at: "2026-09-03T17:14:51.000Z",
      bar_complete: true,
    }],
    ...overrides,
  }];
}

Deno.test("alert draft parser accepts the exact bounded owner-only contract", () => {
  const parsed = parseAlertDraft(rule());
  assert(parsed.rule_id === RULE_ID, "rule id changed");
  assert(parsed.ticker === "ABC", "ticker changed");
  assert(parsed.conditions.length === 1, "condition count changed");

  assertThrows(() => parseAlertDraft({ ...rule(), broker_url: "https://broker.invalid" }),
    "unknown field");
  assertThrows(() => parseAlertDraft({ ...rule(), ticker: "abc" }), "ticker");
  assertThrows(() => parseAlertDraft({ ...rule(), profile: "scalper" }), "profile");
  assertThrows(() => parseAlertDraft({ ...rule(), session: "overnight" }), "session");
  assertThrows(() => parseAlertDraft({ ...rule(), owner_note: "x".repeat(501) }), "owner_note");
  assertThrows(() => parseAlertDraft({ ...rule(), cooldown_seconds: 0 }), "cooldown_seconds");
  assertThrows(() => parseAlertDraft({ ...rule(), fire_limit: 101 }), "fire_limit");
  assertThrows(() => parseAlertDraft({ ...rule(), conditions: [] }), "conditions");
  assertThrows(() => parseAlertDraft({ ...rule(), conditions: Array(6).fill(rule().conditions[0]) }),
    "conditions");
});

Deno.test("alert condition parser rejects noncanonical decimals and invalid combinations", () => {
  const base = rule().conditions[0];
  assertThrows(() => parseAlertDraft(rule({
    conditions: [{ ...base, left: "041.80" }],
  })), "left");
  assertThrows(() => parseAlertDraft(rule({
    conditions: [{ ...base, left: "42.3", right: "41.8" }],
  })), "range");
  assertThrows(() => parseAlertDraft(rule({
    conditions: [{ ...base, timeframe: "5m" as "15m" }],
  })), "timeframe");
  assertThrows(() => parseAlertDraft(rule({
    confirmation: "two_quote",
    conditions: [{ kind: "rsi_range", operator: "inside", left: "50", right: "65", timeframe: "15m" }],
  })), "two_quote");
});

Deno.test("completed-bar ALL evaluation triggers only when every condition passes", () => {
  const multi = rule({
    conditions: [
      rule().conditions[0],
      { kind: "volume_multiple", operator: "above", left: "1.7", right: null, timeframe: "15m" },
      { kind: "rsi_range", operator: "inside", left: "50", right: "65", timeframe: "15m" },
    ],
  });
  const inputs: AlertConditionEvidence[] = [
    evidence()[0],
    evidence({ condition_index: 1, evidence_ids: ["volume-1"], points: [{
      value: "1.8", comparison_value: null,
      observed_at: "2026-09-03T17:14:50.000Z", bar_complete: true,
    }] })[0],
    evidence({ condition_index: 2, evidence_ids: ["rsi-1"], points: [{
      value: "58", comparison_value: null,
      observed_at: "2026-09-03T17:14:49.000Z", bar_complete: true,
    }] })[0],
  ];
  const result = evaluateAlertRule(multi, inputs, new Date("2026-09-03T17:15:04.000Z"));
  assert(result.status === "triggered", "all passing conditions did not trigger");
  assert(result.condition_results.every((item) => item.passed === true), "pass results missing");

  inputs[1].points[0].value = "1.6";
  const quiet = evaluateAlertRule(multi, inputs, new Date("2026-09-03T17:15:04.000Z"));
  assert(quiet.status === "not_triggered", "partial ALL conditions triggered");
});

Deno.test("completed-bar evaluation fails safe for incomplete, stale, conflicting, or wrong-session input", () => {
  const now = new Date("2026-09-03T17:15:04.000Z");
  const cases: Array<[Partial<AlertConditionEvidence>, string]> = [
    [{ points: [{ value: "42", comparison_value: null, observed_at: "2026-09-03T17:14:51.000Z", bar_complete: false }] }, "BAR_INCOMPLETE"],
    [{ status: "stale" }, "EVIDENCE_STALE"],
    [{ status: "conflicting" }, "EVIDENCE_CONFLICTING"],
    [{ market_session: "pre_market" }, "SESSION_MISMATCH"],
    [{ points: [{ value: "42", comparison_value: null, observed_at: "2026-09-03T18:14:51.000Z", bar_complete: true }] }, "OBSERVATION_IN_FUTURE"],
  ];
  for (const [change, code] of cases) {
    const result = evaluateAlertRule(rule(), evidence(change), now);
    assert(result.status === "unsafe_to_evaluate", `${code} did not fail safe`);
    assert(result.reason_codes.includes(code), `${code} missing`);
    assert(result.condition_results[0].passed === null, `${code} exposed a pass`);
  }
});

Deno.test("two-quote confirmation requires two distinct confirmed raw-price observations", () => {
  const stop = rule({
    severity: "critical",
    confirmation: "two_quote",
    conditions: [{ kind: "recorded_stop", operator: "below", left: "39.75", right: null, timeframe: "quote" }],
  });
  const one = evidence({ points: [{
    value: "39.68", comparison_value: null,
    observed_at: "2026-09-03T15:42:01.000Z", bar_complete: false,
  }] });
  const unsafe = evaluateAlertRule(stop, one, new Date("2026-09-03T15:42:18.000Z"));
  assert(unsafe.status === "unsafe_to_evaluate", "one quote was accepted");
  assert(unsafe.reason_codes.includes("QUOTE_CONFIRMATION_MISSING"), "confirmation reason absent");

  one[0].points.push({
    value: "39.7", comparison_value: null,
    observed_at: "2026-09-03T15:42:14.000Z", bar_complete: false,
  });
  const triggered = evaluateAlertRule(stop, one, new Date("2026-09-03T15:42:18.000Z"));
  assert(triggered.status === "triggered", "two confirming quotes did not trigger");
});

Deno.test("price and SMA crosses require a real prior-side transition", () => {
  const cross = rule({
    conditions: [{ kind: "price_cross", operator: "above", left: "42", right: null, timeframe: "15m" }],
  });
  const crossed = evidence({ points: [
    { value: "41.9", comparison_value: null, observed_at: "2026-09-03T16:59:59.000Z", bar_complete: true },
    { value: "42.1", comparison_value: null, observed_at: "2026-09-03T17:14:59.000Z", bar_complete: true },
  ] });
  assert(evaluateAlertRule(cross, crossed, new Date("2026-09-03T17:15:04.000Z")).status === "triggered",
    "price cross was missed");
  crossed[0].points[0].value = "42.05";
  assert(evaluateAlertRule(cross, crossed, new Date("2026-09-03T17:15:04.000Z")).status === "not_triggered",
    "same-side prices fabricated a cross");

  const sma = rule({
    conditions: [{ kind: "sma_cross", operator: "above", left: "50", right: null, timeframe: "1d" }],
  });
  const smaEvidence = evidence({ points: [
    { value: "99", comparison_value: "100", observed_at: "2026-09-02T20:00:00.000Z", bar_complete: true },
    { value: "101", comparison_value: "100.5", observed_at: "2026-09-03T20:00:00.000Z", bar_complete: true },
  ] });
  assert(evaluateAlertRule(sma, smaEvidence, new Date("2026-09-03T20:01:00.000Z")).status === "triggered",
    "SMA cross was missed");
});

Deno.test("alert fingerprints are stable and bind rule version plus condition results", async () => {
  const now = new Date("2026-09-03T17:15:04.000Z");
  const evaluated = evaluateAlertRule(rule(), evidence(), now);
  const first = await alertFingerprint(rule(), evaluated);
  const reordered = parseAlertDraft({
    owner_note: rule().owner_note,
    valid_until: rule().valid_until,
    fire_limit: rule().fire_limit,
    cooldown_seconds: rule().cooldown_seconds,
    conditions: rule().conditions,
    confirmation: rule().confirmation,
    session: rule().session,
    severity: rule().severity,
    profile: rule().profile,
    ticker: rule().ticker,
    state: rule().state,
    version: rule().version,
    rule_id: rule().rule_id,
  });
  assert(first === await alertFingerprint(reordered, evaluated), "key order changed fingerprint");
  assert(first !== await alertFingerprint(rule({ version: 4 }), evaluated), "version did not bind fingerprint");
  assert(/^[0-9a-f]{64}$/.test(first), "fingerprint is not sha256 hex");
});

Deno.test("equivalent draft proposals share one semantic fingerprint", async () => {
  const first = rule({
    rule_id: "11111111-1111-4111-8111-111111111111",
    version: 1,
    state: "draft",
    valid_until: "2026-09-09T21:00:00.000Z",
  });
  const repeated = rule({
    rule_id: "22222222-2222-4222-8222-222222222222",
    version: 1,
    state: "draft",
    valid_until: "2026-09-10T21:00:00.000Z",
  });
  assert(await alertRuleFingerprint(first) === await alertRuleFingerprint(repeated),
    "generated identity or rolling expiry defeated draft deduplication");
  assert(await alertRuleFingerprint(first) !== await alertRuleFingerprint({
    ...repeated,
    conditions: [{ ...repeated.conditions[0], left: "41.9" }],
  }), "different conditions shared a fingerprint");
});

Deno.test("publication gate enforces state, expiry, fire limit, and cooldown", () => {
  const now = new Date("2026-09-03T17:15:04.000Z");
  const evaluated = evaluateAlertRule(rule(), evidence(), now);
  assert(shouldPublishAlert(rule(), evaluated, []), "fresh trigger was suppressed");
  assert(!shouldPublishAlert(rule({ state: "paused" }), evaluated, []), "paused rule published");
  assert(!shouldPublishAlert(rule({ valid_until: "2026-09-03T17:15:03.000Z" }), evaluated, []),
    "expired rule published");
  const recent = [{
    fingerprint: "a".repeat(64), status: "triggered" as const,
    evaluated_at: "2026-09-03T16:00:00.000Z", severity: "review" as const,
  }];
  assert(!shouldPublishAlert(rule(), evaluated, recent), "cooldown was ignored");
  assert(!shouldPublishAlert(rule({ fire_limit: 1 }), evaluated, recent), "fire limit was ignored");
});
