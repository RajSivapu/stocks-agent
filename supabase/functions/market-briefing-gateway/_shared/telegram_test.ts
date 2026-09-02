import { sendTelegramParts, TelegramDeliveryError } from "./telegram.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

async function capture(callback: () => Promise<unknown>): Promise<TelegramDeliveryError> {
  try {
    await callback();
  } catch (error) {
    if (error instanceof TelegramDeliveryError) return error;
    throw error;
  }
  throw new Error("expected TelegramDeliveryError");
}

Deno.test("Telegram accepts all JSON parts and returns integer message IDs", async () => {
  const requests: Array<Record<string, unknown>> = [];
  let nextId = 10;
  const ids = await sendTelegramParts(["one", "two"], "123", "secret-token", (_input, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return Promise.resolve(new Response(JSON.stringify({ ok: true, result: { message_id: nextId++ } })));
  });
  assertEquals(ids, [10, 11]);
  assertEquals(requests, [
    { chat_id: "123", text: "one", parse_mode: "HTML", disable_web_page_preview: true },
    { chat_id: "123", text: "two", parse_mode: "HTML", disable_web_page_preview: true },
  ]);
});

Deno.test("HTTP rejection before acceptance is definitive and redacted", async () => {
  const error = await capture(() => sendTelegramParts(["one"], "123", "secret-token", () =>
    Promise.resolve(new Response("secret-token provider body", { status: 400 }))));
  assertEquals(error.kind, "definitive");
  assertEquals(error.partialMessageIds, []);
  assert(!error.message.includes("secret-token"), "secret leaked into error");
  assert(!error.message.includes("provider body"), "body leaked into error");
});

Deno.test("fetch failure is ambiguous", async () => {
  const error = await capture(() => sendTelegramParts(["one"], "123", "secret-token", () => {
    throw new Error("network included secret-token");
  }));
  assertEquals(error.kind, "ambiguous");
  assertEquals(error.partialMessageIds, []);
  assert(!error.message.includes("secret-token"), "fetch error leaked");
});

Deno.test("any later failure is ambiguous and preserves partial IDs", async () => {
  let count = 0;
  const error = await capture(() => sendTelegramParts(["one", "two"], "123", "secret-token", () => {
    count += 1;
    return Promise.resolve(count === 1
      ? new Response(JSON.stringify({ ok: true, result: { message_id: 42 } }))
      : new Response("bad", { status: 400 }));
  }));
  assertEquals(error.kind, "ambiguous");
  assertEquals(error.partialMessageIds, [42]);
});

Deno.test("malformed success response is ambiguous", async () => {
  const error = await capture(() => sendTelegramParts(["one"], "123", "secret-token", () =>
    Promise.resolve(new Response(JSON.stringify({ ok: true, result: {} })))));
  assertEquals(error.kind, "ambiguous");
  assert(error.message === "telegram delivery outcome is unknown", "unexpected error detail");
});
