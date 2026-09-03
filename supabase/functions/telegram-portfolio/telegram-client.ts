import type { TelegramDelivery, TelegramOutboundMessage } from "./handler.ts";


const MAX_RESPONSE_BYTES = 64 * 1024;

export function createTelegramSender(botToken: string, fetcher: typeof fetch = fetch) {
  if (!/^[1-9][0-9]{3,15}:[A-Za-z0-9_-]{20,}$/.test(botToken)) {
    throw new Error("Telegram bot token is malformed");
  }
  return async (message: TelegramOutboundMessage): Promise<TelegramDelivery> => {
    const payload = {
      chat_id: message.chatId,
      text: message.text,
      disable_web_page_preview: true,
      ...(message.replyToMessageId
        ? { reply_parameters: { message_id: message.replyToMessageId, allow_sending_without_reply: true } }
        : {}),
      ...(message.inlineKeyboard
        ? {
          reply_markup: {
            inline_keyboard: message.inlineKeyboard.map((row) => row.map((button) => ({
              text: button.text,
              callback_data: button.callbackData,
            }))),
          },
        }
        : {}),
    };
    let response: Response;
    try {
      response = await fetcher(`https://api.telegram.org/bot${botToken}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(5_000),
      });
    } catch (_error) {
      return { status: "delivery_unknown" };
    }
    const declared = response.headers.get("content-length");
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)) {
      return { status: "delivery_unknown" };
    }
    let bytes: Uint8Array;
    try {
      bytes = new Uint8Array(await response.arrayBuffer());
    } catch (_error) {
      return { status: "delivery_unknown" };
    }
    if (bytes.byteLength > MAX_RESPONSE_BYTES) return { status: "delivery_unknown" };
    let body: unknown;
    try {
      body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch (_error) {
      return { status: "delivery_unknown" };
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) return { status: "delivery_unknown" };
    const result = body as Record<string, unknown>;
    if (!response.ok) {
      return response.status >= 400 && response.status < 500 && result.ok === false
        ? { status: "delivery_failed" }
        : { status: "delivery_unknown" };
    }
    if (result.ok === false) return { status: "delivery_unknown" };
    if (result.ok !== true || !result.result || typeof result.result !== "object" || Array.isArray(result.result)) {
      return { status: "delivery_unknown" };
    }
    const messageId = (result.result as Record<string, unknown>).message_id;
    return safeMessageId(messageId)
      ? { status: "delivered", messageId }
      : { status: "delivery_unknown" };
  };
}

function safeMessageId(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}
