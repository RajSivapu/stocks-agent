import {
  parseRecordReportPayload,
  parseReportDecisions,
  type RecordReportPayload,
  renderReportDelivery,
  reportIdFromKey,
  type ReportPolicyDecision,
} from "./reports.ts";
import { canonicalJson, sha256Hex } from "./intelligence.ts";

function assert(value: boolean, message: string): void {
  if (!value) throw new Error(message);
}
function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
  }
}
function assertThrows(callback: () => unknown): void {
  try {
    callback();
  } catch {
    return;
  }
  throw new Error("expected rejection");
}
const OPTIONS = {
  dashboardBaseUrl: "https://stocks.example.test/app?owner=secret#data",
  allowedDashboardOrigins: ["https://stocks.example.test"],
};
const PACKET_HASH = "b".repeat(64);
function resign(value: RecordReportPayload): RecordReportPayload {
  value.report_hash = sha256Hex(canonicalJson(value.report));
  value.idempotency_key = sha256Hex(
    `v2:${value.kind}:${value.market_date}:${PACKET_HASH}:${value.report_hash}`,
  );
  value.id = reportIdFromKey(value.idempotency_key);
  value.rendered_hash = sha256Hex(value.rendered_text);
  return value;
}
function report(
  kind: RecordReportPayload["kind"] = "weekly",
): RecordReportPayload {
  return resign({
    id: "",
    idempotency_key: "",
    packet_id: "00000000-0000-4000-8000-000000000020",
    market_date: "2026-09-04",
    kind,
    report_hash: "",
    rendered_hash: "",
    rendered_text: "Caller prose",
    report: {
      title: "Caller research",
      summary: "Review evidence",
      full_markdown: "Caller detailed evidence.",
      source_ids: ["00000000-0000-4000-8000-000000000001"],
      policy_decision_ids: ["00000000-0000-4000-8000-000000000003"],
      comparison_ids: [],
      actionable_risk: false,
      material_thesis_change: false,
      intraday_triggered: false,
      suggestion_only: true,
    },
  });
}
function decision(
  overrides: Partial<ReportPolicyDecision> = {},
): ReportPolicyDecision {
  return {
    evaluation_id: "00000000-0000-4000-8000-000000000003",
    candidate_id: "00000000-0000-4000-8000-000000000010",
    run_id: "00000000-0000-4000-8000-000000000011",
    packet_id: "00000000-0000-4000-8000-000000000020",
    packet_hash: PACKET_HASH,
    ticker: "CENX",
    status: "approved",
    final_action: "buy",
    approved_terms: {
      quantity: "10",
      entry_low: "45",
      entry_high: "47.02",
      stop: "42",
      target: "58",
      urgency: "routine",
    },
    ...overrides,
  };
}
Deno.test("report payload verifies canonical hashes and derived UUID", () => {
  assertEquals(parseRecordReportPayload(report()).market_date, "2026-09-04");
  assertThrows(() =>
    parseRecordReportPayload({
      ...report(),
      idempotency_key: crypto.randomUUID(),
    })
  );
  assertThrows(() =>
    parseRecordReportPayload({ ...report(), report_hash: "0".repeat(64) })
  );
  assertThrows(() =>
    parseRecordReportPayload({ ...report(), id: crypto.randomUUID() })
  );
});
Deno.test("weekly monthly theme links address generated canonical report and omit caller URL data", () => {
  for (const kind of ["weekly", "monthly", "theme"] as const) {
    const rendered = renderReportDelivery(report(kind), [decision()], OPTIONS);
    assertEquals(rendered.status, "ready");
    assert(
      rendered.body.length <= 1200 &&
        rendered.body.includes(`/reports/${rendered.payload!.id}`),
      "bounded canonical report link absent",
    );
    assert(
      !rendered.body.includes("owner=secret") &&
        !rendered.body.includes("#data"),
      "URL data leaked",
    );
    assert(rendered.body.includes("Suggestion only"), "authority label absent");
    assertEquals(
      parseRecordReportPayload(rendered.payload).id,
      rendered.payload!.id,
    );
  }
});
Deno.test("report links require HTTPS and allowlisted origin", () => {
  for (
    const base of [
      "http://stocks.example.test",
      "https://evil.example",
      "https://user:pass@stocks.example.test",
    ]
  ) {
    assertThrows(() =>
      renderReportDelivery(report(), [decision()], {
        ...OPTIONS,
        dashboardBaseUrl: base,
      })
    );
  }
});
Deno.test("urgent and intraday authority derives from final terms instead of caller flags", () => {
  const urgent = report("urgent");
  urgent.report.actionable_risk = true;
  assertEquals(
    renderReportDelivery(resign(urgent), [decision()], OPTIONS).reason,
    "not_actionable",
  );
  assertEquals(
    renderReportDelivery(urgent, [
      decision({
        approved_terms: { ...decision().approved_terms!, urgency: "urgent" },
      }),
    ], OPTIONS).status,
    "ready",
  );
  const intraday = report("intraday");
  intraday.report.intraday_triggered = true;
  assertEquals(
    renderReportDelivery(resign(intraday), [
      decision({
        final_action: "watch",
        status: "downgraded",
        approved_terms: null,
      }),
    ], OPTIONS).reason,
    "no_trigger",
  );
});
Deno.test("raw prose cannot smuggle quantity or urgency through an approved buy word", () => {
  const value = report("morning");
  value.report.summary =
    "BUY CENX 999999 shares immediately; stop 1 target 9999";
  value.report.full_markdown = value.report.summary;
  const delivery = renderReportDelivery(resign(value), [decision()], OPTIONS);
  assertEquals(delivery.status, "ready");
  assert(
    !JSON.stringify(delivery).includes("999999") &&
      !delivery.body.includes("immediately"),
    "caller prose escaped authority",
  );
  assert(
    delivery.body.includes("10 shares") && delivery.body.includes("47.02") &&
      delivery.body.includes("stop 42"),
    "approved terms absent",
  );
});
Deno.test("watch decisions emit no raw proposal fields or buy prose", () => {
  const value = report("morning");
  value.report.summary = "BUY CENX immediately";
  const delivery = renderReportDelivery(resign(value), [
    decision({ final_action: "watch", status: "downgraded" }),
  ], OPTIONS);
  assertEquals(delivery.status, "ready");
  assert(
    delivery.body.includes("WATCH") && !delivery.body.includes("BUY") &&
      !delivery.body.includes("47.02"),
    "watch leaked proposal",
  );
  assertEquals(delivery.actionable_fields, undefined);
});
Deno.test("missing policy decisions suppress even non-keyword action prose", () => {
  const value = report("morning");
  value.report.summary = "Acquire a million shares before the close";
  assertEquals(
    renderReportDelivery(resign(value), [], OPTIONS).reason,
    "REPORT_POLICY_MISMATCH",
  );
});
Deno.test("reports reject wrong IDs, duplicate decisions, wrong packets and arbitrary semantic keys", () => {
  const value = report();
  for (
    const rows of [[decision({ packet_id: crypto.randomUUID() })], [
      decision({ evaluation_id: crypto.randomUUID() }),
    ], [decision(), decision()]]
  ) {
    assertEquals(
      renderReportDelivery(value, rows, OPTIONS).reason,
      "REPORT_POLICY_MISMATCH",
    );
  }
  assertThrows(() =>
    parseReportDecisions(
      [decision()],
      crypto.randomUUID(),
      value.packet_id,
      value.report.policy_decision_ids,
    )
  );
  value.idempotency_key = "a".repeat(64);
  value.id = reportIdFromKey(value.idempotency_key);
  assertEquals(
    renderReportDelivery(value, [decision()], OPTIONS).reason,
    "REPORT_POLICY_MISMATCH",
  );
});
