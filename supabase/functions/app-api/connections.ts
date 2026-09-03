const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function generateInboundConnectionSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export async function prepareConnectionCreate(
  body: Record<string, unknown>,
  createSecret: () => string = generateInboundConnectionSecret,
): Promise<{ request: Record<string, unknown>; secret: string }> {
  const secret = createSecret();
  if (!/^[A-Za-z0-9_-]{43}$/.test(secret)) throw new Error("connection secret generator returned invalid entropy");
  const digest = new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(secret),
  ));
  return {
    request: {
      ...body,
      inbound_token_digest: [...digest].map((value) => value.toString(16).padStart(2, "0")).join(""),
    },
    secret,
  };
}

export function attachGatewayCredential(
  result: Record<string, unknown>,
  secret: string,
): Record<string, unknown> {
  const data = result.data;
  if (result.ok !== true || !data || typeof data !== "object" || Array.isArray(data)) return result;
  const publicId = (data as Record<string, unknown>).public_id;
  if (typeof publicId !== "string" || !UUID.test(publicId)) throw new Error("connection receipt is invalid");
  return {
    ...result,
    data: {
      ...(data as Record<string, unknown>),
      gateway_credential: `${publicId}.${secret}`,
      credential_display: "once",
    },
  };
}
