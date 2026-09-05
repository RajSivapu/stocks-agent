import type { PolicyConfig } from "./contracts.ts";
import { createSupabaseGatewayRepository, GatewayRepositoryError, validatePolicy } from "./repository.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}

function assertEquals<T>(actual: T, expected: T): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function policy(): PolicyConfig {
  return {
    version: 2,
    allocation_bps: { core: 7000, growth: 2000, speculative: 1000 },
    max_position_bps_of_bucket: { core: 2500, growth: 2000, speculative: 1000 },
    max_trade_risk_bps: { core: 100, growth: 100, speculative: 50 },
    min_reward_risk_milli: 2000,
    max_actionable_quote_age_minutes: 20,
    alert_near_bps: 400,
    daily_loss_limit_bps: 300,
    circuit_breaker_consecutive_losses: 3,
    speculative_go_live_bucket_micros: "500000000",
    monthly_investment_micros: "500000000",
    broad_core_etfs: ["SCHD", "VOO", "VTI", "VXUS"],
    self_tuning_enabled: false,
    market_calendar_year: 2026,
    nyse_holidays: [],
    request_limits: {
      max_body_bytes: 262144,
      max_candidates: { "pre-market": 80, intraday: 20, "post-market": 80, "on-demand": 10 },
      max_requests_per_run: 20,
      max_authenticated_requests_per_hour: 100,
    },
    alerts_v3: {
      enabled: false,
      shadow: true,
      enabled_classes: [],
      profile: "balanced",
      draft_ttl_hours: 24,
      drafts_per_hour: 5,
    },
  };
}

function rejects(value: unknown): boolean {
  try {
    validatePolicy(value);
  } catch (error) {
    return error instanceof GatewayRepositoryError && error.code === "POLICY_REJECTED";
  }
  return false;
}

Deno.test("repository accepts only the reviewed alert v3 policy shape", () => {
  const legacy = structuredClone(policy()) as unknown as Record<string, unknown>;
  legacy.version = 2;
  delete (legacy.alerts_v3 as Record<string, unknown>).enabled_classes;
  assertEquals(validatePolicy(legacy).alerts_v3?.enabled_classes, []);

  const reviewed = policy();
  reviewed.version = 3;
  reviewed.alerts_v3!.enabled = true;
  reviewed.alerts_v3!.shadow = false;
  reviewed.alerts_v3!.enabled_classes = ["stop_breach"];
  assert(validatePolicy(reviewed).alerts_v3?.enabled === true, "reviewed policy rejected");
  for (const alerts of [
    { ...policy().alerts_v3, enabled: true, shadow: true },
    { ...policy().alerts_v3, enabled: true, shadow: false, enabled_classes: [] },
    { ...policy().alerts_v3, enabled_classes: ["stop_breach", "stop_breach"] },
    { ...policy().alerts_v3, enabled_classes: ["stop_near"] },
    { ...policy().alerts_v3, profile: "aggressive" },
    { ...policy().alerts_v3, drafts_per_hour: 50 },
    { ...policy().alerts_v3, broker_execution: true },
  ]) {
    assert(rejects({ ...policy(), version: 3, alerts_v3: alerts }), "unreviewed alert policy accepted");
  }
});

Deno.test("report delivery outbox is created from the deterministic report receipt before Telegram work", async () => {
  const calls: Array<{ name: string; parameters: Record<string, unknown> | undefined }> = [];
  const repository = createSupabaseGatewayRepository({
    rpc(name: string, parameters?: Record<string, unknown>) {
      calls.push({ name, parameters });
      return Promise.resolve({
        data: {
          report_id: "00000000-0000-4000-8000-000000000001",
          idempotency_key: "a".repeat(64),
          status: "pending",
          telegram_message_ids: [],
          telegram_accepted_at: null,
          lease_token: null,
        },
        error: null,
      });
    },
  });
  const receipt = await repository.createReportPublication!(
    "00000000-0000-4000-8000-000000000002",
    {
      id: "00000000-0000-4000-8000-000000000001",
      idempotency_key: "a".repeat(64),
      packet_id: "00000000-0000-4000-8000-000000000003",
      market_date: "2026-09-02",
      kind: "morning",
      report: {
        title: "x", summary: "x", full_markdown: "x", source_ids: [],
        policy_decision_ids: [], comparison_ids: [], actionable_risk: false,
        material_thesis_change: false, intraday_triggered: false, suggestion_only: true,
      },
      report_hash: "b".repeat(64), rendered_text: "Delivery body", rendered_hash: "c".repeat(64),
    },
  );
  assertEquals(receipt, {
    id: "00000000-0000-4000-8000-000000000001", idempotency_key: "a".repeat(64),
    status: "pending", telegram_message_ids: [], telegram_accepted_at: null, lease_token: null,
  });
  assertEquals(calls, [{
    name: "create_market_report_publication",
    parameters: {
      p_run_id: "00000000-0000-4000-8000-000000000002",
      p_report_id: "00000000-0000-4000-8000-000000000001",
      p_idempotency_key: "a".repeat(64), p_market_date: "2026-09-02", p_kind: "morning",
      p_rendered_body: "Delivery body", p_rendered_hash: "c".repeat(64),
    },
  }]);
});
