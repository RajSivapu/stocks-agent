const TICKER = /^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/;

export function planTickerAllowed(ticker, activePolicy) {
  if (typeof ticker !== "string" || !TICKER.test(ticker) ||
      !activePolicy || !Array.isArray(activePolicy.broad_core_etfs)) return false;
  return activePolicy.broad_core_etfs.some((value) => value === ticker);
}

function number(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("en-US", { maximumFractionDigits: digits })
    : "unknown";
}

export function planPreviewText(command) {
  if (command.operation === "cancel_plan") {
    return `Preview — cancel the recurring ${command.ticker} reminder.\nThis changes a reminder only; it does not cancel or modify a brokerage order.`;
  }
  return `Preview — record monthly ${command.ticker} reminder for $${number(command.deposit_amount)}; next due ${command.next_due_on} in core.\nThis records a reminder only; it does not schedule or place a brokerage purchase.`;
}

export function planResultText(result) {
  if (result.operation === "cancel_plan") {
    return `Cancelled ${result.ticker} recurring reminder. No brokerage order was changed.`;
  }
  return `Recorded monthly ${result.ticker} reminder for $${number(result.deposit_amount)}; next due ${result.next_due_on} in ${result.bucket}. No brokerage purchase was scheduled or placed.`;
}

export function plansText(plans) {
  if (!Array.isArray(plans) || plans.length === 0) {
    return "No active recurring investment reminders are recorded.";
  }
  return [
    "Recurring investment reminders:",
    ...plans.map((plan) =>
      `${plan.ticker}: $${number(plan.amount)} ${plan.cadence} · next due ${plan.next_due_on} · ${plan.bucket}`
    ),
    "These are reminders only and do not place brokerage orders.",
  ].join("\n");
}
