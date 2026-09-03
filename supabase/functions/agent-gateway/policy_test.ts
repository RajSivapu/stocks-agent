import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import type { DecisionCandidate } from "../market-briefing-gateway/_shared/contracts.ts";
import { applyCorporateActionQuarantine, outcomeDoesNotUpgrade } from "./policy.ts";

function candidate(): DecisionCandidate {
  return {
    candidate_id: crypto.randomUUID(), ticker: "TSTAAA", phase: "intraday", action: "buy",
    notification_kind: "entry_trigger", decision_mode: "discretionary", bucket: "growth",
    depth: "full", confidence: "high", confidence_reason: "Current evidence.", health_score: null,
    observed_price: "10", observed_quote_as_of: new Date().toISOString(), proposed_amount: "100",
    proposed_shares: "10", entry_zone_low: "9", entry_zone_high: "11", stop: "8", target: "15",
    invalidation_price: "8", valid_until: "2026-09-03", evidence: [], factors: [],
    analyst: { completed: true, action: "buy", confidence: "high", reason: "Evidence." },
    checker: { completed: true, verdict: "approve", reason_codes: [], reason: "Checked." },
    decisive_factor: "Valuation", invalidation: "Break below 8", prior_suggestion_ids: [],
  };
}

Deno.test("policy outcomes can downgrade or veto but never upgrade", () => {
  assertEquals(outcomeDoesNotUpgrade("watch", "buy"), false);
  assertEquals(outcomeDoesNotUpgrade("buy", "watch"), true);
  assertEquals(outcomeDoesNotUpgrade("avoid", "avoid"), true);
});

Deno.test("corporate-action uncertainty suppresses price-level alerts", () => {
  const quarantined = applyCorporateActionQuarantine(candidate(), "needs_review");
  assertEquals(quarantined.action, "watch");
  assertEquals(quarantined.notification_kind, "data_warning");
  assertEquals(quarantined.confidence, "low");
});
