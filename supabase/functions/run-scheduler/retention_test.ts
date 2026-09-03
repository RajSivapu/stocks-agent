import {
  assertEquals,
  assertRejects,
} from "https://deno.land/std@0.224.0/assert/mod.ts";

import { runRetentionCycle } from "./retention.ts";


Deno.test("retention accepts only bounded server count receipts", async () => {
  let calls = 0;
  const receipt = await runRetentionCycle({
    applyRetention() {
      calls += 1;
      return Promise.resolve({
        status: "completed",
        pairing_codes: 2,
        callback_tokens: 3,
        telegram_updates: 4,
        pairing_deliveries: 5,
        commands_compacted: 6,
        evidence_compacted: 7,
        submissions_compacted: 8,
        tombstones_expired: 9,
      });
    },
  }, () => "11111111-1111-4111-8111-111111111111");
  assertEquals(calls, 1);
  assertEquals(receipt.commands_compacted, 6);
});

Deno.test("retention rejects unknown fields, private data, and unbounded counts", async () => {
  for (const invalid of [
    { status: "completed", owner_id: crypto.randomUUID() },
    {
      status: "completed", pairing_codes: 0, callback_tokens: 0,
      telegram_updates: 0, pairing_deliveries: 0, commands_compacted: 0,
      evidence_compacted: 0, submissions_compacted: 0, tombstones_expired: -1,
    },
  ]) {
    await assertRejects(
      () => runRetentionCycle({ applyRetention: () => Promise.resolve(invalid) }),
      Error,
      "retention receipt",
    );
  }
});
