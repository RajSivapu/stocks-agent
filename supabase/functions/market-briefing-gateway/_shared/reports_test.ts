import {
  parseRecordReportPayload,
  type RecordReportPayload,
  renderReportDelivery,
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
function assertThrows(callback: () => unknown, message: string): void {
  try {
    callback();
  } catch (error) {
    if (error instanceof Error && error.message.includes(message)) return;
    throw error;
  }
  throw new Error(`expected error containing ${message}`);
}

function report(
  kind: RecordReportPayload["kind"] = "weekly",
): RecordReportPayload {
  const body = {
    title: "Weekly owner research",
    summary: "Evidence changed; review the cited research.",
    full_markdown: "# Weekly owner research\n\n" +
      "Detailed evidence. ".repeat(80),
    source_ids: [
      "00000000-0000-4000-8000-000000000001",
      "00000000-0000-4000-8000-000000000002",
    ],
    policy_decision_ids: ["00000000-0000-4000-8000-000000000003"],
    comparison_ids: [],
    actionable_risk: false,
    material_thesis_change: false,
    intraday_triggered: true,
    suggestion_only: true,
  };
  return {
    id: "00000000-0000-4000-8000-000000000040",
    idempotency_key: "a".repeat(64),
    packet_id: "00000000-0000-4000-8000-000000000020",
    market_date: "2026-09-04",
    kind,
    report: body,
    report_hash: sha256Hex(canonicalJson(body)),
    rendered_text: body.full_markdown,
    rendered_hash: sha256Hex(body.full_markdown),
  };
}

Deno.test("report payload accepts only canonical hashes and a SHA-256 idempotency key", () => {
  assertEquals(
    parseRecordReportPayload(report()).idempotency_key,
    "a".repeat(64),
  );
  assertThrows(
    () =>
      parseRecordReportPayload({
        ...report(),
        idempotency_key: crypto.randomUUID(),
      }),
    "SHA-256",
  );
  assertThrows(
    () =>
      parseRecordReportPayload({ ...report(), report_hash: "0".repeat(64) }),
    "report_hash",
  );
});

Deno.test("weekly monthly and theme delivery is bounded summary plus authenticated report link", () => {
  for (const kind of ["weekly", "monthly", "theme"] as const) {
    const value = report(kind);
    const rendered = renderReportDelivery(value, {
      dashboardBaseUrl: "https://stocks.example.test/app",
      allowedDashboardOrigins: ["https://stocks.example.test"],
    });
    assert(
      rendered.body.length <= 1200,
      `${kind} body exceeded Telegram bound`,
    );
    assert(
      rendered.body.includes(`/reports/${value.id}`),
      `${kind} link absent`,
    );
    assert(
      !rendered.body.includes(value.report.full_markdown),
      `${kind} duplicated full report`,
    );
    assert(
      rendered.body.includes("Suggestion only"),
      `${kind} authority label absent`,
    );
  }
});

Deno.test("report link rejects unsafe or non-allowlisted origins and contains only report UUID", () => {
  assertThrows(
    () =>
      renderReportDelivery(report(), {
        dashboardBaseUrl: "http://stocks.example.test",
        allowedDashboardOrigins: ["http://stocks.example.test"],
      }),
    "HTTPS",
  );
  assertThrows(
    () =>
      renderReportDelivery(report(), {
        dashboardBaseUrl: "https://evil.example/report?owner=secret",
        allowedDashboardOrigins: ["https://stocks.example.test"],
      }),
    "allowlisted",
  );
  const rendered = renderReportDelivery(report(), {
    dashboardBaseUrl: "https://stocks.example.test/app?owner=secret#data",
    allowedDashboardOrigins: ["https://stocks.example.test"],
  });
  assert(!rendered.body.includes("owner=secret"), "URL leaked query content");
  assert(!rendered.body.includes("#data"), "URL leaked fragment content");
});

Deno.test("morning is concise, urgent requires material action, and no-trigger intraday is silent", () => {
  const options = {
    dashboardBaseUrl: "https://stocks.example.test",
    allowedDashboardOrigins: ["https://stocks.example.test"],
  };
  const morning = renderReportDelivery(report("morning"), options);
  assert(
    morning.status === "ready" && morning.body.length <= 1200,
    "morning was not concise",
  );
  assert(
    !morning.body.includes("/reports/"),
    "morning unexpectedly linked full report",
  );
  const urgent = report("urgent");
  assertEquals(renderReportDelivery(urgent, options), {
    status: "suppressed",
    body: "",
    parts: [],
    reason: "not_actionable",
  });
  urgent.report.actionable_risk = true;
  urgent.report_hash = sha256Hex(canonicalJson(urgent.report));
  assert(
    renderReportDelivery(urgent, options).status === "ready",
    "actionable urgent report suppressed",
  );
  const intraday = report("intraday");
  intraday.report.intraday_triggered = false;
  intraday.report_hash = sha256Hex(canonicalJson(intraday.report));
  assertEquals(renderReportDelivery(intraday, options), {
    status: "suppressed",
    body: "",
    parts: [],
    reason: "no_trigger",
  });
});
