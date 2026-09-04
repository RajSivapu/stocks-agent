import {
  createLocalJWKSet,
  exportJWK,
  generateKeyPair,
  SignJWT,
} from "jose";

import { verifyOwnerRequest } from "./auth.ts";

const OWNER = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22";
const PROJECT_URL = "https://test-project.supabase.co";
const NOW = new Date("2026-09-03T18:00:00.000Z");

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

async function fixture(overrides: Record<string, unknown> = {}) {
  const { privateKey, publicKey } = await generateKeyPair("ES256");
  const jwk = await exportJWK(publicKey);
  jwk.kid = "owner-test";
  const issuedAt = Math.floor(NOW.getTime() / 1000);
  const claims = {
    sub: OWNER,
    aud: "authenticated",
    iss: `${PROJECT_URL}/auth/v1`,
    iat: issuedAt,
    exp: issuedAt + 900,
    ...overrides,
  };
  const token = await new SignJWT(claims)
    .setProtectedHeader({ alg: "ES256", kid: "owner-test" })
    .sign(privateKey);
  return {
    request: new Request("https://api.example/v1/meta", {
      headers: { authorization: `Bearer ${token}` },
    }),
    jwks: createLocalJWKSet({ keys: [jwk] }),
  };
}

Deno.test("owner JWT requires exact issuer audience subject and bounded lifetime", async () => {
  const valid = await fixture();
  assertEquals(
    await verifyOwnerRequest(valid.request, valid.jwks, OWNER, PROJECT_URL, NOW),
    { subject: OWNER },
  );

  for (const claims of [
    { sub: "2c254745-5890-4559-bd38-ca234845c49a" },
    { aud: "anon" },
    { iss: "https://attacker.example/auth/v1" },
    { exp: Math.floor(NOW.getTime() / 1000) + 901 },
    { sub: "not-a-uuid" },
  ]) {
    const invalid = await fixture(claims);
    let failed = false;
    try {
      await verifyOwnerRequest(invalid.request, invalid.jwks, OWNER, PROJECT_URL, NOW);
    } catch {
      failed = true;
    }
    assertEquals(failed, true);
  }
});

Deno.test("owner JWT rejects missing bearer credentials", async () => {
  const valid = await fixture();
  let failed = false;
  try {
    await verifyOwnerRequest(
      new Request("https://api.example/v1/meta"),
      valid.jwks,
      OWNER,
      PROJECT_URL,
      NOW,
    );
  } catch {
    failed = true;
  }
  assertEquals(failed, true);
});
