import assert from "node:assert/strict";
import test from "node:test";

import {
  ownerMatches,
  parseCallbackData,
  resolveExecutionDate,
  secureEqual,
} from "../supabase/functions/telegram-portfolio/webhook-utils.mjs";

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
