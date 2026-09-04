import { sendTelegramAlert, sendTelegramParts, sendTelegramReport, TelegramDeliveryError } from "./telegram.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}

async function capture(
  callback: () => Promise<unknown>,
): Promise<TelegramDeliveryError> {
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
  const ids = await sendTelegramParts(
    ["one", "two"],
    "123",
    "secret-token",
    (_input, init) => {
      requests.push(JSON.parse(String(init?.body)));
      return Promise.resolve(
        new Response(
          JSON.stringify({ ok: true, result: { message_id: nextId++ } }),
        ),
      );
    },
  );
  assertEquals(ids, [10, 11]);
  assertEquals(requests, [
    {
      chat_id: "123",
      text: "one",
      parse_mode: "HTML",
      disable_web_page_preview: true,
    },
    {
      chat_id: "123",
      text: "two",
      parse_mode: "HTML",
      disable_web_page_preview: true,
    },
  ]);
});

Deno.test("HTTP rejection before acceptance is definitive and redacted", async () => {
  const error = await capture(() =>
    sendTelegramParts(
      ["one"],
      "123",
      "secret-token",
      () =>
        Promise.resolve(
          new Response("secret-token provider body", { status: 400 }),
        ),
    )
  );
  assertEquals(error.kind, "definitive");
  assertEquals(error.partialMessageIds, []);
  assert(!error.message.includes("secret-token"), "secret leaked into error");
  assert(!error.message.includes("provider body"), "body leaked into error");
});

Deno.test("fetch failure is ambiguous", async () => {
  const error = await capture(() =>
    sendTelegramParts(["one"], "123", "secret-token", () => {
      throw new Error("network included secret-token");
    })
  );
  assertEquals(error.kind, "ambiguous");
  assertEquals(error.partialMessageIds, []);
  assert(!error.message.includes("secret-token"), "fetch error leaked");
});

Deno.test("any later failure is ambiguous and preserves partial IDs", async () => {
  let count = 0;
  const error = await capture(() =>
    sendTelegramParts(["one", "two"], "123", "secret-token", () => {
      count += 1;
      return Promise.resolve(
        count === 1
          ? new Response(
            JSON.stringify({ ok: true, result: { message_id: 42 } }),
          )
          : new Response("bad", { status: 400 }),
      );
    })
  );
  assertEquals(error.kind, "ambiguous");
  assertEquals(error.partialMessageIds, [42]);
});

Deno.test("malformed success response is ambiguous", async () => {
  const error = await capture(() =>
    sendTelegramParts(
      ["one"],
      "123",
      "secret-token",
      () => Promise.resolve(new Response(JSON.stringify({ ok: true, result: {} }))),
    )
  );
  assertEquals(error.kind, "ambiguous");
  assert(
    error.message === "telegram delivery outcome is unknown",
    "unexpected error detail",
  );
});

Deno.test("suppressed report is never sent and an unknown result is never called delivered", async () => {
  let called = false;
  const suppressed = await sendTelegramReport(
    { status: "suppressed", body: "", parts: [], reason: "no_trigger" },
    "123",
    "secret-token",
    () => {
      called = true;
      return Promise.resolve(new Response("{}"));
    },
  );
  assertEquals(suppressed, { status: "suppressed", message_ids: [] });
  assert(!called, "suppressed report made a Telegram call");
  const error = await capture(() =>
    sendTelegramReport(
      { status: "ready", body: "brief", parts: ["brief"] },
      "123",
      "secret-token",
      () => {
        throw new Error("unknown network outcome");
      },
    )
  );
  assertEquals(error.kind, "ambiguous");
});

Deno.test("Telegram alert sender includes owner actions and returns API-acceptance receipt", async () => {
  const requests: Array<Record<string, unknown>> = [];
  const receipt = await sendTelegramAlert(
    {
      body: "<b>v3 alert</b>",
      reply_markup: {
        inline_keyboard: [[
          {
            text: "Acknowledge",
            callback_data: "al:ack:7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
          },
          {
            text: "Dismiss",
            callback_data: "al:dismiss:7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
          },
        ]],
      },
    },
    "123",
    "secret-token",
    (_input, init) => {
      requests.push(JSON.parse(String(init?.body)));
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, result: { message_id: 77 } })),
      );
    },
    () => new Date("2026-09-03T17:15:06.000Z"),
  );
  assertEquals(receipt, {
    status: "accepted_by_telegram",
    message_id: 77,
    accepted_at: "2026-09-03T17:15:06.000Z",
  });
  assertEquals(requests, [{
    chat_id: "123",
    text: "<b>v3 alert</b>",
    parse_mode: "HTML",
    disable_web_page_preview: true,
    reply_markup: {
      inline_keyboard: [[
        {
          text: "Acknowledge",
          callback_data: "al:ack:7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
        },
        {
          text: "Dismiss",
          callback_data: "al:dismiss:7f7f70bf-5cec-4f1e-9de8-ec8823d99fc7:3",
        },
      ]],
    },
  }]);
});

Deno.test("Telegram alert sender rejects unsafe markup before any network call", async () => {
  let called = false;
  const error = await capture(() =>
    sendTelegramAlert(
      {
        body: "alert",
        reply_markup: {
          inline_keyboard: [[{
            text: "Buy",
            callback_data: "not-an-alert-action",
          }]],
        },
      },
      "123",
      "secret-token",
      () => {
        called = true;
        return Promise.resolve(new Response("{}"));
      },
    )
  );
  assertEquals(error.kind, "definitive");
  assert(!called, "invalid alert made a network call");
});
