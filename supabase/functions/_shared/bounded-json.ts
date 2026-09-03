import { HttpError, requireJsonContentType } from "./errors.ts";


const MAX_ALLOWED_BOUND = 1024 * 1024;

export async function readBoundedJson(
  request: Request,
  maxBytes: number,
): Promise<Record<string, unknown>> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > MAX_ALLOWED_BOUND) {
    throw new Error("invalid JSON byte ceiling");
  }
  requireJsonContentType(request);
  const declaredText = request.headers.get("content-length");
  if (declaredText !== null) {
    if (!/^\d+$/.test(declaredText) || Number(declaredText) > maxBytes) {
      throw new HttpError(413, "BODY_TOO_LARGE", "request body is too large");
    }
  }
  if (request.body === null) {
    throw new HttpError(400, "INVALID_JSON", "request body is not valid JSON");
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > maxBytes) {
      await reader.cancel().catch(() => undefined);
      throw new HttpError(413, "BODY_TOO_LARGE", "request body is too large");
    }
    chunks.push(value);
  }

  const body = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(body);
    const parsed: unknown = JSON.parse(text);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("JSON body must be an object");
    }
    return parsed as Record<string, unknown>;
  } catch (_error) {
    throw new HttpError(400, "INVALID_JSON", "request body is not valid JSON");
  }
}
