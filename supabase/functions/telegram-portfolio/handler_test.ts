import {
  assert,
  assertEquals,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import {
  createTelegramPortfolioHandler,
  type TelegramDelivery,
  type TelegramPortfolioRepository,
} from "./handler.ts";


const SECRET = "a-telegram-webhook-secret-at-least-32";

class FakeRepository implements TelegramPortfolioRepository {
  calls: Array<{ name: string; value: Record<string, unknown> }> = [];
  claimed = true;
  linked = true;
  callbackResult: Record<string, unknown> = {
    claimed: true,
    status: "applied",
    action: "confirm",
    result: { operation: "buy", ticker: "AAPL", holding: { shares: 2, avg_cost: 210 } },
  };

  private result(name: string, value: Record<string, unknown>, result: Record<string, unknown>) {
    this.calls.push({ name, value: structuredClone(value) });
    return Promise.resolve(structuredClone(result));
  }

  resolveLink(value: Record<string, unknown>) {
    return this.result("resolveLink", value, { linked: this.linked });
  }
  claimUpdate(value: Record<string, unknown>) {
    return this.result("claimUpdate", value, { claimed: this.claimed });
  }
  consumePairing(value: Record<string, unknown>) {
    return this.result("consumePairing", value, { status: "linked" });
  }
  prepareCommand(value: Record<string, unknown>) {
    return this.result("prepareCommand", value, {
      claimed: this.claimed,
      command_id: "11111111-1111-4111-8111-111111111111",
      status: "previewed",
      preview_digest: "a".repeat(64),
      expires_at: "2026-09-02T18:15:00Z",
      operation: "buy",
      before: { shares: 0 },
      after: { shares: 2 },
      warnings: [],
    });
  }
  applyCallback(value: Record<string, unknown>) {
    return this.result("applyCallback", value, this.callbackResult);
  }
  unlink(value: Record<string, unknown>) {
    return this.result("unlink", value, { claimed: this.claimed, status: "unlinked" });
  }
  portfolio(value: Record<string, unknown>) {
    return this.result("portfolio", value, { claimed: this.claimed, holdings: [] });
  }
  plans(value: Record<string, unknown>) {
    return this.result("plans", value, { claimed: this.claimed, plans: [] });
  }
  recordDelivery(value: Record<string, unknown>) {
    return this.result("recordDelivery", value, { recorded: true });
  }
  recordPairingDelivery(value: Record<string, unknown>) {
    return this.result("recordPairingDelivery", value, { recorded: true });
  }
}

function update(text = "/buy AAPL 2 210 growth") {
  return {
    update_id: 501,
    message: {
      message_id: 50,
      date: 1_788_368_400,
      text,
      chat: { id: 123, type: "private" },
      from: { id: 123 },
    },
  };
}

function makeHandler(delivery: TelegramDelivery = { status: "delivered", messageId: 77 }) {
  const repository = new FakeRepository();
  const telegramCalls: Array<Record<string, unknown>> = [];
  const handler = createTelegramPortfolioHandler({
    webhookSecret: SECRET,
    pairingHashPepper: "a-pairing-test-pepper-with-at-least-32-bytes",
    repository,
    sendTelegram: (value) => {
      telegramCalls.push(structuredClone(value));
      return Promise.resolve(delivery);
    },
    newId: () => "22222222-2222-4222-8222-222222222222",
    newCallbackToken: (() => {
      let count = 0;
      return () => count++ === 0
        ? "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        : "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
    })(),
  });
  return { handler, repository, telegramCalls };
}

function request(value: unknown, options: { secret?: string; method?: string; body?: string } = {}) {
  return new Request("https://edge.example.test/telegram-portfolio", {
    method: options.method ?? "POST",
    headers: {
      "content-type": "application/json",
      "x-telegram-bot-api-secret-token": options.secret ?? SECRET,
    },
    body: options.method === "GET" ? undefined : options.body ?? JSON.stringify(value),
  });
}

Deno.test("webhook secret and method are checked before body or repository work", async () => {
  const { handler, repository } = makeHandler();
  const method = await handler(request({}, { method: "GET" }));
  const secret = await handler(request({}, { secret: "wrong", body: "{broken" }));
  assertEquals(method.status, 405);
  assertEquals(secret.status, 401);
  assertEquals(repository.calls, []);
});

Deno.test("private trade update is atomically claimed, previewed, and assigned callbacks", async () => {
  const { handler, repository, telegramCalls } = makeHandler();
  const response = await handler(request(update()));
  assertEquals(response.status, 200);
  assertEquals(await response.json(), { ok: true });
  assertEquals(repository.calls.map((call) => call.name), [
    "resolveLink", "prepareCommand", "recordDelivery",
  ]);
  const preview = repository.calls.find((call) => call.name === "prepareCommand")?.value;
  assertEquals(preview?.command, {
    operation: "buy",
    ticker: "AAPL",
    quantity: "2",
    fill_price: "210",
    fees: "0",
    cash_total: null,
    executed_on: "2026-09-02",
    bucket: "growth",
  });
  assertEquals(Object.hasOwn(preview ?? {}, "owner_id"), false);
  assertEquals(telegramCalls.length, 1);
  assert((telegramCalls[0].text as string).includes("BUY 2 AAPL @ $210 on 2026-09-02"));
  assert((telegramCalls[0].text as string).includes("does not place"));
});

Deno.test("duplicates and non-private updates do no portfolio work", async () => {
  const duplicate = makeHandler();
  duplicate.repository.claimed = false;
  await duplicate.handler(request(update()));
  assertEquals(duplicate.repository.calls.map((call) => call.name), ["resolveLink", "prepareCommand"]);
  assertEquals(duplicate.telegramCalls, []);

  const group = makeHandler();
  const groupUpdate = update();
  groupUpdate.message.chat.type = "group";
  const response = await group.handler(request(groupUpdate));
  assertEquals(response.status, 200);
  assertEquals(group.repository.calls, []);
  assertEquals(group.telegramCalls, []);
});

Deno.test("pairing is private, HMAC-bound, and does not require an existing link", async () => {
  const { handler, repository } = makeHandler();
  repository.linked = false;
  await handler(request(update("/start ABCD234567")));
  assertEquals(repository.calls.map((call) => call.name), ["consumePairing", "recordPairingDelivery"]);
  const pairing = repository.calls[0].value;
  assertEquals(pairing.chat_id, 123);
  assertEquals(pairing.user_id, 123);
  assertEquals((pairing.code_digest as string).length, 64);
  assertEquals(Object.hasOwn(pairing, "code"), false);
});

Deno.test("opaque callback token is applied atomically and no bot token enters SQL", async () => {
  const { handler, repository } = makeHandler();
  const token = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC";
  const callbackUpdate = {
    update_id: 502,
    callback_query: {
      id: "callback-id",
      data: `pc:c:${token}`,
      from: { id: 123 },
      message: {
        message_id: 77,
        date: 1_788_368_400,
        chat: { id: 123, type: "private" },
      },
    },
  };
  await handler(request(callbackUpdate));
  assertEquals(repository.calls.map((call) => call.name), [
    "resolveLink", "applyCallback", "recordDelivery",
  ]);
  const serialized = JSON.stringify(repository.calls);
  assertEquals(serialized.includes("bot-token"), false);
  assertEquals(serialized.includes(token), false);
});

Deno.test("delivery unknown is recorded and never blindly retried", async () => {
  const { handler, repository, telegramCalls } = makeHandler({ status: "delivery_unknown" });
  const response = await handler(request(update("/status")));
  assertEquals(response.status, 200);
  assertEquals(telegramCalls.length, 1);
  assertEquals(repository.calls.at(-1)?.name, "recordDelivery");
  assertEquals(repository.calls.at(-1)?.value.status, "delivery_unknown");
});

Deno.test("callback copy reports unavailable and rejected outcomes without false success", async () => {
  for (const callbackResult of [
    { claimed: true, status: "unavailable", action: "confirm", reason: "callback_unavailable" },
    { claimed: true, status: "rejected", action: "cancel", reason: "command_no_longer_applicable" },
  ]) {
    const context = makeHandler();
    context.repository.callbackResult = callbackResult;
    const token = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC";
    await context.handler(request({
      update_id: 503,
      callback_query: {
        id: "callback-id",
        data: `pc:${callbackResult.action === "cancel" ? "x" : "c"}:${token}`,
        from: { id: 123 },
        message: { message_id: 77, date: 1_788_368_400, chat: { id: 123, type: "private" } },
      },
    }));
    const text = String(context.telegramCalls[0].text);
    assert(text.includes("Nothing was changed"));
    assertEquals(/\b(?:Recorded|Cancelled|processed)\b/.test(text), false);
  }
});

Deno.test("oversized database prose is bounded and retains the record-only warning", async () => {
  const { handler, repository, telegramCalls } = makeHandler();
  repository.prepareCommand = (value) => {
    repository.calls.push({ name: "prepareCommand", value: structuredClone(value) });
    return Promise.resolve({
      claimed: true,
      command_id: "11111111-1111-4111-8111-111111111111",
      status: "previewed",
      preview_digest: "a".repeat(64),
      expires_at: "2026-09-02T18:15:00Z",
      operation: "buy",
      before: { shares: 0 },
      after: { shares: 2 },
      warnings: ["x".repeat(5_000)],
    });
  };
  await handler(request(update()));
  const text = telegramCalls[0].text as string;
  assertEquals([...text].length, 4_096);
  assert(text.endsWith("This bot does not place or modify brokerage orders.]"));
});
