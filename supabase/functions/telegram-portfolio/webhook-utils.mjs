const encoder = new TextEncoder();
const CALLBACK = /^pc:(confirm|cancel):([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const OWNER_TIME_ZONE = "America/Chicago";

export async function secureEqual(received, expected) {
  if (typeof received !== "string" || typeof expected !== "string" || !received || !expected) return false;
  const [receivedHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(received)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(receivedHash);
  const right = new Uint8Array(expectedHash);
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
  return difference === 0;
}

export function ownerMatches(chatId, userId, expectedChatId, expectedUserId) {
  if (chatId === null || chatId === undefined || userId === null || userId === undefined) return false;
  return String(chatId) === String(expectedChatId) && String(userId) === String(expectedUserId);
}

export function parseCallbackData(value) {
  if (typeof value !== "string") return null;
  const match = value.match(CALLBACK);
  return match ? { action: match[1].toLowerCase(), commandId: match[2].toLowerCase() } : null;
}

function validTradeDate(value) {
  if (typeof value !== "string" || !ISO_DATE.test(value) || value < "2000-01-01") return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function ownerDate(unixSeconds) {
  if (!Number.isSafeInteger(unixSeconds) || unixSeconds <= 0) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: OWNER_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(unixSeconds * 1000));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function resolveExecutionDate(explicitDate, telegramUnixSeconds) {
  const reportedOn = ownerDate(telegramUnixSeconds);
  if (!reportedOn) return { ok: false };
  if (explicitDate === undefined || explicitDate === null) {
    return { ok: true, executedOn: reportedOn };
  }
  if (!validTradeDate(explicitDate) || explicitDate > reportedOn) return { ok: false };
  return { ok: true, executedOn: explicitDate };
}

export function resolvePlanDate(explicitDate, telegramUnixSeconds) {
  const reportedOn = ownerDate(telegramUnixSeconds);
  if (!reportedOn || !validTradeDate(explicitDate) || explicitDate < reportedOn) {
    return { ok: false };
  }
  return { ok: true, nextDueOn: explicitDate };
}
