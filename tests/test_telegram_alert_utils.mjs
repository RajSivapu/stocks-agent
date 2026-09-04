import assert from "node:assert/strict";
import test from "node:test";

import {
  alertActionPayload,
  alertActionResultText,
  parseAlertCallbackData,
} from "../supabase/functions/telegram-portfolio/alert-utils.mjs";

const ALERT_ID = "7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7";

test("alert callbacks accept only supported actions, a UUID, and a positive version", () => {
  assert.deepEqual(parseAlertCallbackData(`al:arm:${ALERT_ID}:1`), {
    action: "arm", alertId: ALERT_ID, version: 1, snoozeSeconds: null,
  });
  assert.deepEqual(parseAlertCallbackData(`al:snooze20m:${ALERT_ID.toUpperCase()}:42`), {
    action: "snooze", alertId: ALERT_ID, version: 42, snoozeSeconds: 1_200,
  });
  assert.deepEqual(parseAlertCallbackData(`al:snooze1d:${ALERT_ID}:2`), {
    action: "snooze", alertId: ALERT_ID, version: 2, snoozeSeconds: 86_400,
  });
  assert.deepEqual(parseAlertCallbackData(`al:ack:${ALERT_ID}:3`), {
    action: "acknowledge", alertId: ALERT_ID, version: 3, snoozeSeconds: null,
  });
  for (const invalid of [
    `al:buy:${ALERT_ID}:1`,
    `al:arm:${ALERT_ID}:0`,
    `al:arm:${ALERT_ID}:-1`,
    `al:arm:${ALERT_ID}:1000001`,
    `al:arm:${ALERT_ID}:1;DROP TABLE holdings`,
    "al:arm:not-a-uuid:1",
    "x".repeat(65),
  ]) assert.equal(parseAlertCallbackData(invalid), null);
});

test("alert action payload binds the verified owner receipt and deterministic snooze time", () => {
  const parsed = parseAlertCallbackData(`al:snooze20m:${ALERT_ID}:7`);
  assert.ok(parsed);
  assert.deepEqual(alertActionPayload(parsed, 901, 123, 456, new Date("2026-09-03T17:20:00.000Z")), {
    p_draft_or_rule_id: ALERT_ID,
    p_action: "snooze",
    p_update_id: 901,
    p_chat_id: 123,
    p_user_id: 456,
    p_expected_version: 7,
    p_snooze_until: "2026-09-03T17:40:00.000Z",
  });
  assert.deepEqual(
    alertActionPayload(
      parseAlertCallbackData(`al:arm:${ALERT_ID}:1`),
      902,
      123,
      456,
      new Date("2026-09-03T17:20:00.000Z"),
    ),
    {
      p_draft_or_rule_id: ALERT_ID,
      p_action: "arm",
      p_update_id: 902,
      p_chat_id: 123,
      p_user_id: 456,
      p_expected_version: 1,
      p_snooze_until: null,
    },
  );
});

test("alert action result wording is explicit about monitoring-only behavior", () => {
  assert.match(alertActionResultText({ ok: true, state: "active", version: 1 }, "arm"), /Alert armed/);
  assert.match(alertActionResultText({ ok: true, state: "snoozed", version: 8 }, "snooze"), /Alert snoozed/);
  assert.match(alertActionResultText({ ok: true, state: "dismissed", version: 9 }, "dismiss"), /Alert dismissed/);
  assert.match(alertActionResultText({ ok: false, state: "rejected" }, "pause"), /Nothing changed/);
  for (const text of [
    alertActionResultText({ ok: true, state: "active", version: 1 }, "arm"),
    alertActionResultText({ ok: true, state: "paused", version: 2 }, "pause"),
    alertActionResultText({ ok: true, state: "active", version: 3 }, "resume"),
    alertActionResultText({ ok: true, state: "active", version: 3 }, "acknowledge"),
  ]) assert.match(text, /No brokerage order was placed or modified\./);
});
