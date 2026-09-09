import { parseIntelligenceView } from "./dashboard-contracts.ts";
import parityCases from "../../../tests/fixtures/owner_intelligence_contract_cases.json" with { type: "json" };

function projection(dataAsOf: string | null = null): Record<string, unknown> {
  return {
    intelligence_version: 2,
    run_id: null,
    data_as_of: dataAsOf,
    themes: [],
    companies: [],
    evidence: [],
    source_health: [],
    coverage: { mode: "bounded", complete_market_coverage: false },
    reference: { state: "unavailable" },
    scope: { research_only: true, market_wide: true },
    backlog: { available: 0, returned: 0, deferred: 0, byte_truncated: false },
    omissions: [],
    boundaries: {
      research_only: true,
      execution_disabled: true,
      valuation_unavailable: true,
    },
  };
}

function theme(mechanism: string): Record<string, unknown> {
  return {
    theme_id: "data_center_power",
    episode_id: "11111111-1111-4111-8111-111111111111",
    revision_id: "22222222-2222-4222-8222-222222222222",
    revision: 1,
    mechanism,
    subject: "US data centers",
    jurisdiction: "US",
    state: "active",
    first_seen: "2026-09-08T12:00:00Z",
    last_seen: "2026-09-08T12:00:00.123456+00:00",
    next_review_at: "2026-09-09T12:00:00-05:00",
    expires_at: "2026-10-08T12:00:00Z",
    adverse_evidence_count: 0,
    missing_questions: [],
    invalidation_conditions: [],
  };
}

Deno.test("intelligence parser matches the shared cross-runtime acceptance corpus", () => {
  for (const testCase of parityCases) {
    const value = projection();
    let target: Record<string, unknown> = value;
    for (const key of testCase.path.slice(0, -1)) {
      target = target[key] as Record<string, unknown>;
    }
    if (testCase.path.length > 0) {
      target[testCase.path.at(-1)!] = testCase.value;
    }
    let accepted = true;
    try {
      parseIntelligenceView(value);
    } catch {
      accepted = false;
    }
    if (accepted !== testCase.accepted) {
      throw new Error(`parity case ${testCase.name}: expected ${testCase.accepted}, got ${accepted}`);
    }
  }
});

Deno.test("intelligence bounded strings count JavaScript UTF-16 units", () => {
  const accepted = projection();
  accepted.themes = [theme("😀".repeat(120))];
  parseIntelligenceView(accepted);

  const rejected = projection();
  rejected.themes = [theme("😀".repeat(121))];
  let failed = false;
  try {
    parseIntelligenceView(rejected);
  } catch {
    failed = true;
  }
  if (!failed) throw new Error("expected overlong astral string to be rejected");
});

Deno.test("intelligence timestamps accept only canonical valid database shapes", () => {
  for (const value of [
    "2026-09-08T12:00:00Z",
    "2026-09-08T12:00:00.123456+00:00",
    "2024-02-29T23:59:59-05:30",
  ]) {
    parseIntelligenceView(projection(value));
  }
  for (const value of [
    "2026-W01-1",
    "2026-02-30T12:00:00Z",
    "2026-09-08 12:00:00Z",
    "0000-01-01T00:00:00Z",
    "2026-09-08T24:00:00Z",
    "2026-09-08T12:00:00+24:00",
  ]) {
    let failed = false;
    try {
      parseIntelligenceView(projection(value));
    } catch {
      failed = true;
    }
    if (!failed) throw new Error(`expected invalid timestamp to be rejected: ${value}`);
  }
});
