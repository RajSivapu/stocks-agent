const encoder = new TextEncoder();
const CALLBACK = /^pc:(confirm|cancel):([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i;

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
