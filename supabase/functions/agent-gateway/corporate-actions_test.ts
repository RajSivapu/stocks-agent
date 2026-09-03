import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  assessCorporateAction,
  suppressPriceAlerts,
  type CorporateActionInput,
} from "./corporate-actions.ts";

function input(overrides: Partial<CorporateActionInput> = {}): CorporateActionInput {
  return {
    ticker: "NVDA",
    held: true,
    allowedSourceVerified: false,
    allowedSourceEvent: null,
    historySplitRatios: [],
    largestRawAdjustedGapBps: 0,
    issuerOrExchangeCheckedAt: "2026-09-02T17:05:00.000Z",
    ...overrides,
  };
}

Deno.test("an allowed primary-source split can be normalized", () => {
  const result = assessCorporateAction(input({
    allowedSourceVerified: true,
    allowedSourceEvent: {
      type: "split", effectiveDate: "2026-08-31", ratio: "10", reference: "issuer:event-1",
    },
  }));
  assertEquals(result, {
    state: "clear",
    eventType: "split",
    ratio: "10",
    reason: "confirmed_and_normalized",
  });
  assertEquals(suppressPriceAlerts(result), false);
});

Deno.test("a held symbol with an unverified split enters needs-review quarantine", () => {
  const result = assessCorporateAction(input({ historySplitRatios: ["10"] }));
  assertEquals(result.state, "needs_review");
  assertEquals(result.reason, "unverified_corporate_action");
  assertEquals(suppressPriceAlerts(result), true);
});

Deno.test("large discontinuity plus issuer check is only suspected and never auto-cleared", () => {
  const held = assessCorporateAction(input({ largestRawAdjustedGapBps: 4200 }));
  assertEquals(held.state, "needs_review");
  const unheld = assessCorporateAction(input({ held: false, largestRawAdjustedGapBps: 4200 }));
  assertEquals(unheld.state, "suspected");
});

Deno.test("conflicting primary-source events fail closed", () => {
  const result = assessCorporateAction(input({
    allowedSourceVerified: true,
    allowedSourceEvent: {
      type: "split", effectiveDate: "2026-08-31", ratio: "2", reference: "issuer:event-2",
    },
    historySplitRatios: ["10"],
  }));
  assertEquals(result.state, "needs_review");
  assertEquals(result.reason, "corporate_action_conflict");
});
