import { createClient } from "npm:@supabase/supabase-js@2.112.4";

import {
  alertActionPayload,
  alertActionResultText,
  parseAlertCallbackData,
} from "./alert-utils.mjs";
import { parsePortfolioCommand } from "./parser.mjs";
import { planPreviewText, planResultText, plansText, planTickerAllowed } from "./plan-utils.mjs";
import { ownerMatches, parseCallbackData, resolveExecutionDate, resolvePlanDate, secureEqual } from "./webhook-utils.mjs";

type TelegramMessage = {
  message_id: number;
  date: number;
  text?: string;
  chat: { id: number };
  from?: { id: number };
};

type TelegramCallback = {
  id: string;
  data?: string;
  from: { id: number };
  message?: TelegramMessage;
};

type TelegramUpdate = {
  update_id?: number;
  message?: TelegramMessage;
  callback_query?: TelegramCallback;
};

type Holding = {
  ticker: string;
  shares: string | number;
  avg_cost: string | number;
  bucket: string | null;
  stop: string | number | null;
  target: string | number | null;
};

type InvestmentPlan = {
  ticker: string;
  amount: string | number;
  cadence: string;
  next_due_on: string;
  bucket: string;
  active: boolean;
  updated_at: string;
};

const mustEnv = (name: string): string => {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
};

const TELEGRAM_BOT_TOKEN = mustEnv("TELEGRAM_BOT_TOKEN");
const TELEGRAM_WEBHOOK_SECRET = mustEnv("TELEGRAM_WEBHOOK_SECRET");
const OWNER_CHAT_ID = mustEnv("TELEGRAM_OWNER_CHAT_ID");
const OWNER_USER_ID = mustEnv("TELEGRAM_OWNER_USER_ID");

const telegramId = (name: string, value: string): number => {
  if (!/^-?\d+$/.test(value)) throw new Error(`${name} must be an integer`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${name} is outside the safe integer range`);
  return parsed;
};

const OWNER_CHAT_ID_NUMBER = telegramId("TELEGRAM_OWNER_CHAT_ID", OWNER_CHAT_ID);
const OWNER_USER_ID_NUMBER = telegramId("TELEGRAM_OWNER_USER_ID", OWNER_USER_ID);

const supabase = createClient(
  mustEnv("SUPABASE_URL"),
  mustEnv("SUPABASE_SERVICE_ROLE_KEY"),
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } },
);

const HELP_TEXT = [
  "I only record trades you already placed; I never place or modify brokerage orders.",
  "Examples:",
  "/buy AAPL 2 210 growth",
  "bought 2 AAPL at 210 growth",
  "/sell AAPL 1 225",
  "sold all NVDA at 210",
  "/sell NVDA all 210 on 2026-08-28",
  "/stop AAPL 195",
  "/portfolio",
  "/plan VTI 300 monthly 2026-09-21 core",
  "/cancelplan VTI",
  "/plans",
].join("\n");

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return Response.json(body, { status });
}

function finiteNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function formatNumber(value: unknown, digits = 4): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "unknown";
  return number.toLocaleString("en-US", { maximumFractionDigits: digits });
}

async function telegram(method: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => null) as { ok?: boolean; result?: Record<string, unknown> } | null;
  if (!response.ok || !body?.ok) throw new Error(`Telegram ${method} failed`);
  return body.result ?? {};
}

async function sendText(chatId: number, text: string, replyMarkup?: Record<string, unknown>) {
  return await telegram("sendMessage", {
    chat_id: chatId,
    text,
    disable_web_page_preview: true,
    ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
  });
}

async function claimUpdate(updateId: number, kind: "message" | "callback_query"): Promise<boolean> {
  const { error } = await supabase.from("telegram_updates").insert({
    telegram_update_id: updateId,
    kind,
  });
  if (!error) return true;
  if (error.code === "23505") return false;
  throw new Error("Could not claim Telegram update");
}

async function getHolding(ticker: string): Promise<Holding | null> {
  const { data, error } = await supabase.from("holdings").select(
    "ticker,shares,avg_cost,bucket,stop,target",
  ).eq("ticker", ticker).maybeSingle();
  if (error) throw new Error("Could not read holding");
  return data as Holding | null;
}

async function portfolioText(): Promise<string> {
  const { data, error } = await supabase.from("holdings").select(
    "ticker,shares,avg_cost,bucket,stop,target",
  ).order("ticker");
  if (error) throw new Error("Could not read portfolio");
  const holdings = (data ?? []) as Holding[];
  if (!holdings.length) return "No holdings are recorded in Supabase.";
  return [
    "Recorded portfolio:",
    ...holdings.map((holding) => {
      const stop = holding.stop === null ? "no stop" : `stop $${formatNumber(holding.stop, 2)}`;
      return `${holding.ticker}: ${formatNumber(holding.shares)} shares @ $${formatNumber(holding.avg_cost, 2)} · ${holding.bucket ?? "unbucketed"} · ${stop}`;
    }),
  ].join("\n");
}

async function getInvestmentPlan(ticker: string): Promise<InvestmentPlan | null> {
  const { data, error } = await supabase.from("owner_investment_plans").select(
    "ticker,amount,cadence,next_due_on,bucket,active,updated_at",
  ).eq("ticker", ticker).maybeSingle();
  if (error) throw new Error("Could not read investment reminder");
  return data as InvestmentPlan | null;
}

async function activePolicy(): Promise<Record<string, unknown> | null> {
  const { data, error } = await supabase.from("market_policy_config").select("config")
    .eq("active", true).order("version", { ascending: false }).limit(2);
  if (error || !Array.isArray(data) || data.length !== 1) return null;
  const policy = data[0]?.config;
  return policy && typeof policy === "object" && !Array.isArray(policy)
    ? policy as Record<string, unknown>
    : null;
}

async function investmentPlansText(): Promise<string> {
  const { data, error } = await supabase.from("owner_investment_plans").select(
    "ticker,amount,cadence,next_due_on,bucket",
  ).eq("active", true).order("next_due_on").limit(20);
  if (error) throw new Error("Could not read investment reminders");
  return plansText(data ?? []);
}

function previewText(command: Record<string, unknown>, holding: Holding | null, resolvedQty?: number): string {
  const executionDate = command.executed_on ? ` on ${String(command.executed_on)}` : "";
  if (command.operation === "buy") {
    return `Preview — record BUY ${formatNumber(command.qty)} ${command.ticker} @ $${formatNumber(command.price, 2)} in ${holding?.bucket ?? command.bucket}${executionDate}.\nThis only updates Supabase; it does not place a trade.`;
  }
  if (command.operation === "sell") {
    const realized = holding && resolvedQty !== undefined
      ? (Number(command.price) - Number(holding.avg_cost)) * resolvedQty
      : null;
    return `Preview — record SELL ${formatNumber(resolvedQty)} ${command.ticker} @ $${formatNumber(command.price, 2)}${executionDate}${realized === null ? "" : ` · estimated realized P&L $${formatNumber(realized, 2)}`}.\nThis only updates Supabase; it does not place a trade.`;
  }
  return `Preview — set the recorded ${command.ticker} stop to $${formatNumber(command.stop, 2)}.\nThis only updates Supabase; it does not place or modify a brokerage order.`;
}

async function handleMutation(updateId: number, chatId: number, command: Record<string, unknown>) {
  const ticker = String(command.ticker);
  if (command.operation === "plan" || command.operation === "cancel_plan") {
    const existingPlan = await getInvestmentPlan(ticker);
    if (command.operation === "cancel_plan" && !existingPlan?.active) {
      await sendText(chatId, `${ticker} has no active recurring reminder. Nothing changed.`);
      return;
    }
    const preview = planPreviewText(command);
    const row = {
      telegram_update_id: updateId,
      chat_id: OWNER_CHAT_ID_NUMBER,
      user_id: OWNER_USER_ID_NUMBER,
      operation: command.operation,
      ticker,
      qty: null,
      price: null,
      executed_on: null,
      bucket: command.operation === "plan" ? command.bucket : null,
      expected_shares: null,
      stop: null,
      amount: command.operation === "plan" ? command.amount : null,
      cadence: command.operation === "plan" ? command.cadence : null,
      next_due_on: command.operation === "plan" ? command.next_due_on : null,
      expected_plan_updated_at: existingPlan?.updated_at ?? null,
      preview: { text: preview, parsed: command },
    };
    const { data, error } = await supabase.from("portfolio_commands").insert(row).select("id").single();
    if (error || !data?.id) throw new Error("Could not create pending reminder command");
    const result = await sendText(chatId, preview, {
      inline_keyboard: [[
        { text: "Confirm", callback_data: `pc:confirm:${data.id}` },
        { text: "Cancel", callback_data: `pc:cancel:${data.id}` },
      ]],
    });
    const messageId = Number(result.message_id);
    if (Number.isSafeInteger(messageId)) {
      await supabase.from("portfolio_commands").update({ confirmation_message_id: messageId }).eq("id", data.id);
    }
    return;
  }

  const holding = await getHolding(ticker);
  const expectedShares = holding ? finiteNumber(holding.shares) : 0;
  if (expectedShares === null) throw new Error("Recorded shares are invalid");

  let qty: number | null = command.qty === undefined ? null : Number(command.qty);
  let bucket: string | null = command.bucket ? String(command.bucket) : null;

  if (command.operation === "buy") {
    if (!holding && !bucket) {
      await sendText(chatId, "A new holding needs a bucket: core, growth, or speculative. Example: /buy AAPL 2 210 growth");
      return;
    }
    if (holding) bucket = holding.bucket;
  } else if (command.operation === "sell") {
    if (!holding) {
      await sendText(chatId, `${ticker} is not in the recorded portfolio, so nothing changed.`);
      return;
    }
    qty = command.qty === "all" ? expectedShares : Number(command.qty);
    if (!Number.isFinite(qty) || qty <= 0 || qty > expectedShares) {
      await sendText(chatId, `That sell exceeds the recorded ${formatNumber(expectedShares)} ${ticker} shares. Nothing changed.`);
      return;
    }
  } else if (!holding) {
    await sendText(chatId, `${ticker} is not in the recorded portfolio, so no stop was changed.`);
    return;
  }

  const preview = previewText(command, holding, qty ?? undefined);
  const row = {
    telegram_update_id: updateId,
    chat_id: OWNER_CHAT_ID_NUMBER,
    user_id: OWNER_USER_ID_NUMBER,
    operation: command.operation,
    ticker,
    qty,
    price: command.price ?? null,
    executed_on: command.executed_on ?? null,
    bucket,
    expected_shares: expectedShares,
    stop: command.stop ?? null,
    preview: { text: preview, parsed: command },
  };
  const { data, error } = await supabase.from("portfolio_commands").insert(row).select("id").single();
  if (error || !data?.id) throw new Error("Could not create pending command");

  const result = await sendText(chatId, preview, {
    inline_keyboard: [[
      { text: "Confirm", callback_data: `pc:confirm:${data.id}` },
      { text: "Cancel", callback_data: `pc:cancel:${data.id}` },
    ]],
  });
  const messageId = Number(result.message_id);
  if (Number.isSafeInteger(messageId)) {
    await supabase.from("portfolio_commands").update({ confirmation_message_id: messageId }).eq("id", data.id);
  }
}

async function handleMessage(updateId: number, message: TelegramMessage) {
  const parsed = parsePortfolioCommand(message.text);
  if (!parsed.ok || !("command" in parsed)) {
    await sendText(message.chat.id, HELP_TEXT);
    return;
  }
  const command = parsed.command as Record<string, unknown>;
  if (command.operation === "help") {
    await sendText(message.chat.id, HELP_TEXT);
  } else if (command.operation === "portfolio") {
    await sendText(message.chat.id, await portfolioText());
  } else if (command.operation === "plans") {
    await sendText(message.chat.id, await investmentPlansText());
  } else {
    if (command.operation === "buy" || command.operation === "sell") {
      const execution = resolveExecutionDate(command.executed_on, message.date);
      if (!execution.ok) {
        await sendText(message.chat.id, "Use a real, non-future trade date as YYYY-MM-DD. Nothing changed.");
        return;
      }
      command.executed_on = execution.executedOn;
    }
    if (command.operation === "plan") {
      const due = resolvePlanDate(command.next_due_on, message.date);
      if (!due.ok) {
        await sendText(message.chat.id, "Use a real due date on or after this message date as YYYY-MM-DD. Nothing changed.");
        return;
      }
      command.next_due_on = due.nextDueOn;
      if (!planTickerAllowed(String(command.ticker), await activePolicy())) {
        await sendText(message.chat.id, "Only an approved broad Core ETF can use a recurring plan. Nothing changed.");
        return;
      }
    }
    await handleMutation(updateId, message.chat.id, command);
  }
}

function callbackResultText(result: Record<string, unknown>): string {
  if (result.ok && result.status === "cancelled") return "Cancelled. Nothing was changed.";
  if (!result.ok) {
    return `${String(result.status ?? "rejected").toUpperCase()}: ${String(result.reason ?? "Nothing was changed; submit the command again.")}`;
  }
  if (result.operation === "stop") {
    return `Recorded ${result.ticker} stop at $${formatNumber(result.stop, 2)}. No brokerage order was placed or modified.`;
  }
  if (result.operation === "plan" || result.operation === "cancel_plan") {
    return planResultText(result);
  }
  const pnl = result.operation === "sell" ? ` · realized P&L $${formatNumber(result.realized_pnl, 2)}` : "";
  const executionDate = result.executed_on ? ` · executed ${String(result.executed_on)}` : "";
  return `Recorded ${String(result.operation).toUpperCase()} ${result.ticker}. Position: ${formatNumber(result.shares)} shares @ $${formatNumber(result.avg_cost, 2)}${executionDate}${pnl}. No trade was placed by this bot.`;
}

async function handleAlertCallback(
  updateId: number,
  callback: TelegramCallback,
  parsed: NonNullable<ReturnType<typeof parseAlertCallbackData>>,
) {
  if (!callback.message) {
    await telegram("answerCallbackQuery", { callback_query_id: callback.id, text: "Invalid or expired action." });
    return;
  }
  const { data, error } = await supabase.rpc(
    "apply_market_alert_action",
    alertActionPayload(parsed, updateId, OWNER_CHAT_ID_NUMBER, OWNER_USER_ID_NUMBER),
  );
  if (error || !data) {
    await telegram("answerCallbackQuery", {
      callback_query_id: callback.id,
      text: "Alert action rejected. Nothing changed.",
    });
    return;
  }
  const result = data as Record<string, unknown>;
  await telegram("answerCallbackQuery", {
    callback_query_id: callback.id,
    text: result.duplicate ? "Already recorded." : "Alert updated.",
  });
  await telegram("editMessageText", {
    chat_id: callback.message.chat.id,
    message_id: callback.message.message_id,
    text: alertActionResultText(result, parsed.action),
    reply_markup: { inline_keyboard: [] },
  });
}

async function handleCallback(updateId: number, callback: TelegramCallback) {
  const alert = parseAlertCallbackData(callback.data);
  if (alert) {
    await handleAlertCallback(updateId, callback, alert);
    return;
  }
  const parsed = parseCallbackData(callback.data);
  if (!parsed || !callback.message) {
    await telegram("answerCallbackQuery", { callback_query_id: callback.id, text: "Invalid or expired action." });
    return;
  }
  const functionName = parsed.action === "confirm" ? "apply_portfolio_command" : "cancel_portfolio_command";
  const { data, error } = await supabase.rpc(functionName, {
    p_command_id: parsed.commandId,
    p_chat_id: OWNER_CHAT_ID_NUMBER,
    p_user_id: OWNER_USER_ID_NUMBER,
  });
  if (error || !data) {
    await telegram("answerCallbackQuery", { callback_query_id: callback.id, text: "Temporary database error. Nothing was changed." });
    return;
  }
  const result = data as Record<string, unknown>;
  await telegram("answerCallbackQuery", {
    callback_query_id: callback.id,
    text: result.ok ? "Recorded." : "Nothing changed.",
  });
  await telegram("editMessageText", {
    chat_id: callback.message.chat.id,
    message_id: callback.message.message_id,
    text: callbackResultText(result),
    reply_markup: { inline_keyboard: [] },
  });
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return jsonResponse(405, { ok: false });
  const suppliedSecret = request.headers.get("x-telegram-bot-api-secret-token") ?? "";
  if (!(await secureEqual(suppliedSecret, TELEGRAM_WEBHOOK_SECRET))) {
    return jsonResponse(401, { ok: false });
  }

  let update: TelegramUpdate;
  try {
    update = await request.json() as TelegramUpdate;
  } catch {
    return jsonResponse(400, { ok: false });
  }

  const updateId = update.update_id;
  const kind = update.message ? "message" : update.callback_query ? "callback_query" : null;
  const chatId = update.message?.chat.id ?? update.callback_query?.message?.chat.id;
  const userId = update.message?.from?.id ?? update.callback_query?.from.id;
  if (!Number.isSafeInteger(updateId) || (updateId as number) < 0 || !kind) {
    return jsonResponse(200, { ok: true });
  }
  if (!ownerMatches(chatId, userId, OWNER_CHAT_ID, OWNER_USER_ID)) {
    // Telegram retries every non-2xx webhook response. Acknowledge untrusted identities while
    // doing no work so a stranger cannot create an unbounded retry stream for the owner bot.
    return jsonResponse(200, { ok: true, ignored: true });
  }

  try {
    if (!(await claimUpdate(updateId as number, kind))) return jsonResponse(200, { ok: true, duplicate: true });
    if (update.message) await handleMessage(updateId as number, update.message);
    else if (update.callback_query) await handleCallback(updateId as number, update.callback_query);
    return jsonResponse(200, { ok: true });
  } catch {
    try {
      if (chatId !== undefined) await sendText(chatId, "Temporary recorder error. Nothing was changed; please try again shortly.");
    } catch {
      // Telegram delivery also failed; never expose credentials or internal errors.
    }
    return jsonResponse(200, { ok: false });
  }
});
