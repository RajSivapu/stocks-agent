import { classifyFreshness } from "./freshness.ts";

const calendar = { holidays: ["2026-09-07", "2026-11-26", "2026-12-25"] };

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

Deno.test("Friday close remains as-of-close over a weekend and holiday", () => {
  for (const now of ["2026-09-06T15:00:00.000Z", "2026-09-07T15:00:00.000Z"]) {
    const result = classifyFreshness({
      kind: "price",
      dataAsOf: "2026-09-04T20:00:00.000Z",
      sourceMarketState: "CLOSED",
    }, new Date(now), calendar);
    assertEquals(result.freshness, "fresh");
    assertEquals(result.marketState, "as_of_close");
    assertEquals(result.dataAsOf, "2026-09-04T20:00:00.000Z");
  }
});

Deno.test("regular quote is fresh for twenty minutes only during session", () => {
  assertEquals(classifyFreshness({
    kind: "price",
    dataAsOf: "2026-09-03T17:40:00.000Z",
    sourceMarketState: "REGULAR",
  }, new Date("2026-09-03T18:00:00.000Z"), calendar).freshness, "fresh");
  assertEquals(classifyFreshness({
    kind: "price",
    dataAsOf: "2026-09-03T17:39:59.000Z",
    sourceMarketState: "REGULAR",
  }, new Date("2026-09-03T18:00:00.000Z"), calendar).freshness, "stale");
});

Deno.test("closed price becomes stale after the next session opens", () => {
  const result = classifyFreshness({
    kind: "price",
    dataAsOf: "2026-09-04T20:00:00.000Z",
    sourceMarketState: "CLOSED",
  }, new Date("2026-09-08T15:00:00.000Z"), calendar);
  assertEquals(result.freshness, "stale");
  assertEquals(result.marketState, "regular");
});

Deno.test("missing or future evidence is unavailable", () => {
  assertEquals(classifyFreshness({ kind: "run", dataAsOf: null }, new Date(), calendar).freshness, "unavailable");
  assertEquals(classifyFreshness({
    kind: "price",
    dataAsOf: "2026-09-03T19:00:00.000Z",
    sourceMarketState: "REGULAR",
  }, new Date("2026-09-03T18:00:00.000Z"), calendar).freshness, "unavailable");
});

Deno.test("scheduled receipts remain current across a closed weekend and holiday", () => {
  const result = classifyFreshness({
    kind: "brief",
    phase: "post-market",
    status: "delivered",
    dataAsOf: "2026-09-04T20:10:00.000Z",
  }, new Date("2026-09-07T17:00:00.000Z"), calendar);
  assertEquals(result.freshness, "fresh");
  assertEquals(result.marketState, "holiday");
});

Deno.test("the prior close becomes stale after the next pre-market deadline", () => {
  const result = classifyFreshness({
    kind: "run",
    phase: "post-market",
    status: "completed",
    dataAsOf: "2026-09-04T20:10:00.000Z",
  }, new Date("2026-09-08T12:05:00.000Z"), calendar);
  assertEquals(result.freshness, "stale");
  assertEquals(result.marketState, "pre_market");
});
