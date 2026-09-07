import { createGatewayHandler } from "../../supabase/functions/market-briefing-gateway/_shared/handler.ts";
import {
  canonicalJson,
  sha256Hex,
} from "../../supabase/functions/market-briefing-gateway/_shared/intelligence.ts";
import { createSupabaseGatewayRepository } from "../../supabase/functions/market-briefing-gateway/_shared/repository.ts";

const [rpcEndpoint, fixturePath] = Deno.args;
const fixture = JSON.parse(await Deno.readTextFile(fixturePath)) as {
  run_id: string;
  secret: string;
  capability_id: string;
  envelopes: string[];
};

const rawClient = {
  async rpc(name: string, parameters: Record<string, unknown>) {
    const response = await fetch(rpcEndpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: canonicalJson({ name, parameters }),
    });
    return await response.json();
  },
  from() {
    throw new Error("reference transfer must use only protected RPCs");
  },
};
const repository = createSupabaseGatewayRepository(rawClient);
const handler = createGatewayHandler({
  repository,
  marketAgentSecret: fixture.secret,
  telegramToken: "unused",
  telegramChatId: "unused",
});
let calls = 0;
let totalBytes = 0;
let maxRequestBytes = 0;

async function invoke(encoded: string): Promise<Record<string, unknown>> {
  const bytes = new TextEncoder().encode(encoded).byteLength;
  calls += 1;
  totalBytes += bytes;
  maxRequestBytes = Math.max(maxRequestBytes, bytes);
  const response = await handler(
    new Request("https://example.invalid/gateway", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-market-agent-secret": fixture.secret,
      },
      body: encoded,
    }),
  );
  const body = await response.json() as Record<string, unknown>;
  if (response.status !== 200 || body.ok !== true) {
    throw new Error(
      `gateway operation failed: ${response.status} ${JSON.stringify(body)}`,
    );
  }
  return body;
}

for (const envelope of fixture.envelopes) await invoke(envelope);

let after: string | null = null;
let pageCount = 0;
let maxPageBytes = 0;
const membershipFrames: string[] = [];
while (true) {
  const requestId = `eeeeeeee-eeee-4eee-8eee-${
    String(pageCount + 1).padStart(12, "0")
  }`;
  const encoded = canonicalJson({
    dry_run: false,
    operation: "read_discovery_reference",
    payload: {
      capability_id: fixture.capability_id,
      binding_role: "current",
      after_security_id: after,
      limit: 500,
    },
    request_id: requestId,
    run_id: fixture.run_id,
    schema_version: 1,
  });
  const body = await invoke(encoded);
  const reference = body.reference as Record<string, unknown>;
  const securities = reference.securities as Array<Record<string, unknown>>;
  maxPageBytes = Math.max(
    maxPageBytes,
    new TextEncoder().encode(canonicalJson(reference)).byteLength,
  );
  for (const row of securities) {
    membershipFrames.push(
      `${row.security_id}\u001f${row.id}\u001f${row.content_hash}`,
    );
  }
  pageCount += 1;
  if (reference.complete === true) break;
  after = reference.next_after_security_id as string;
}

await Deno.stdout.write(new TextEncoder().encode(
  canonicalJson({
    calls,
    total_bytes: totalBytes,
    max_request_bytes: maxRequestBytes,
    max_page_bytes: maxPageBytes,
    pages: pageCount,
    members: membershipFrames.length,
    membership_hash: sha256Hex(membershipFrames.join("\n")),
  }) + "\n",
));
