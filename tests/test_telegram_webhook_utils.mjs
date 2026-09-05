import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  committedDeliveryUncertainText,
  webhookFailureText,
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
import {
  acknowledgeCommittedCommand,
  isDefinitiveServerRejection,
  reconcileLostCommandAcknowledgementRpc,
} from "../supabase/functions/telegram-portfolio/command-delivery-utils.mjs";

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

test("parseCallbackData accepts only a command action and UUID", () => {
  const id = "7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7";
  assert.deepEqual(parseCallbackData(`pc:confirm:${id}`), { action: "confirm", commandId: id });
  assert.deepEqual(parseCallbackData(`pc:cancel:${id.toUpperCase()}`), { action: "cancel", commandId: id });
  assert.equal(parseCallbackData(`pc:apply:${id}`), null);
  assert.equal(parseCallbackData(`pc:confirm:${id};DROP TABLE holdings`), null);
  assert.equal(parseCallbackData("pc:confirm:not-a-uuid"), null);
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
    operation: "plan", ticker: "VTI", amount: 300, cadence: "monthly",
    next_due_on: "2026-09-21", bucket: "core",
  });
  assert.match(preview, /Preview — record monthly VTI reminder/);
  assert.match(preview, /This records a reminder only; it does not schedule or place a brokerage purchase\./);
  assert.match(planResultText({
    operation: "plan", ticker: "VTI", amount: 300, cadence: "monthly",
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

test("a late transaction receipt tells the owner to reconcile", () => {
  const source = readFileSync(new URL("../supabase/functions/telegram-portfolio/index.ts", import.meta.url), "utf8");
  assert.match(source, /TRANSACTION_OUT_OF_ORDER/);
  assert.match(source, /reconciliation is required/);
  assert.match(source, /No trade was placed by this bot/);
});

test("a committed command never claims nothing changed when Telegram acknowledgement is uncertain", () => {
  const message = committedDeliveryUncertainText("Recorded BUY AAPL.");
  assert.match(message, /Recorded BUY AAPL\./);
  assert.match(message, /Telegram acknowledgement is uncertain/i);
  assert.doesNotMatch(message, /Nothing (was )?changed/i);
});

test("a committed callback reports uncertain acknowledgement when editMessageText fails", async () => {
  const calls = [];
  const receipt = await acknowledgeCommittedCommand({
    telegram: async (method) => {
      calls.push(method);
      if (method === "editMessageText") throw new Error("timeout");
    },
    sendText: async (_chatId, text) => calls.push(text),
    callback: { id: "callback", message: { chat: { id: 123 }, message_id: 456 } },
    result: { ok: true },
    resultText: "Recorded BUY AAPL.",
  });
  assert.deepEqual(receipt, { committed: true, acknowledgement: "uncertain" });
  assert.equal(calls[0], "answerCallbackQuery");
  assert.equal(calls[1], "editMessageText");
  assert.match(calls[2], /Telegram acknowledgement is uncertain/i);
  assert.doesNotMatch(calls[2], /Nothing (was )?changed/i);
});

test("post-commit acknowledgement persistence failures never produce a false rollback message", () => {
  assert.equal(webhookFailureText(true), null);
  assert.match(webhookFailureText(false), /Nothing was changed/);
});

test("lost apply-and-ack RPC response reconciles a committed receipt without claiming rollback", async () => {
  const calls = [];
  const receipt = await reconcileLostCommandAcknowledgementRpc({
    readReceipt: async () => ({ status: "pending", result: { ok: true } }),
    telegram: async (method, payload) => calls.push({ method, payload }),
    callback: { id: "callback" },
    resultText: "Recorded BUY AAPL.",
  });

  assert.deepEqual(receipt, { status: "pending", result: { ok: true } });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "answerCallbackQuery");
  assert.match(calls[0].payload.text, /Recorded BUY AAPL\./);
  assert.match(calls[0].payload.text, /acknowledgement is uncertain/i);
  assert.match(calls[0].payload.text, /reconciliation is required/i);
  assert.doesNotMatch(calls[0].payload.text, /Nothing (was )?changed/i);
});

test("definite apply-and-ack server rejection without a receipt can truthfully report no change", async () => {
  assert.equal(isDefinitiveServerRejection({ code: "42501" }), true);
  assert.equal(isDefinitiveServerRejection({ status: 409 }), true);
  assert.equal(isDefinitiveServerRejection({ code: "ECONNRESET" }), false);
  assert.equal(isDefinitiveServerRejection({ status: 500 }), false);

  const calls = [];
  const receipt = await reconcileLostCommandAcknowledgementRpc({
    readReceipt: async () => null,
    telegram: async (method, payload) => calls.push({ method, payload }),
    callback: { id: "callback" },
    resultText: "Recorded BUY AAPL.",
    definitiveRejection: true,
  });
  assert.equal(receipt, null);
  assert.match(calls[0].payload.text, /No change was recorded/i);
});

for (const [name, readReceipt] of [
  ["missing", async () => null],
  ["unreadable", async () => { throw new Error("timeout"); }],
]) {
  test(`${name} acknowledgement receipt after an ambiguous RPC response requires reconciliation`, async () => {
    const calls = [];
    await reconcileLostCommandAcknowledgementRpc({
      readReceipt,
      telegram: async (method, payload) => calls.push({ method, payload }),
      callback: { id: "callback" },
      resultText: "Recorded BUY AAPL.",
      definitiveRejection: false,
    });
    assert.match(calls[0].payload.text, /outcome is uncertain/i);
    assert.match(calls[0].payload.text, /reconciliation is required/i);
    assert.doesNotMatch(calls[0].payload.text, /Nothing (was )?changed/i);
  });
}
