import {
  isFirstNyseSessionOfMonth,
  isNyseHoliday,
  quoteAllowedForPhase,
} from "./market-calendar.ts";
import type { VerifiedQuote } from "./contracts.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

const holidays = ["2026-09-07", "2026-11-26", "2026-12-25"];

function quote(asOf: string, marketState: string): VerifiedQuote {
  return {
    ticker: "VTI",
    price: "400",
    previous_close: "399",
    as_of: asOf,
    market_state: marketState,
    source: "yahoo-chart",
  };
}

Deno.test("NYSE holiday lookup is exact and weekends are not mislabeled holidays", () => {
  assert(isNyseHoliday("2026-09-07", holidays), "Labor Day should be closed");
  assert(!isNyseHoliday("2026-09-08", holidays), "next session should be open");
  assert(
    !isNyseHoliday("2026-09-05", holidays),
    "weekend is not a holiday label",
  );
});

Deno.test("first NYSE session of month accounts for weekends and configured holidays", () => {
  assert(
    isFirstNyseSessionOfMonth("2026-09-01", holidays),
    "September 1 should be the month's first session",
  );
  assert(
    !isFirstNyseSessionOfMonth("2026-09-02", holidays),
    "September 2 should not repeat the monthly review",
  );
  assert(
    isFirstNyseSessionOfMonth("2026-09-02", ["2026-09-01"]),
    "a configured closure should advance the first session",
  );
  assert(
    !isFirstNyseSessionOfMonth("2026-08-01", holidays),
    "a weekend is not a session",
  );
});

Deno.test("intraday and regular-session on-demand quotes obey the same age bound", () => {
  const now = new Date("2026-09-02T17:00:00.000Z");
  assert(
    quoteAllowedForPhase(
      "intraday",
      quote("2026-09-02T16:40:00.000Z", "REGULAR"),
      now,
      holidays,
      20,
    ),
    "20-minute quote should pass",
  );
  assert(
    !quoteAllowedForPhase(
      "intraday",
      quote("2026-09-02T16:39:00.000Z", "REGULAR"),
      now,
      holidays,
      20,
    ),
    "21-minute quote should fail",
  );
  assert(
    !quoteAllowedForPhase(
      "on-demand",
      quote("2026-09-02T16:39:00.000Z", "REGULAR"),
      now,
      holidays,
      20,
    ),
    "on-demand must not bypass live freshness",
  );
});

Deno.test("Monday pre-market accepts Friday close but not on a Monday holiday", () => {
  const fridayClose = quote("2026-09-11T20:00:00.000Z", "CLOSED");
  assert(
    quoteAllowedForPhase(
      "pre-market",
      fridayClose,
      new Date("2026-09-14T11:00:00.000Z"),
      holidays,
      20,
    ),
    "Friday close should be the latest completed Monday pre-market session",
  );
  assert(
    !quoteAllowedForPhase(
      "pre-market",
      quote("2026-09-04T20:00:00.000Z", "CLOSED"),
      new Date("2026-09-07T11:00:00.000Z"),
      holidays,
      20,
    ),
    "scheduled holiday should short-circuit",
  );
});

Deno.test("outside-session on-demand requires the latest official close", () => {
  const afterClose = new Date("2026-09-02T22:00:00.000Z");
  assert(
    quoteAllowedForPhase(
      "on-demand",
      quote("2026-09-02T20:00:00.000Z", "CLOSED"),
      afterClose,
      holidays,
      20,
    ),
    "same-day official close should pass after close",
  );
  assert(
    !quoteAllowedForPhase(
      "on-demand",
      quote("2026-09-01T20:00:00.000Z", "CLOSED"),
      afterClose,
      holidays,
      20,
    ),
    "older close should fail",
  );
});
