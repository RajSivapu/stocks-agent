import assert from "node:assert/strict";
import test from "node:test";

import {
  isPrivateTelegramIdentity,
  ownerMatches,
  parseCallbackData,
  resolveExecutionDate,
  resolvePlanDate,
  secureEqual,
} from "../supabase/functions/telegram-portfolio/webhook-utils.mjs";
import {
  planPreviewText,
  planResultText,
  plansText,
  planTickerAllowed,
} from "../supabase/functions/telegram-portfolio/plan-utils.mjs";

test("secureEqual accepts only an exact secret", async () => {
  assert.equal(await secureEqual("correct-secret", "correct-secret"), true);
  assert.equal(await secureEqual("correct-secret", "wrong-secret"), false);
  assert.equal(await secureEqual("", ""), false);
});

test("ownerMatches compares both Telegram identifiers exactly", () => {
  assert.equal(ownerMatches(123, 456, "123", "456"), true);
  assert.equal(ownerMatches(123, 999, "123", "456"), false);
  assert.equal(ownerMatches(999, 456, "123", "456"), false);
});

test("parseCallbackData accepts only an opaque command action token", () => {
  const token = "Bf3S4xq0Vn2_6CYwPRtHd8AkLm9jZeXu";
  assert.deepEqual(parseCallbackData(`pc:c:${token}`), { action: "confirm", token });
  assert.deepEqual(parseCallbackData(`pc:x:${token}`), { action: "cancel", token });
  assert.equal(parseCallbackData(`pc:a:${token}`), null);
  assert.equal(parseCallbackData(`pc:c:${token};DROP`), null);
  assert.equal(parseCallbackData("pc:c:short"), null);
  assert(Buffer.byteLength(`pc:c:${token}`, "utf8") <= 64);
});

test("private Telegram identity requires a private chat and matching safe integer IDs", () => {
  assert.equal(isPrivateTelegramIdentity({ chat: { id: 123, type: "private" }, from: { id: 123 } }), true);
  assert.equal(isPrivateTelegramIdentity({ chat: { id: 123, type: "group" }, from: { id: 123 } }), false);
  assert.equal(isPrivateTelegramIdentity({ chat: { id: 123, type: "private" }, from: { id: 456 } }), false);
  assert.equal(isPrivateTelegramIdentity({ chat: { id: 1.5, type: "private" }, from: { id: 1.5 } }), false);
});

test("resolveExecutionDate defaults to the Telegram message date in Chicago", () => {
  const lateEveningChicago = Date.UTC(2026, 8, 2, 2, 0, 0) / 1000;
  assert.deepEqual(resolveExecutionDate(undefined, lateEveningChicago), {
    ok: true,
    executedOn: "2026-09-01",
  });
});

test("resolveExecutionDate accepts a valid earlier explicit trade date", () => {
  const reportedAt = Date.UTC(2026, 8, 2, 17, 0, 0) / 1000;
  assert.deepEqual(resolveExecutionDate("2026-08-28", reportedAt), {
    ok: true,
    executedOn: "2026-08-28",
  });
});

test("resolveExecutionDate rejects future and malformed explicit dates", () => {
  const reportedAt = Date.UTC(2026, 8, 2, 17, 0, 0) / 1000;
  assert.deepEqual(resolveExecutionDate("2026-09-03", reportedAt), { ok: false });
  assert.deepEqual(resolveExecutionDate("2026-02-30", reportedAt), { ok: false });
});

test("resolvePlanDate accepts today or a future owner-local due date", () => {
  const reportedAt = Date.UTC(2026, 8, 2, 2, 0, 0) / 1000;
  assert.deepEqual(resolvePlanDate("2026-09-01", reportedAt), {
    ok: true,
    nextDueOn: "2026-09-01",
  });
  assert.deepEqual(resolvePlanDate("2026-09-21", reportedAt), {
    ok: true,
    nextDueOn: "2026-09-21",
  });
});

test("resolvePlanDate rejects past, malformed, and invalid Telegram dates", () => {
  const reportedAt = Date.UTC(2026, 8, 2, 2, 0, 0) / 1000;
  assert.deepEqual(resolvePlanDate("2026-08-31", reportedAt), { ok: false });
  assert.deepEqual(resolvePlanDate("2026-02-30", reportedAt), { ok: false });
  assert.deepEqual(resolvePlanDate("2026-09-21", 0), { ok: false });
});

test("planTickerAllowed uses only a valid active policy broad-core list", () => {
  assert.equal(planTickerAllowed("VTI", { broad_core_etfs: ["VTI", "VOO"] }), true);
  assert.equal(planTickerAllowed("QQQ", { broad_core_etfs: ["VTI", "VOO"] }), false);
  assert.equal(planTickerAllowed("VTI", { broad_core_etfs: "VTI" }), false);
  assert.equal(planTickerAllowed("VTI", null), false);
  assert.equal(planTickerAllowed("vti", { broad_core_etfs: ["VTI"] }), false);
});

test("plan preview and callback wording cannot imply a brokerage order", () => {
  const preview = planPreviewText({
    operation: "plan", ticker: "VTI", deposit_amount: "300", cadence: "monthly",
    next_due_on: "2026-09-21", bucket: "core",
  });
  assert.match(preview, /Preview — record monthly VTI reminder/);
  assert.match(preview, /This records a reminder only; it does not schedule or place a brokerage purchase\./);
  assert.match(planResultText({
    operation: "plan", ticker: "VTI", deposit_amount: "300", cadence: "monthly",
    next_due_on: "2026-09-21", bucket: "core",
  }), /Recorded monthly VTI reminder/);
  assert.match(planResultText({ operation: "cancel_plan", ticker: "VTI" }), /Cancelled VTI recurring reminder/);
});

test("plansText is bounded by its caller and labels reminders", () => {
  assert.equal(plansText([]), "No active recurring investment reminders are recorded.");
  const text = plansText([{
    ticker: "VTI", amount: 300, cadence: "monthly", next_due_on: "2026-09-21", bucket: "core",
  }]);
  assert.match(text, /Recurring investment reminders/);
  assert.match(text, /VTI: \$300 monthly · next due 2026-09-21 · core/);
  assert.match(text, /do not place brokerage orders/);
});
