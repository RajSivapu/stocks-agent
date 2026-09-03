import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { runSlotsForMarketDay, zonedDateTime } from "./calendar.ts";

Deno.test("market anchors follow New York daylight saving rather than fixed UTC", () => {
  assertEquals(runSlotsForMarketDay({ marketDate: "2026-03-09", status: "open", earlyClose: false }).map((slot) => slot.dueAt), [
    "2026-03-09T11:30:00.000Z", "2026-03-09T17:00:00.000Z", "2026-03-09T20:10:00.000Z",
  ]);
  assertEquals(runSlotsForMarketDay({ marketDate: "2026-11-02", status: "open", earlyClose: false }).map((slot) => slot.dueAt), [
    "2026-11-02T12:30:00.000Z", "2026-11-02T18:00:00.000Z", "2026-11-02T21:10:00.000Z",
  ]);
  assertEquals(zonedDateTime("2026-11-01", 1, 30).toISOString(), "2026-11-01T05:30:00.000Z");
});

Deno.test("weekends have no slots and holidays have one model-free pre-market slot", () => {
  assertEquals(runSlotsForMarketDay({ marketDate: "2026-09-05", status: "open", earlyClose: false }), []);
  const holiday = runSlotsForMarketDay({ marketDate: "2026-09-07", status: "holiday", earlyClose: false });
  assertEquals(holiday.length, 1);
  assertEquals(holiday[0].phase, "pre-market");
  assertEquals(holiday[0].holiday, true);
});

Deno.test("early close moves intraday and post-market anchors", () => {
  const slots = runSlotsForMarketDay({ marketDate: "2026-11-27", status: "open", earlyClose: true });
  assertEquals(slots.map((slot) => slot.dueAt), [
    "2026-11-27T12:30:00.000Z", "2026-11-27T16:30:00.000Z", "2026-11-27T18:10:00.000Z",
  ]);
});
