import {
  jwtVerify,
  type JWTVerifyGetKey,
} from "npm:jose@6.2.2";

import { DashboardHttpError } from "./errors.ts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const ALGORITHMS = ["ES256", "RS256", "EdDSA"];
const MAX_TOKEN_SECONDS = 900;

export interface VerifiedOwner {
  subject: string;
}

export async function verifyOwnerRequest(
  request: Request,
  jwks: JWTVerifyGetKey,
  ownerUserId: string,
  projectUrl: string,
  now = new Date(),
): Promise<VerifiedOwner> {
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([A-Za-z0-9._~-]+)$/.exec(authorization);
  if (!match) {
    throw new DashboardHttpError(401, "unauthorized", "Sign in is required.");
  }
  const token = match[1];
  if (!token) {
    throw new DashboardHttpError(401, "unauthorized", "Sign in is required.");
  }
  if (!UUID_PATTERN.test(ownerUserId)) {
    throw new DashboardHttpError(503, "temporarily_unavailable", "Dashboard access is unavailable.");
  }

  let issuer: string;
  try {
    const parsed = new URL(projectUrl);
    if (parsed.protocol !== "https:" || parsed.origin !== projectUrl || !parsed.hostname.endsWith(".supabase.co")) {
      throw new Error("invalid project URL");
    }
    issuer = `${parsed.origin}/auth/v1`;
  } catch {
    throw new DashboardHttpError(503, "temporarily_unavailable", "Dashboard access is unavailable.");
  }

  let payload;
  try {
    ({ payload } = await jwtVerify(token, jwks, {
      algorithms: ALGORITHMS,
      issuer,
      audience: "authenticated",
      currentDate: now,
      clockTolerance: 0,
      maxTokenAge: `${MAX_TOKEN_SECONDS}s`,
    }));
  } catch {
    throw new DashboardHttpError(401, "unauthorized", "Your session is invalid or expired.");
  }

  if (
    typeof payload.sub !== "string" ||
    !UUID_PATTERN.test(payload.sub) ||
    typeof payload.iat !== "number" ||
    typeof payload.exp !== "number" ||
    payload.exp <= payload.iat ||
    payload.exp - payload.iat > MAX_TOKEN_SECONDS
  ) {
    throw new DashboardHttpError(401, "unauthorized", "Your session is invalid or expired.");
  }
  if (payload.sub !== ownerUserId) {
    throw new DashboardHttpError(403, "owner_only", "This dashboard is restricted to its owner.");
  }
  return { subject: payload.sub };
}
