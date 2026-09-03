import { readBoundedJson } from "../_shared/bounded-json.ts";
import { HttpError, jsonResponse } from "../_shared/errors.ts";
import { digestPairingValue } from "./pairing.mjs";
import { parsePortfolioCommand } from "./parser.mjs";
import { planResultText, plansText } from "./plan-utils.mjs";
import {
  isPrivateTelegramIdentity,
  parseCallbackData,
  resolveExecutionDate,
  resolvePlanDate,
  secureEqual,
} from "./webhook-utils.mjs";


const MAX_BODY_BYTES = 64 * 1024;
const MAX_TELEGRAM_TEXT_CHARACTERS = 4_096;
const TELEGRAM_ID = /^[1-9][0-9]{0,15}$/;

export type TelegramDelivery = {
  status: "delivered" | "delivery_failed" | "delivery_unknown";
  messageId?: number;
};

export type TelegramOutboundMessage = {
  chatId: number;
  text: string;
  replyToMessageId?: number;
  inlineKeyboard?: Array<Array<{ text: string; callbackData: string }>>;
};

export type TelegramPortfolioRepository = {
  resolveLink(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  claimUpdate(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  consumePairing(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  prepareCommand(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  applyCallback(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  unlink(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  portfolio(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  plans(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  recordDelivery(value: Record<string, unknown>): Promise<Record<string, unknown>>;
  recordPairingDelivery(value: Record<string, unknown>): Promise<Record<string, unknown>>;
};

export type TelegramPortfolioHandlerDependencies = {
  webhookSecret: string;
  pairingHashPepper: string;
  repository: TelegramPortfolioRepository;
  sendTelegram(value: TelegramOutboundMessage): Promise<TelegramDelivery>;
  newId?: () => string;
  newCallbackToken?: () => string;
};

type TelegramIdentity = {
  chatId: number;
  userId: number;
  messageId?: number;
  date: number;
};

const HELP_TEXT = [
  "I only record trades you already placed; I never place, modify, or cancel brokerage orders.",
  "Commands:",
  "/buy AAPL 2 210 growth",
  "/sell AAPL 1 225",
  "/sell NVDA all 210",
  "/stop AAPL 195",
  "/portfolio",
  "/plan VTI 300 monthly 2026-09-21 core",
  "/cancelplan VTI",
  "/plans",
  "/status",
  "/unlink",
].join("\n");

function safeId(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0 && TELEGRAM_ID.test(String(value));
}

function updateId(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

function messageIdentity(value: unknown): TelegramIdentity | null {
  if (!isPrivateTelegramIdentity(value)) return null;
  const message = value as Record<string, unknown>;
  const chat = message.chat as Record<string, unknown>;
  const from = message.from as Record<string, unknown>;
  if (!safeId(chat.id) || !safeId(from.id) || !Number.isSafeInteger(message.date) || Number(message.date) <= 0) {
    return null;
  }
  return {
    chatId: chat.id,
    userId: from.id,
    messageId: safeId(message.message_id) ? message.message_id : undefined,
    date: Number(message.date),
  };
}

function callbackIdentity(value: unknown): TelegramIdentity | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const callback = value as Record<string, unknown>;
  const message = callback.message;
  if (!message || typeof message !== "object" || Array.isArray(message)) return null;
  return messageIdentity({ ...message, from: callback.from });
}

function valueText(value: unknown): string {
  if (typeof value === "string" && /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(value)) return value;
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return String(value);
  return "unknown";
}

function projectionText(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "unavailable";
  const projection = value as Record<string, unknown>;
  const parts = [
    `${valueText(projection.shares)} shares`,
    `average cost $${valueText(projection.avg_cost)}`,
  ];
  if (typeof projection.bucket === "string") parts.push(projection.bucket);
  return parts.join(" · ");
}

function commandText(command: Record<string, unknown>): string {
  const ticker = String(command.ticker ?? "?");
  const date = command.executed_on ? ` on ${String(command.executed_on)}` : "";
  const fees = command.fees && command.fees !== "0" ? `, fees $${valueText(command.fees)}` : "";
  if (command.operation === "buy") {
    return `BUY ${valueText(command.quantity)} ${ticker} @ $${valueText(command.fill_price)}${date}${fees} in ${String(command.bucket ?? "the existing bucket")}`;
  }
  if (command.operation === "sell") {
    return `SELL ${valueText(command.quantity)} ${ticker} @ $${valueText(command.fill_price)}${date}${fees}`;
  }
  if (command.operation === "sell_all") {
    return `SELL ALL recorded ${ticker} shares @ $${valueText(command.fill_price)}${date}${fees}`;
  }
  if (command.operation === "stop") return `SET recorded ${ticker} stop to $${valueText(command.stop)}`;
  if (command.operation === "plan") {
    return `SET monthly ${ticker} reminder to $${valueText(command.deposit_amount)}, next due ${String(command.next_due_on ?? "?")}`;
  }
  if (command.operation === "cancel_plan") return `CANCEL ${ticker} recurring reminder`;
  return "RECORD CHANGE";
}

function previewText(preview: Record<string, unknown>, command: Record<string, unknown>): string {
  const operation = String(preview.operation ?? "record change").replaceAll("_", " ").toUpperCase();
  const warnings = Array.isArray(preview.warnings) && preview.warnings.length
    ? `\nWarnings: ${preview.warnings.map(String).join("; ")}`
    : "";
  return [
    `Preview — record ${operation}: ${commandText(command)}.`,
    `Before: ${projectionText(preview.before)}`,
    `After: ${projectionText(preview.after)}`,
    `Preview digest: ${String(preview.preview_digest ?? "unavailable")}`,
    "This records an activity you already completed; it does not place, modify, or cancel a brokerage order.",
  ].join("\n") + warnings;
}

function portfolioText(result: Record<string, unknown>): string {
  const holdings = result.holdings;
  if (!Array.isArray(holdings) || holdings.length === 0) {
    return "No holdings are recorded. This is a recordkeeping view, not a brokerage account.";
  }
  return [
    "Recorded portfolio:",
    ...holdings.map((value) => {
      const holding = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
      const stop = holding.stop === null || holding.stop === undefined
        ? "no recorded stop"
        : `recorded stop $${valueText(holding.stop)}`;
      return `${String(holding.ticker ?? "?")}: ${valueText(holding.shares)} shares @ $${valueText(holding.avg_cost)} · ${String(holding.bucket ?? "unclassified")} · ${stop}`;
    }),
    "Recorded data only; no brokerage positions or orders were queried.",
  ].join("\n");
}

function callbackResultText(result: Record<string, unknown>): string {
  const action = result.action;
  const status = result.status;
  if (action === "cancel" && status === "cancelled") {
    return "Cancelled the pending record change. No brokerage order was changed.";
  }
  if (action !== "confirm" || status !== "applied") {
    return "That preview is unavailable or no longer applicable. Nothing was changed; create a new preview and review it before confirming.";
  }
  const applied = result.result;
  if (!applied || typeof applied !== "object" || Array.isArray(applied)) {
    return "I could not verify the record change outcome. Nothing was changed by this callback; check /portfolio before trying again.";
  }
  const value = applied as Record<string, unknown>;
  if (value.operation === "plan" || value.operation === "cancel_plan") return planResultText(value);
  if (value.operation === "stop") {
    return `Recorded ${String(value.ticker ?? "the holding")} stop at $${valueText(value.stop)}. No brokerage order was placed or modified.`;
  }
  return `Recorded ${String(value.operation ?? "activity").replaceAll("_", " ").toUpperCase()} ${String(value.ticker ?? "")}. Result: ${projectionText(value.holding)}. No brokerage order was placed.`;
}

function pairingText(status: unknown, code: string): string {
  switch (status) {
    case "linked":
      return "Telegram is linked to your stock-agent account. I can now record completed activity after you confirm each preview.";
    case "relink_required":
      return `This account is already linked elsewhere. If you intend to replace that link, send /relink ${code} in this private chat before the code expires.`;
    case "rate_limited":
      return "Too many pairing attempts. Wait ten minutes, then generate a new code in the web app.";
    default:
      return "That pairing code is invalid or expired. Generate a new code in the web app.";
  }
}

async function safeDelivery(
  send: (value: TelegramOutboundMessage) => Promise<TelegramDelivery>,
  value: TelegramOutboundMessage,
): Promise<TelegramDelivery> {
  try {
    return await send(value);
  } catch (_error) {
    return { status: "delivery_unknown" };
  }
}

function boundedTelegramText(value: string): string {
  const characters = [...value];
  if (characters.length <= MAX_TELEGRAM_TEXT_CHARACTERS) return value;
  const suffix = "\n\n[Output truncated. Use the web app for the complete record. This bot does not place or modify brokerage orders.]";
  return characters.slice(0, MAX_TELEGRAM_TEXT_CHARACTERS - [...suffix].length).join("") + suffix;
}

async function sendAndRecord(
  dependencies: TelegramPortfolioHandlerDependencies,
  identity: TelegramIdentity,
  telegramUpdateId: number,
  kind: "message" | "callback_query",
  text: string,
  inlineKeyboard?: TelegramOutboundMessage["inlineKeyboard"],
): Promise<void> {
  const delivery = await safeDelivery(dependencies.sendTelegram, {
    chatId: identity.chatId,
    text: boundedTelegramText(text),
    ...(identity.messageId ? { replyToMessageId: identity.messageId } : {}),
    ...(inlineKeyboard ? { inlineKeyboard } : {}),
  });
  await dependencies.repository.recordDelivery({
    chat_id: identity.chatId,
    user_id: identity.userId,
    update_id: telegramUpdateId,
    kind,
    status: delivery.status,
    message_id: delivery.messageId ?? null,
  });
}

function normalizedCommand(command: Record<string, unknown>, telegramDate: number): Record<string, unknown> | null {
  if (["buy", "sell", "sell_all"].includes(String(command.operation))) {
    const resolved = resolveExecutionDate(command.executed_on, telegramDate);
    return resolved.ok ? { ...command, executed_on: resolved.executedOn } : null;
  }
  if (command.operation === "plan") {
    const resolved = resolvePlanDate(command.next_due_on, telegramDate);
    return resolved.ok ? { ...command, next_due_on: resolved.nextDueOn } : null;
  }
  return command;
}

export function createTelegramPortfolioHandler(dependencies: TelegramPortfolioHandlerDependencies) {
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(dependencies.webhookSecret)) {
    throw new Error("Telegram webhook secret is malformed");
  }
  if (new TextEncoder().encode(dependencies.pairingHashPepper).byteLength < 32) {
    throw new Error("Telegram pairing pepper is too short");
  }
  const newId = dependencies.newId ?? crypto.randomUUID;
  const newCallbackToken = dependencies.newCallbackToken ?? (() => {
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  });

  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return jsonResponse(405, { ok: false });
    if (!await secureEqual(
      request.headers.get("x-telegram-bot-api-secret-token"),
      dependencies.webhookSecret,
    )) return jsonResponse(401, { ok: false });

    let update: Record<string, unknown>;
    try {
      update = await readBoundedJson(request, MAX_BODY_BYTES);
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 400;
      return jsonResponse(status, { ok: false });
    }
    const telegramUpdateId = updateId(update.update_id);
    if (telegramUpdateId === null) return jsonResponse(200, { ok: true });

    try {
      if (update.message) {
        const identity = messageIdentity(update.message);
        if (!identity) return jsonResponse(200, { ok: true });
        const message = update.message as Record<string, unknown>;
        const parsed = parsePortfolioCommand(message.text);
        if (!parsed.ok || !("command" in parsed)) {
          const link = await dependencies.repository.resolveLink({ chat_id: identity.chatId, user_id: identity.userId });
          if (link.linked !== true) return jsonResponse(200, { ok: true });
          const claim = await dependencies.repository.claimUpdate({
            chat_id: identity.chatId, user_id: identity.userId,
            update_id: telegramUpdateId, kind: "message",
          });
          if (claim.claimed === true) {
            await sendAndRecord(dependencies, identity, telegramUpdateId, "message", HELP_TEXT);
          }
          return jsonResponse(200, { ok: true });
        }

        const command = parsed.command as Record<string, unknown>;
        if (command.operation === "pair") {
          const code = String(command.code);
          const pairing = await dependencies.repository.consumePairing({
            chat_id: identity.chatId,
            user_id: identity.userId,
            update_id: telegramUpdateId,
            code_digest: await digestPairingValue(code, dependencies.pairingHashPepper),
            identity_digest: await digestPairingValue(`${identity.chatId}:${identity.userId}`, dependencies.pairingHashPepper),
            confirm_relink: command.confirm_relink === true,
          });
          const delivery = await safeDelivery(dependencies.sendTelegram, {
            chatId: identity.chatId,
            text: pairingText(pairing.status, code),
            ...(identity.messageId ? { replyToMessageId: identity.messageId } : {}),
          });
          await dependencies.repository.recordPairingDelivery({
            update_id: telegramUpdateId,
            pairing_status: pairing.status,
            status: delivery.status,
            message_id: delivery.messageId ?? null,
          });
          return jsonResponse(200, { ok: true });
        }

        const link = await dependencies.repository.resolveLink({ chat_id: identity.chatId, user_id: identity.userId });
        if (link.linked !== true) return jsonResponse(200, { ok: true });

        if (command.operation === "status") {
          const claim = await dependencies.repository.claimUpdate({
            chat_id: identity.chatId, user_id: identity.userId,
            update_id: telegramUpdateId, kind: "message",
          });
          if (claim.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message",
            "Telegram is linked. I only record confirmed activity; I cannot place or change brokerage orders.");
        } else if (command.operation === "help") {
          const claim = await dependencies.repository.claimUpdate({
            chat_id: identity.chatId, user_id: identity.userId,
            update_id: telegramUpdateId, kind: "message",
          });
          if (claim.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message", HELP_TEXT);
        } else if (command.operation === "unlink") {
          const result = await dependencies.repository.unlink({
            chat_id: identity.chatId, user_id: identity.userId, update_id: telegramUpdateId,
          });
          if (result.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message",
            "Telegram is unlinked and pending Telegram previews were cancelled. No brokerage order was changed.");
        } else if (command.operation === "portfolio") {
          const result = await dependencies.repository.portfolio({
            chat_id: identity.chatId, user_id: identity.userId, update_id: telegramUpdateId,
          });
          if (result.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message", portfolioText(result));
        } else if (command.operation === "plans") {
          const result = await dependencies.repository.plans({
            chat_id: identity.chatId, user_id: identity.userId, update_id: telegramUpdateId,
          });
          if (result.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message", plansText(result.plans));
        } else {
          const normalized = normalizedCommand(command, identity.date);
          if (!normalized) {
            const claim = await dependencies.repository.claimUpdate({
              chat_id: identity.chatId, user_id: identity.userId,
              update_id: telegramUpdateId, kind: "message",
            });
            if (claim.claimed !== true) return jsonResponse(200, { ok: true });
            await sendAndRecord(dependencies, identity, telegramUpdateId, "message",
              "The date is invalid for this record. Nothing was changed.");
            return jsonResponse(200, { ok: true });
          }
          const confirmToken = newCallbackToken();
          const cancelToken = newCallbackToken();
          if (!/^[A-Za-z0-9_-]{32}$/.test(confirmToken) || !/^[A-Za-z0-9_-]{32}$/.test(cancelToken) || confirmToken === cancelToken) {
            throw new Error("callback token generation failed");
          }
          const preview = await dependencies.repository.prepareCommand({
            chat_id: identity.chatId,
            user_id: identity.userId,
            update_id: telegramUpdateId,
            idempotency_key: newId(),
            command: normalized,
            confirm_digest: await digestPairingValue(confirmToken, dependencies.pairingHashPepper),
            cancel_digest: await digestPairingValue(cancelToken, dependencies.pairingHashPepper),
          });
          if (preview.claimed !== true) return jsonResponse(200, { ok: true });
          await sendAndRecord(dependencies, identity, telegramUpdateId, "message", previewText(preview, normalized), [[
            { text: "Confirm record", callbackData: `pc:c:${confirmToken}` },
            { text: "Cancel", callbackData: `pc:x:${cancelToken}` },
          ]]);
        }
        return jsonResponse(200, { ok: true });
      }

      if (update.callback_query) {
        const identity = callbackIdentity(update.callback_query);
        if (!identity) return jsonResponse(200, { ok: true });
        const callback = update.callback_query as Record<string, unknown>;
        const parsed = parseCallbackData(callback.data);
        if (!parsed) return jsonResponse(200, { ok: true });
        const link = await dependencies.repository.resolveLink({ chat_id: identity.chatId, user_id: identity.userId });
        if (link.linked !== true) return jsonResponse(200, { ok: true });
        const result = await dependencies.repository.applyCallback({
          chat_id: identity.chatId,
          user_id: identity.userId,
          update_id: telegramUpdateId,
          action: parsed.action,
          token_digest: await digestPairingValue(parsed.token, dependencies.pairingHashPepper),
        });
        if (result.claimed !== true) return jsonResponse(200, { ok: true });
        await sendAndRecord(dependencies, identity, telegramUpdateId, "callback_query", callbackResultText(result));
      }
      return jsonResponse(200, { ok: true });
    } catch (_error) {
      return jsonResponse(500, { ok: false });
    }
  };
}
