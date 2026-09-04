export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export class TelegramDeliveryError extends Error {
  readonly kind: "definitive" | "ambiguous";
  readonly partialMessageIds: number[];

  constructor(kind: "definitive" | "ambiguous", partialMessageIds: number[]) {
    super(
      kind === "definitive" ? "telegram delivery was rejected" : "telegram delivery outcome is unknown",
    );
    this.name = "TelegramDeliveryError";
    this.kind = kind;
    this.partialMessageIds = [...partialMessageIds];
  }
}

const TELEGRAM_TIMEOUT_MS = 25_000;
const MAX_RESPONSE_LENGTH = 65_536;
const ALERT_CALLBACK =
  /^al:(arm|dismiss|pause|resume|ack|snooze20m|snooze1d):[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}:[1-9]\d{0,6}$/i;

export interface TelegramAlertInput {
  body: string;
  reply_markup: {
    inline_keyboard: Array<Array<{ text: string; callback_data: string }>>;
  };
}

export interface TelegramAlertReceipt {
  status: "accepted_by_telegram";
  message_id: number;
  accepted_at: string;
}

export interface TelegramReportDelivery {
  status: "ready" | "suppressed";
  body: string;
  parts: string[];
  reason?: string;
}

export async function sendTelegramReport(
  delivery: TelegramReportDelivery,
  chatId: string,
  token: string,
  fetchImpl: FetchLike = fetch,
): Promise<
  { status: "suppressed" | "accepted_by_telegram"; message_ids: number[] }
> {
  if (delivery.status === "suppressed") {
    if (delivery.body !== "" || delivery.parts.length !== 0) {
      throw new TelegramDeliveryError("definitive", []);
    }
    return { status: "suppressed", message_ids: [] };
  }
  if (
    delivery.body.length < 1 || delivery.body.length > 1_200 ||
    delivery.parts.length !== 1 || delivery.parts[0] !== delivery.body
  ) throw new TelegramDeliveryError("definitive", []);
  return {
    status: "accepted_by_telegram",
    message_ids: await sendTelegramParts(
      delivery.parts,
      chatId,
      token,
      fetchImpl,
    ),
  };
}

async function sendPart(
  part: string,
  chatId: string,
  token: string,
  fetchImpl: FetchLike,
  replyMarkup?: TelegramAlertInput["reply_markup"],
): Promise<number> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TELEGRAM_TIMEOUT_MS);
  try {
    const response = await fetchImpl(
      `https://api.telegram.org/bot${token}/sendMessage`,
      {
        method: "POST",
        signal: controller.signal,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: part,
          parse_mode: "HTML",
          disable_web_page_preview: true,
          ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
        }),
      },
    );
    if (!response.ok) throw new TelegramDeliveryError("definitive", []);
    const body = await response.text();
    if (body.length > MAX_RESPONSE_LENGTH) {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    let payload: unknown;
    try {
      payload = JSON.parse(body);
    } catch {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    if (
      typeof payload !== "object" || payload === null || Array.isArray(payload)
    ) {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    const row = payload as Record<string, unknown>;
    const result = row.result;
    if (
      row.ok !== true || typeof result !== "object" || result === null ||
      Array.isArray(result)
    ) {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    const messageId = (result as Record<string, unknown>).message_id;
    if (typeof messageId !== "number" || !Number.isSafeInteger(messageId)) {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    return messageId;
  } catch (error) {
    if (error instanceof TelegramDeliveryError) throw error;
    throw new TelegramDeliveryError("ambiguous", []);
  } finally {
    clearTimeout(timeout);
  }
}

export async function sendTelegramParts(
  parts: readonly string[],
  chatId: string,
  token: string,
  fetchImpl: FetchLike = fetch,
): Promise<number[]> {
  if (
    !/^-?\d+$/.test(chatId) || token.length < 8 || token.length > 256 ||
    /\s/.test(token) ||
    parts.length < 1 || parts.length > 4 ||
    parts.some((part) => part.length < 1 || part.length > 3500)
  ) {
    throw new TelegramDeliveryError("definitive", []);
  }
  const accepted: number[] = [];
  for (const part of parts) {
    try {
      accepted.push(await sendPart(part, chatId, token, fetchImpl));
    } catch (error) {
      if (accepted.length > 0) {
        throw new TelegramDeliveryError("ambiguous", accepted);
      }
      if (error instanceof TelegramDeliveryError) throw error;
      throw new TelegramDeliveryError("ambiguous", []);
    }
  }
  return accepted;
}

function validAlertMarkup(markup: TelegramAlertInput["reply_markup"]): boolean {
  if (
    !Array.isArray(markup?.inline_keyboard) ||
    markup.inline_keyboard.length !== 1
  ) return false;
  const buttons = markup.inline_keyboard[0];
  return Array.isArray(buttons) && buttons.length >= 1 && buttons.length <= 3 &&
    buttons.every((button) =>
      button && typeof button.text === "string" && button.text.length >= 1 &&
      button.text.length <= 40 &&
      !/\b(?:buy|sell|order)\b/i.test(button.text) &&
      typeof button.callback_data === "string" &&
      button.callback_data.length <= 64 &&
      ALERT_CALLBACK.test(button.callback_data)
    );
}

export async function sendTelegramAlert(
  alert: TelegramAlertInput,
  chatId: string,
  token: string,
  fetchImpl: FetchLike = fetch,
  now: () => Date = () => new Date(),
): Promise<TelegramAlertReceipt> {
  if (
    !/^-?\d+$/.test(chatId) || token.length < 8 || token.length > 256 ||
    /\s/.test(token) ||
    typeof alert?.body !== "string" || alert.body.length < 1 ||
    alert.body.length > 3_500 ||
    !validAlertMarkup(alert.reply_markup)
  ) {
    throw new TelegramDeliveryError("definitive", []);
  }
  const messageId = await sendPart(
    alert.body,
    chatId,
    token,
    fetchImpl,
    alert.reply_markup,
  );
  const acceptedAt = now();
  if (Number.isNaN(acceptedAt.valueOf())) {
    throw new TelegramDeliveryError("ambiguous", [messageId]);
  }
  return {
    status: "accepted_by_telegram",
    message_id: messageId,
    accepted_at: acceptedAt.toISOString(),
  };
}
