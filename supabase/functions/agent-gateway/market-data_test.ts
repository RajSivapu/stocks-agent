import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  assessServerQuote,
  ownerVisibleQuotes,
  type QuoteObservation,
  type TradingSession,
} from "./market-data.ts";

const SESSION: TradingSession = {
  marketDate: "2026-11-27",
  openAt: "2026-11-27T14:30:00.000Z",
  closeAt: "2026-11-27T18:00:00.000Z",
};

function quote(overrides: Partial<QuoteObservation> = {}): QuoteObservation {
  return {
    ticker: "VTI",
    price: "380.16",
    previousClose: "377.89",
    sourceTimestamp: "2026-11-27T17:58:00.000Z",
    retrievedAt: "2026-11-27T17:59:00.000Z",
    session: "REGULAR",
    provider: "yahoo-chart",
    adjustmentStatus: "raw",
    ...overrides,
  };
}

Deno.test("early-close post-market quote is fresh against the actual session close", () => {
  const result = assessServerQuote(
    "post-market",
    quote({
      session: "CLOSED",
      sourceTimestamp: "2026-11-27T18:00:00.000Z",
      retrievedAt: "2026-11-27T18:01:00.000Z",
    }),
    null,
    new Date("2026-11-27T18:10:00.000Z"),
    SESSION,
  );
  assertEquals(result.status, "fresh");
});

Deno.test("intraday quote status distinguishes delayed stale and unavailable", () => {
  assertEquals(assessServerQuote(
    "intraday", quote(), null, new Date("2026-11-27T18:00:00.000Z"), SESSION, 5,
  ).status, "fresh");
  assertEquals(assessServerQuote(
    "intraday", quote({ sourceTimestamp: "2026-11-27T17:50:00.000Z" }), null,
    new Date("2026-11-27T18:00:00.000Z"), SESSION, 5,
  ).status, "delayed");
  assertEquals(assessServerQuote(
    "intraday", quote({ sourceTimestamp: "2026-11-27T16:00:00.000Z" }), null,
    new Date("2026-11-27T18:00:00.000Z"), SESSION, 5,
  ).status, "stale");
  assertEquals(assessServerQuote(
    "intraday", null, null, new Date("2026-11-27T18:00:00.000Z"), SESSION, 5,
  ).status, "unavailable");
});

Deno.test("material cross-source price conflict fails closed", () => {
  const result = assessServerQuote(
    "intraday",
    quote(),
    quote({ provider: "independent-source", price: "370.00" }),
    new Date("2026-11-27T18:00:00.000Z"),
    SESSION,
  );
  assertEquals(result.status, "conflicting");
  assertEquals(result.quote?.price, "380.16");
});

Deno.test("browser quote rows are limited to this owner's holdings and radar", () => {
  const visible = ownerVisibleQuotes(
    ["VTI", "CENX"],
    [quote(), quote({ ticker: "CENX", price: "47.02" }), quote({ ticker: "NVDA", price: "180" })],
  );
  assertEquals(visible.map((item) => item.ticker), ["CENX", "VTI"]);
});
