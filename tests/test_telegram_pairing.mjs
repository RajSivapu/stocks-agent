import assert from "node:assert/strict";
import test from "node:test";

import {
  digestPairingValue,
  generateCallbackToken,
  generatePairingCode,
  normalizePairingCode,
} from "../supabase/functions/telegram-portfolio/pairing.mjs";


test("pairing code is ten unambiguous base32 characters", () => {
  const code = generatePairingCode(Uint8Array.from([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]));
  assert.match(code, /^[A-HJ-NP-Z2-9]{10}$/);
  assert.equal(normalizePairingCode(code.toLowerCase()), code);
  assert.equal(normalizePairingCode("ABCD123456"), null);
  assert.equal(normalizePairingCode("ABCD23456"), null);
});

test("pairing and Telegram identity digests are deterministic HMACs", async () => {
  const first = await digestPairingValue("ABCD234567", "a-pairing-pepper-that-is-at-least-32-bytes");
  const second = await digestPairingValue("ABCD234567", "a-pairing-pepper-that-is-at-least-32-bytes");
  const other = await digestPairingValue("ABCD234568", "a-pairing-pepper-that-is-at-least-32-bytes");
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.equal(first, second);
  assert.notEqual(first, other);
});

test("callback tokens are opaque, URL-safe, and fit Telegram's limit", () => {
  const token = generateCallbackToken(Uint8Array.from({ length: 24 }, (_, index) => index));
  assert.match(token, /^[A-Za-z0-9_-]{32}$/);
  assert(Buffer.byteLength(`pc:c:${token}`) <= 64);
});
