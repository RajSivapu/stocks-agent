/// <reference lib="deno.ns" />

import { parseAnalysisSubmissionV2 } from "./provider.ts";

function assertThrows(callback: () => unknown, expected: string): void {
  try {
    callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(expected)) return;
    throw error;
  }
  throw new Error(`expected error containing ${expected}`);
}

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const DIMENSIONS = [
  "fundamentals", "valuation", "catalyst", "technical", "portfolio_fit", "downside",
  "bear_case", "invalidation", "decisive_factor",
] as const;

export function analysisSubmission(): Record<string, any> {
  return {
    phase: "intraday",
    market_date: "2026-09-02",
    title: "Independent intraday review",
    suggestion_only: true,
    provider: "claude",
    model: "configured-by-owner",
    analyst: { completed: true, action: "watch", confidence: "medium", thesis: "Evidence is mixed." },
    checker: { completed: true, verdict: "approve", reason: "No unsupported conclusion found." },
    dimensions: Object.fromEntries(DIMENSIONS.map((name) => [name, {
      status: "supported",
      summary: `${name} was reviewed.`,
      evidence_ids: ["quote-current", "search-current"],
    }])),
    evidence_refs: [
      { evidence_id: "quote-current", run_id: RUN_ID, content_hash: "a".repeat(64) },
      { evidence_id: "search-current", run_id: RUN_ID, content_hash: "b".repeat(64) },
    ],
    prior_suggestion_ids: [],
    candidates: [],
  };
}

Deno.test("analysis submission requires every independent analytical dimension", () => {
  const parsed = parseAnalysisSubmissionV2(analysisSubmission());
  if (!parsed.analyst.completed || !parsed.checker.completed) throw new Error("reviews were lost");
  const missing = analysisSubmission();
  delete (missing.dimensions as Record<string, unknown>).bear_case;
  assertThrows(() => parseAnalysisSubmissionV2(missing), "missing field");
});

Deno.test("analysis submission cannot fabricate server quote persistence or delivery authority", () => {
  for (const forbidden of [
    { server_quote: { price: "47.02" } },
    { persistence_receipt: { writes: 1 } },
    { publication_receipt: { delivered: true } },
    { write_counts: { suggestions: 1 } },
  ]) {
    const value = analysisSubmission();
    value.candidates = [forbidden];
    assertThrows(() => parseAnalysisSubmissionV2(value), "server authority");
  }
});

Deno.test("analysis submission requires suggestion-only declaration and exact evidence references", () => {
  assertThrows(
    () => parseAnalysisSubmissionV2({ ...analysisSubmission(), suggestion_only: false }),
    "suggestion_only",
  );
  const changed = analysisSubmission();
  changed.evidence_refs.push({ ...changed.evidence_refs[0], content_hash: "c".repeat(64) });
  assertThrows(() => parseAnalysisSubmissionV2(changed), "duplicate evidence");
  assertThrows(
    () => parseAnalysisSubmissionV2({ ...analysisSubmission(), market_date: "2026-02-30" }),
    "market_date",
  );
});
