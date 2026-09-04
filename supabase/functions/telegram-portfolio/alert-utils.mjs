const UUID = "([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})";
const CALLBACK = new RegExp(`^al:(arm|dismiss|pause|resume|ack|snooze20m|snooze1d):${UUID}:([1-9]\\d{0,6})$`, "i");

const ACTIONS = Object.freeze({
  arm: { action: "arm", snoozeSeconds: null },
  dismiss: { action: "dismiss", snoozeSeconds: null },
  pause: { action: "pause", snoozeSeconds: null },
  resume: { action: "resume", snoozeSeconds: null },
  ack: { action: "acknowledge", snoozeSeconds: null },
  snooze20m: { action: "snooze", snoozeSeconds: 20 * 60 },
  snooze1d: { action: "snooze", snoozeSeconds: 24 * 60 * 60 },
});

export function parseAlertCallbackData(value) {
  if (typeof value !== "string" || value.length > 64) return null;
  const match = value.match(CALLBACK);
  if (!match) return null;
  const version = Number(match[3]);
  if (!Number.isSafeInteger(version) || version <= 0 || version > 1_000_000) return null;
  const mapped = ACTIONS[match[1].toLowerCase()];
  return {
    action: mapped.action,
    alertId: match[2].toLowerCase(),
    version,
    snoozeSeconds: mapped.snoozeSeconds,
  };
}

export function alertActionPayload(parsed, updateId, chatId, userId, now = new Date()) {
  const snoozeUntil = parsed.snoozeSeconds === null
    ? null
    : new Date(now.valueOf() + parsed.snoozeSeconds * 1000).toISOString();
  return {
    p_draft_or_rule_id: parsed.alertId,
    p_action: parsed.action,
    p_update_id: updateId,
    p_chat_id: chatId,
    p_user_id: userId,
    p_expected_version: parsed.version,
    p_snooze_until: snoozeUntil,
  };
}

export function alertActionResultText(result, action) {
  if (!result?.ok) return "Alert action rejected. Nothing changed. No brokerage order was placed or modified.";
  const labels = {
    arm: "Alert armed",
    dismiss: "Alert dismissed",
    pause: "Alert paused",
    resume: "Alert resumed",
    snooze: "Alert snoozed",
    acknowledge: "Alert acknowledged",
  };
  const label = labels[action] ?? "Alert updated";
  const duplicate = result.duplicate ? " The earlier receipt was reused." : "";
  return `${label}. Monitoring state: ${String(result.state ?? "recorded")}.${duplicate} No brokerage order was placed or modified.`;
}
