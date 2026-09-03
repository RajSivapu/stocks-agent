import {
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { createTelegramSender } from "./telegram-client.ts";


const TOKEN = `12345:${"a".repeat(24)}`;
const MESSAGE = {
  chatId: 123,
  text: "Fixed record-only text",
  replyToMessageId: 12,
  inlineKeyboard: [[{ text: "Confirm record", callbackData: `pc:c:${"A".repeat(32)}` }]],
};

Deno.test("Telegram sender maps fixed message fields and returns a delivered receipt", async () => {
  let observed: Request | undefined;
  const sender = createTelegramSender(TOKEN, ((input, init) => {
    observed = new Request(input, init);
    return Promise.resolve(Response.json({ ok: true, result: { message_id: 77 } }));
  }) as typeof fetch);
  assertEquals(await sender(MESSAGE), { status: "delivered", messageId: 77 });
  const payload = await observed?.json();
  assertEquals(payload, {
    chat_id: 123,
    text: "Fixed record-only text",
    disable_web_page_preview: true,
    reply_parameters: { message_id: 12, allow_sending_without_reply: true },
    reply_markup: {
      inline_keyboard: [[{ text: "Confirm record", callback_data: `pc:c:${"A".repeat(32)}` }]],
    },
  });
  assertEquals(JSON.stringify(payload).includes(TOKEN), false);
});

Deno.test("definitive Telegram rejection is failed and is not retried", async () => {
  let calls = 0;
  const sender = createTelegramSender(TOKEN, (() => {
    calls += 1;
    return Promise.resolve(Response.json({ ok: false }, { status: 400 }));
  }) as typeof fetch);
  assertEquals(await sender(MESSAGE), { status: "delivery_failed" });
  assertEquals(calls, 1);
});

Deno.test("transport and malformed success states are unknown and are not retried", async () => {
  for (const fetcher of [
    (() => Promise.reject(new Error("disconnect"))) as typeof fetch,
    (() => Promise.resolve(new Response("not-json", { status: 200 }))) as typeof fetch,
    (() => Promise.resolve(Response.json({ ok: true, result: {} }))) as typeof fetch,
  ]) {
    let calls = 0;
    const sender = createTelegramSender(TOKEN, ((input, init) => {
      calls += 1;
      return fetcher(input, init);
    }) as typeof fetch);
    assertEquals(await sender(MESSAGE), { status: "delivery_unknown" });
    assertEquals(calls, 1);
  }
});

Deno.test("server failures and malformed intermediary rejections are delivery unknown", async () => {
  for (const response of [
    Response.json({ ok: false }, { status: 500 }),
    new Response("gateway says no", { status: 400 }),
  ]) {
    const sender = createTelegramSender(TOKEN, (() => Promise.resolve(response.clone())) as typeof fetch);
    assertEquals(await sender(MESSAGE), { status: "delivery_unknown" });
  }
});
