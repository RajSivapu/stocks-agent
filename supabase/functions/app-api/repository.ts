import type { AppApiRepository } from "./handler.ts";


async function boundedResult(response: Response): Promise<Record<string, unknown>> {
  if (!response.ok || response.body === null) throw new Error("app API database request failed");
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > 64 * 1024)) {
    throw new Error("app API database response is too large");
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > 64 * 1024) throw new Error("app API database response is too large");
  const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("app API database response is invalid");
  }
  return value as Record<string, unknown>;
}

export function createAppApiRepository(
  projectUrl: string,
  anonKey: string,
  fetcher: typeof fetch = fetch,
): AppApiRepository {
  const endpoint = `${projectUrl.replace(/\/$/, "")}/rest/v1/rpc/app_dispatch`;
  if (!projectUrl.startsWith("https://") || !anonKey || anonKey.length > 16_384) {
    throw new Error("app API repository configuration is invalid");
  }
  return {
    async dispatch(input) {
      const bearer = input.bearerToken;
      if (typeof bearer !== "string") throw new Error("app API bearer token is missing");
      const response = await fetcher(endpoint, {
        method: "POST",
        headers: {
          apikey: anonKey,
          authorization: `Bearer ${bearer}`,
          "content-type": "application/json",
          "content-profile": "api",
          accept: "application/vnd.pgrst.object+json",
          "accept-profile": "api",
        },
        body: JSON.stringify({
          p_route: input.route,
          p_request_id: input.requestId,
          p_ip_digest: input.ipDigest,
          p_request: input.body,
        }),
        signal: AbortSignal.timeout(5_000),
      });
      return await boundedResult(response);
    },
  };
}
