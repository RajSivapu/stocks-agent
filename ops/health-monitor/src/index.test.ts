import {
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { runHealthCheck } from "./index.ts";


const env = {
  HEALTH_URL: "https://project-ref.supabase.co/functions/v1/app-api/healthz",
  TELEGRAM_BOT_TOKEN: "123456:" + "T".repeat(24),
  OPERATIONAL_TELEGRAM_CHAT_ID: "123456",
};

Deno.test("health monitor sends no credential to the public health endpoint", async () => {
  const requests: Request[] = [];
  const result = await runHealthCheck(env, (input, init) => {
    const request = new Request(input, init);
    requests.push(request);
    return Promise.resolve(Response.json({ ok: true, data: { status: "ok", schema_version: 1 } }));
  });
  assertEquals(result, "healthy");
  assertEquals(requests.length, 1);
  assertEquals([...requests[0].headers], []);
});

Deno.test("health monitor sends one fixed alert on unavailable or malformed health", async () => {
  const requests: Request[] = [];
  const result = await runHealthCheck(env, (input, init) => {
    const request = new Request(input, init);
    requests.push(request);
    if (request.url === env.HEALTH_URL) return Promise.resolve(new Response("unavailable", { status: 503 }));
    return Promise.resolve(Response.json({ ok: true, result: { message_id: 1 } }));
  });
  assertEquals(result, "alerted");
  assertEquals(requests.length, 2);
  const alert = await requests[1].json();
  assertEquals(alert.text, "STOCK AGENT UNAVAILABLE: the public health check failed. Check Supabase and the deployment status.");
  assertEquals(JSON.stringify(alert).includes("project-ref"), false);
});
