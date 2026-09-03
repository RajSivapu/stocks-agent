import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { createRunSchedulerHandler } from "./handler.ts";
import type { ClaimedSlot, SchedulerRepository, TriggerSecret } from "./repository.ts";

const SECRET = "scheduler-secret-with-enough-entropy";
const REQUEST_ID = "11111111-1111-4111-8111-111111111111";
const ATTEMPT_ID = "22222222-2222-4222-8222-222222222222";

class FakeRepository implements SchedulerRepository {
  slots: ClaimedSlot[] = [];
  calls: string[] = [];
  claimDueSlots(_now: string, _limit: number) { this.calls.push("claim"); return Promise.resolve(this.slots); }
  readTriggerSecret(_attemptId: string): Promise<TriggerSecret> {
    this.calls.push("secret");
    return Promise.resolve({ endpoint: "https://api.anthropic.com/v1/claude_code/routines/trig_ABCDEF/fire", token: "t".repeat(32), trigger_request_id: REQUEST_ID });
  }
  recordTriggerResult() { this.calls.push("record"); return Promise.resolve({ status: "recorded" }); }
  publishHoliday() { this.calls.push("holiday"); return Promise.resolve({ status: "ready" }); }
}

function request(body: string, authorization = `Bearer ${SECRET}`) {
  return new Request("https://example.test/run-scheduler", {
    method: "POST", headers: { authorization, "content-type": "application/json" }, body,
  });
}

Deno.test("scheduler authenticates before parsing and returns no secret material", async () => {
  const repository = new FakeRepository();
  const handler = createRunSchedulerHandler({ repository, schedulerSecret: SECRET });
  assertEquals((await handler(request("{broken", "Bearer wrong"))).status, 401);
  assertEquals(repository.calls, []);
  const response = await handler(request('{"limit":10}'));
  assertEquals(response.status, 200);
  assertEquals(JSON.stringify(await response.json()).includes("token"), false);
});

Deno.test("one claimed slot causes one fire and one terminal trigger receipt", async () => {
  const repository = new FakeRepository();
  repository.slots = [{ slot_id: crypto.randomUUID(), trigger_request_id: REQUEST_ID,
    phase: "pre-market", market_date: "2026-09-03", holiday: false, attempt_id: ATTEMPT_ID }];
  let fires = 0;
  const response = await createRunSchedulerHandler({
    repository, schedulerSecret: SECRET,
    fire: async (_url, _token, id) => {
      fires += 1;
      assertEquals(id, REQUEST_ID);
      return { status: "trigger_unknown", responseStatus: null, sessionUrl: null, responseDigest: "a".repeat(64) };
    },
  })(request('{"limit":10}'));
  assertEquals(response.status, 200);
  assertEquals(fires, 1);
  assertEquals(repository.calls, ["claim", "secret", "record"]);
  assertEquals((await response.json()).counts.trigger_unknown, 1);
});

Deno.test("holiday slots publish deterministic copy without reading a provider secret", async () => {
  const repository = new FakeRepository();
  repository.slots = [{ slot_id: crypto.randomUUID(), trigger_request_id: REQUEST_ID,
    phase: "pre-market", market_date: "2026-09-07", holiday: true, attempt_id: null }];
  const response = await createRunSchedulerHandler({
    repository, schedulerSecret: SECRET,
    fire: () => Promise.reject(new Error("provider must not run")),
  })(request('{"limit":10}'));
  assertEquals(response.status, 200);
  assertEquals(repository.calls, ["claim", "holiday"]);
});
