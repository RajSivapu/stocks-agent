import {
  parseArtifactMutationBatch,
  parseDecisionBundle,
  parseGatewayEnvelope,
  type Phase,
} from "./contracts.ts";

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

function assertThrows(fn: () => unknown, message: string): void {
  try {
    fn();
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes(message)) {
      throw new Error(
        `expected error containing ${message}, got ${String(error)}`,
      );
    }
    return;
  }
  throw new Error(`expected error containing ${message}`);
}

function validCandidate(ticker = "CENX", phase: Phase = "on-demand") {
  return {
    candidate_id: "00000000-0000-4000-8000-000000000010",
    ticker,
    phase,
    action: "buy",
    notification_kind: "brief",
    decision_mode: "discretionary",
    bucket: "growth",
    depth: "full",
    confidence: "medium",
    confidence_reason: "Evidence is current but cyclical risk remains.",
    health_score: "72",
    observed_price: "47.02",
    observed_quote_as_of: "2026-09-02T17:00:00.000Z",
    proposed_amount: "2057.04",
    proposed_shares: "43.748192",
    entry_zone_low: "45",
    entry_zone_high: "47.02",
    stop: "42",
    target: "58",
    invalidation_price: "41.5",
    valid_until: "2026-09-09",
    evidence: [{
      id: "quote-1",
      kind: "quote",
      source: "yahoo",
      status: "fresh",
      observed_at: "2026-09-02T17:00:00.000Z",
      retrieved_at: "2026-09-02T17:00:01.000Z",
      reference: "https://example.invalid/untrusted",
      claims: ["Price observed at 47.02."],
      exposure_kind: "filing",
    }],
    factors: [{
      kind: "risk",
      stance: "bear",
      text: "Cyclical aluminum pricing remains the main risk.",
      evidence_ids: ["quote-1"],
    }],
    analyst: {
      id: "00000000-0000-4000-8000-000000000020",
      packet_id: "00000000-0000-4000-8000-000000000030",
      completed: true,
      action: "buy",
      confidence: "medium",
      reason: "Valuation and demand support the thesis.",
    },
    checker: {
      id: "00000000-0000-4000-8000-000000000021",
      analyst_id: "00000000-0000-4000-8000-000000000020",
      completed: true,
      verdict: "approve",
      reason_codes: [],
      reason: "Required evidence is present.",
    },
    relationship_type: "direct",
    decisive_factor: "Demand durability.",
    invalidation: "Demand or margins weaken materially.",
    prior_suggestion_ids: [],
  };
}

function fixturePacket() {
  return {
    candidates: [{ candidate_key: "CENX", evidence_ids: ["quote-1"] }],
    evidence: [{
      item_id: "quote-1",
      normalized_text: "Price observed at 47.02.",
    }],
    coverage: { mode: "fixture_dry_run", complete_market_coverage: false },
    limitations: ["fixture_only_no_external_coverage"],
    policy_version: 1,
  };
}

function validBundle(phase: Phase = "on-demand") {
  return {
    phase,
    market_date: "2026-09-02",
    title: "Owner research request",
    candidates: [validCandidate("CENX", phase)],
  };
}

function validEnvelope() {
  return {
    schema_version: 1,
    operation: "evaluate_and_publish",
    request_id: "00000000-0000-4000-8000-000000000001",
    run_id: "00000000-0000-4000-8000-000000000002",
    dry_run: false,
    payload: validBundle(),
  };
}

Deno.test("gateway envelope accepts one complete decision bundle", () => {
  const parsed = parseGatewayEnvelope(validEnvelope());
  assertEquals(parsed.operation, "evaluate_and_publish");
  assertEquals(
    parseDecisionBundle(parsed.payload, "on-demand").candidates[0].ticker,
    "CENX",
  );
});

Deno.test("decision bundle parses an exact intelligence packet reference and bounded fixture", () => {
  const live = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "complete_for_plan",
    },
  });
  assertEquals(
    parseDecisionBundle(live, "intraday").intelligence_packet!.coverage,
    "complete_for_plan",
  );

  const dry = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "fixture_dry_run",
      packet: fixturePacket(),
    },
  });
  assertEquals(
    parseDecisionBundle(dry, "intraday").intelligence_packet!.packet?.candidates
      .length,
    1,
  );
});

Deno.test("inline intelligence packet enforces candidate, evidence, and byte bounds", () => {
  const bundle = Object.assign(validBundle("intraday"), {
    intelligence_packet: {
      id: "00000000-0000-4000-8000-000000000030",
      content_hash: "a".repeat(64),
      coverage: "fixture_dry_run",
      packet: fixturePacket(),
    },
  });
  bundle.intelligence_packet.packet.candidates[0].evidence_ids = Array.from({
    length: 9,
  }, (_, index) => `e-${index}`);
  assertThrows(() => parseDecisionBundle(bundle, "intraday"), "at most 8");

  const oversized = structuredClone(bundle);
  oversized.intelligence_packet.packet.candidates[0].evidence_ids = ["quote-1"];
  (oversized.intelligence_packet.packet as {
    coverage: Record<string, unknown>;
  }).coverage = { padding: "x".repeat(98_304) };
  assertThrows(() => parseDecisionBundle(oversized, "intraday"), "96 KiB");
});

Deno.test("gateway envelope accepts the bounded standalone alert evaluation operation", () => {
  const envelope = {
    ...validEnvelope(),
    operation: "evaluate_alert_rules",
    run_id: null,
    payload: {},
  };
  assertEquals(
    parseGatewayEnvelope(envelope).operation,
    "evaluate_alert_rules",
  );
});

Deno.test("gateway envelope accepts only the two scoped intelligence persistence operations", () => {
  const start = {
    ...validEnvelope(),
    operation: "start_intelligence_run",
    run_id: null,
    payload: {
      phase: "on-demand",
      market_date: "2026-09-02",
      policy_version: 1,
      reservation_plan: { reservations: [] },
    },
  };
  assertEquals(parseGatewayEnvelope(start).operation, "start_intelligence_run");
});

Deno.test("gateway envelope rejects unknown and extra authority fields", () => {
  const unknownOperation = validEnvelope();
  unknownOperation.operation = "send_telegram";
  assertThrows(() => parseGatewayEnvelope(unknownOperation), "operation");

  const withTable = { ...validEnvelope(), table: "holdings" };
  assertThrows(() => parseGatewayEnvelope(withTable), "unexpected key");
});

Deno.test("gateway envelope accepts only a bounded review-only learning record", () => {
  const observation = {
    status: "owner_review",
    evidence_ids: ["00000000-0000-4000-8000-000000000010"],
    limitations: ["Historical outcomes do not prove future performance."],
    metrics: { false_positive_rate: "0.1667" },
    proposed_change: {
      area: "candidate_ranking_review",
      recommendation: "review_false_positive_rate",
      false_positive_rate: "0.1667",
    },
  };
  const envelope = {
    ...validEnvelope(),
    operation: "record_learning",
    payload: {
      id: "00000000-0000-4000-8000-000000000040",
      policy_version: 1,
      observation_type: "outcome",
      horizon_days: 21,
      sample_size: 6,
      benchmark: "VOO",
      observation,
      content_hash: "a".repeat(64),
    },
  };

  assertEquals(parseGatewayEnvelope(envelope).operation, "record_learning");

  const executable = structuredClone(envelope);
  (executable.payload.observation as Record<string, unknown>).apply = true;
  assertThrows(() => parseGatewayEnvelope(executable), "unexpected key");

  const mutationTarget = structuredClone(envelope);
  (mutationTarget.payload.observation.proposed_change as Record<string, unknown>)
    .operation = "update_policy";
  assertThrows(() => parseGatewayEnvelope(mutationTarget), "unexpected key");
});

Deno.test("learning record rejects unsupported horizons, kinds, and unbounded evidence", () => {
  const base = {
    ...validEnvelope(),
    operation: "record_learning",
    payload: {
      id: "00000000-0000-4000-8000-000000000040",
      policy_version: 1,
      observation_type: "noise",
      horizon_days: 5,
      sample_size: 6,
      benchmark: "VOO",
      observation: {
        status: "observation",
        evidence_ids: [],
        limitations: [],
        metrics: { false_positive_rate: "0.1667" },
        proposed_change: null,
      },
      content_hash: "a".repeat(64),
    },
  };
  const horizon = structuredClone(base);
  horizon.payload.horizon_days = 10;
  assertThrows(() => parseGatewayEnvelope(horizon), "horizon");

  const kind = structuredClone(base);
  kind.payload.observation_type = "policy-update";
  assertThrows(() => parseGatewayEnvelope(kind), "observation_type");

  const evidence = structuredClone(base);
  (evidence.payload.observation.evidence_ids as string[]) = Array.from(
    { length: 97 },
    () => "00000000-0000-4000-8000-000000000010",
  );
  assertThrows(() => parseGatewayEnvelope(evidence), "at most 96");
});

Deno.test("gateway envelope rejects invalid identifiers and decimal JSON numbers", () => {
  const invalidId = validEnvelope();
  invalidId.request_id = "not-a-uuid";
  assertThrows(() => parseGatewayEnvelope(invalidId), "request_id");

  const numericPrice = validEnvelope();
  (numericPrice.payload.candidates[0] as Record<string, unknown>)
    .entry_zone_high = 47.02;
  const parsed = parseGatewayEnvelope(numericPrice);
  assertThrows(
    () => parseDecisionBundle(parsed.payload, "on-demand"),
    "decimal string",
  );
});

Deno.test("decision bundle rejects lowercase tickers and duplicate evidence", () => {
  const lowercase = validBundle();
  lowercase.candidates[0].ticker = "cenx";
  assertThrows(() => parseDecisionBundle(lowercase, "on-demand"), "ticker");

  const duplicateEvidence = validBundle();
  duplicateEvidence.candidates[0].evidence.push({
    ...duplicateEvidence.candidates[0].evidence[0],
  });
  assertThrows(
    () => parseDecisionBundle(duplicateEvidence, "on-demand"),
    "duplicate evidence id",
  );
});

Deno.test("decision bundle enforces phase and per-phase candidate limits", () => {
  assertThrows(
    () => parseDecisionBundle(validBundle("pre-market"), "on-demand"),
    "phase",
  );

  const oversized = validBundle();
  oversized.candidates = Array.from({ length: 11 }, (_, index) => ({
    ...validCandidate(`T${index}`, "on-demand"),
    candidate_id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
  }));
  assertThrows(
    () => parseDecisionBundle(oversized, "on-demand"),
    "candidate limit",
  );
});

Deno.test("decision bundle permits only one candidate per ticker", () => {
  const duplicateTicker = validBundle();
  duplicateTicker.candidates.push({
    ...validCandidate(),
    candidate_id: "00000000-0000-4000-8000-000000000011",
  });
  assertThrows(
    () => parseDecisionBundle(duplicateTicker, "on-demand"),
    "duplicate ticker",
  );
});

Deno.test("decision bundle accepts bounded evidence-linked portfolio comparisons", () => {
  const bundle = validBundle("on-demand");
  bundle.candidates.push({
    ...validCandidate("ITOT", "on-demand"),
    candidate_id: "00000000-0000-4000-8000-000000000011",
    evidence: [{
      ...validCandidate().evidence[0],
      id: "itot-profile",
      status: "fresh",
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "The fund covers the broad U.S. equity market.",
      evidence_ids: ["itot-profile"],
    }],
  });
  Object.assign(bundle, {
    comparisons: [{
      baseline_ticker: "CENX",
      alternative_ticker: "ITOT",
      relationship: "tilt",
      prospective_view: "stronger",
      reason:
        "The broader fund would reduce single-company concentration risk.",
      evidence_ids: ["itot-profile"],
    }],
  });
  const parsed = parseDecisionBundle(bundle, "on-demand");
  assertEquals(parsed.comparisons?.[0].alternative_ticker, "ITOT");
  assertEquals(parsed.comparisons?.[0].relationship, "tilt");
});

Deno.test("portfolio comparisons reject unknown tickers, evidence, duplicates, and scheduled noise", () => {
  const base = validBundle("on-demand") as ReturnType<typeof validBundle> & {
    comparisons?: unknown[];
  };
  base.comparisons = [{
    baseline_ticker: "CENX",
    alternative_ticker: "ITOT",
    relationship: "peer",
    prospective_view: "similar",
    reason: "Evidence-linked comparison.",
    evidence_ids: ["missing"],
  }];
  assertThrows(
    () => parseDecisionBundle(base, "on-demand"),
    "comparison ticker",
  );

  const noisy = validBundle("intraday") as ReturnType<typeof validBundle> & {
    comparisons?: unknown[];
  };
  noisy.comparisons = [];
  assertThrows(
    () => parseDecisionBundle(noisy, "intraday"),
    "comparisons are limited",
  );
});

function companionBundle(phase: Phase = "on-demand") {
  const bundle = validBundle(phase);
  bundle.candidates[0].ticker = "VTI";
  bundle.candidates.push({
    ...validCandidate("VXUS", phase),
    candidate_id: "00000000-0000-4000-8000-000000000012",
    evidence: [{
      ...validCandidate().evidence[0],
      id: "vxus-profile",
      kind: "fundamentals",
      status: "fresh",
    }],
    factors: [{
      kind: "fundamentals",
      stance: "neutral",
      text: "The fund covers developed and emerging non-U.S. equity markets.",
      evidence_ids: ["vxus-profile"],
    }],
  });
  return Object.assign(bundle, {
    comparisons: [{
      baseline_ticker: "VTI",
      alternative_ticker: "VXUS",
      relationship: "diversifier",
      prospective_view: "similar",
      reason: "The candidate adds a distinct geographic role.",
      evidence_ids: ["vxus-profile"],
    }],
    companion_proposal: {
      baseline_ticker: "VTI",
      companion_ticker: "VXUS",
      role: "diversifier",
      thesis: "Non-U.S. exposure adds a distinct geographic role.",
      risk_note:
        "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
      evidence_ids: ["vxus-profile"],
    },
  });
}

Deno.test("decision bundle accepts one evidence-linked long-term companion nomination", () => {
  const parsed = parseDecisionBundle(companionBundle(), "on-demand");
  assertEquals(parsed.companion_proposal, {
    baseline_ticker: "VTI",
    companion_ticker: "VXUS",
    role: "diversifier",
    thesis: "Non-U.S. exposure adds a distinct geographic role.",
    risk_note:
      "Currency and foreign-market risks can cause long periods of lagging U.S. stocks.",
    evidence_ids: ["vxus-profile"],
  });
});

Deno.test("long-term companion requires a valid pair, evidence, phase, and additive role", () => {
  const unknownTicker = companionBundle();
  unknownTicker.companion_proposal.companion_ticker = "UNKNOWN";
  assertThrows(
    () => parseDecisionBundle(unknownTicker, "on-demand"),
    "companion ticker",
  );

  const absentPair = companionBundle();
  absentPair.comparisons = [];
  assertThrows(
    () => parseDecisionBundle(absentPair, "on-demand"),
    "matching portfolio comparison",
  );

  const unknownEvidence = companionBundle();
  unknownEvidence.companion_proposal.evidence_ids = ["missing"];
  assertThrows(
    () => parseDecisionBundle(unknownEvidence, "on-demand"),
    "companion evidence",
  );

  const intraday = companionBundle("intraday");
  assertThrows(
    () => parseDecisionBundle(intraday, "intraday"),
    "comparisons are limited",
  );

  const substitute = companionBundle();
  substitute.companion_proposal.role = "like_for_like";
  assertThrows(
    () => parseDecisionBundle(substitute, "on-demand"),
    "companion role",
  );
});

Deno.test("artifact parser accepts bounded paper-watch input", () => {
  assertEquals(
    parseArtifactMutationBatch({
      mutations: [{
        kind: "paper_watch_create",
        ticker: "MP",
        entry_ref_price: "74.25",
        target_price: null,
        hypothetical_amount: "500",
        thesis: "Domestic magnet capacity may expand.",
        horizon: "three months",
      }],
    }),
    {
      mutations: [{
        kind: "paper_watch_create",
        ticker: "MP",
        entry_ref_price: "74.25",
        target_price: null,
        hypothetical_amount: "500",
        thesis: "Domestic magnet capacity may expand.",
        horizon: "three months",
      }],
    },
  );
});

Deno.test("artifact parser rejects dynamic tables and caller-owned close fields", () => {
  assertThrows(
    () =>
      parseArtifactMutationBatch({
        mutations: [{ kind: "radar_delete", ticker: "MP", table: "holdings" }],
      }),
    "unexpected key",
  );
  assertThrows(
    () =>
      parseArtifactMutationBatch({
        mutations: [{
          kind: "paper_watch_close",
          watch_id: 1,
          ticker: "MP",
          close_price: "99",
        }],
      }),
    "unexpected key",
  );
});
