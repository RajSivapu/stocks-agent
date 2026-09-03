const PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PAIRING_CODE = /^[A-HJ-NP-Z2-9]{10}$/;
const encoder = new TextEncoder();


export function normalizePairingCode(value) {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toUpperCase();
  return PAIRING_CODE.test(normalized) ? normalized : null;
}

export function generatePairingCode(randomBytes = crypto.getRandomValues(new Uint8Array(10))) {
  if (!(randomBytes instanceof Uint8Array) || randomBytes.length < 10) {
    throw new Error("ten random bytes are required");
  }
  let code = "";
  for (let index = 0; index < 10; index += 1) {
    code += PAIRING_ALPHABET[randomBytes[index] & 31];
  }
  return code;
}

export async function digestPairingValue(value, pepper) {
  if (typeof value !== "string" || !value || typeof pepper !== "string" ||
      encoder.encode(pepper).byteLength < 32) {
    throw new Error("pairing digest input is invalid");
  }
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function generateCallbackToken(randomBytes = crypto.getRandomValues(new Uint8Array(24))) {
  if (!(randomBytes instanceof Uint8Array) || randomBytes.length !== 24) {
    throw new Error("twenty-four random bytes are required");
  }
  let binary = "";
  for (const byte of randomBytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}
