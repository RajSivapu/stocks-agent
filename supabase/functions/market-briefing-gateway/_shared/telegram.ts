export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export class TelegramDeliveryError extends Error {
  readonly kind: "definitive" | "ambiguous";
  readonly partialMessageIds: number[];

  constructor(kind: "definitive" | "ambiguous", partialMessageIds: number[]) {
    super(kind === "definitive"
      ? "telegram delivery was rejected"
      : "telegram delivery outcome is unknown");
    this.name = "TelegramDeliveryError";
    this.kind = kind;
    this.partialMessageIds = [...partialMessageIds];
  }
}

const TELEGRAM_TIMEOUT_MS = 25_000;
const MAX_RESPONSE_LENGTH = 65_536;

async function sendPart(
  part: string,
  chatId: string,
  token: string,
  fetchImpl: FetchLike,
): Promise<number> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TELEGRAM_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      signal: controller.signal,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: part,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
    if (!response.ok) throw new TelegramDeliveryError("definitive", []);
    const body = await response.text();
    if (body.length > MAX_RESPONSE_LENGTH) throw new TelegramDeliveryError("ambiguous", []);
    let payload: unknown;
    try {
      payload = JSON.parse(body);
    } catch {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      throw new TelegramDeliveryError("ambiguous", []);
    }
    const row = payload as Record<string, unknown>;
    const result = row.result;
    if (row.ok !== true || typeof result !== "object" || result === null || Array.isArray(result)) {
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
  if (!/^-?\d+$/.test(chatId) || token.length < 8 || token.length > 256 || /\s/.test(token) ||
    parts.length < 1 || parts.length > 4 || parts.some((part) => part.length < 1 || part.length > 3500)) {
    throw new TelegramDeliveryError("definitive", []);
  }
  const accepted: number[] = [];
  for (const part of parts) {
    try {
      accepted.push(await sendPart(part, chatId, token, fetchImpl));
    } catch (error) {
      if (accepted.length > 0) throw new TelegramDeliveryError("ambiguous", accepted);
      if (error instanceof TelegramDeliveryError) throw error;
      throw new TelegramDeliveryError("ambiguous", []);
    }
  }
  return accepted;
}

