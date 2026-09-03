import {
  createLocalJWKSet,
  decodeProtectedHeader,
  jwtVerify,
  type JSONWebKeySet,
  type JWTPayload,
} from "npm:jose@6.2.10";

import { HttpError } from "./errors.ts";


const ASYMMETRIC_ALGORITHMS = ["ES256", "RS256", "EdDSA"] as const;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export type AuthenticatedClaims = JWTPayload & {
  sub: string;
  role: "authenticated";
};

export type JwtVerificationOptions = {
  projectUrl: string;
  audience?: string;
  now?: Date;
  maxTokenLifetimeSeconds?: number;
};

function canonicalProjectUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch (_error) {
    throw new Error("Supabase project URL is malformed");
  }
  if (
    url.protocol !== "https:" || !url.hostname.endsWith(".supabase.co") ||
    url.port || url.username || url.password || !["", "/"].includes(url.pathname) ||
    url.search || url.hash
  ) {
    throw new Error("Supabase project URL must be canonical HTTPS");
  }
  return value.replace(/\/$/, "");
}

function bearerToken(request: Request): string {
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/.exec(authorization);
  if (!match || match[1].length > 16_384) {
    throw new HttpError(401, "UNAUTHORIZED", "unauthorized");
  }
  return match[1];
}

export async function verifyUserJwt(
  request: Request,
  jwks: JSONWebKeySet,
  options: JwtVerificationOptions,
): Promise<AuthenticatedClaims> {
  try {
    const projectUrl = canonicalProjectUrl(options.projectUrl);
    const token = bearerToken(request);
    const protectedHeader = decodeProtectedHeader(token);
    if (
      typeof protectedHeader.kid !== "string" || !protectedHeader.kid ||
      protectedHeader.typ !== "JWT" ||
      !ASYMMETRIC_ALGORITHMS.includes(protectedHeader.alg as typeof ASYMMETRIC_ALGORITHMS[number])
    ) {
      throw new Error("unsupported JWT header");
    }
    const result = await jwtVerify(token, createLocalJWKSet(jwks), {
      algorithms: [...ASYMMETRIC_ALGORITHMS],
      issuer: `${projectUrl}/auth/v1`,
      audience: options.audience ?? "authenticated",
      currentDate: options.now,
      clockTolerance: 5,
    });
    const { payload } = result;
    const maxLifetime = options.maxTokenLifetimeSeconds ?? 15 * 60;
    if (
      payload.role !== "authenticated" || typeof payload.sub !== "string" ||
      !UUID_RE.test(payload.sub) || !Number.isSafeInteger(payload.iat) ||
      !Number.isSafeInteger(payload.exp) || (payload.exp as number) <= (payload.iat as number) ||
      (payload.exp as number) - (payload.iat as number) > maxLifetime
    ) {
      throw new Error("unsupported JWT claims");
    }
    return payload as AuthenticatedClaims;
  } catch (_error) {
    throw new HttpError(401, "UNAUTHORIZED", "unauthorized");
  }
}

export async function fetchProjectJwks(
  projectUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<JSONWebKeySet> {
  const endpoint = `${canonicalProjectUrl(projectUrl)}/auth/v1/.well-known/jwks.json`;
  try {
    const response = await fetcher(endpoint, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok || response.body === null) throw new Error("JWKS request failed");
    const declared = response.headers.get("content-length");
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > 64 * 1024)) {
      throw new Error("JWKS response is too large");
    }
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > 64 * 1024) {
        await reader.cancel().catch(() => undefined);
        throw new Error("JWKS response is too large");
      }
      chunks.push(value);
    }
    const body = new Uint8Array(received);
    let offset = 0;
    for (const chunk of chunks) {
      body.set(chunk, offset);
      offset += chunk.byteLength;
    }
    const parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    if (!parsed || !Array.isArray(parsed.keys) || parsed.keys.length < 1 || parsed.keys.length > 10) {
      throw new Error("invalid JWKS");
    }
    return parsed as JSONWebKeySet;
  } catch (_error) {
    throw new HttpError(503, "AUTH_KEYS_UNAVAILABLE", "authentication keys unavailable");
  }
}

export class ProjectJwksCache {
  readonly #projectUrl: string;
  readonly #ttlMs: number;
  readonly #fetcher: typeof fetch;
  readonly #now: () => number;
  #cached: { value: JSONWebKeySet; expiresAt: number } | undefined;
  #inFlight: Promise<JSONWebKeySet> | undefined;

  constructor(
    projectUrl: string,
    options: {
      ttlMs?: number;
      fetcher?: typeof fetch;
      now?: () => number;
    } = {},
  ) {
    this.#projectUrl = canonicalProjectUrl(projectUrl);
    this.#ttlMs = options.ttlMs ?? 5 * 60_000;
    if (!Number.isSafeInteger(this.#ttlMs) || this.#ttlMs < 1_000 || this.#ttlMs > 10 * 60_000) {
      throw new Error("JWKS cache TTL is outside the reviewed range");
    }
    this.#fetcher = options.fetcher ?? fetch;
    this.#now = options.now ?? Date.now;
  }

  async get(): Promise<JSONWebKeySet> {
    const now = this.#now();
    if (this.#cached && now < this.#cached.expiresAt) return this.#cached.value;
    if (this.#inFlight) return await this.#inFlight;
    this.#inFlight = fetchProjectJwks(this.#projectUrl, this.#fetcher)
      .then((value) => {
        this.#cached = { value, expiresAt: this.#now() + this.#ttlMs };
        return value;
      })
      .finally(() => {
        this.#inFlight = undefined;
      });
    return await this.#inFlight;
  }

  clear(): void {
    this.#cached = undefined;
  }
}
