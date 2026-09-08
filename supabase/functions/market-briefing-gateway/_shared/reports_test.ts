import {
  parseRecordReportPayload,
  parseReportDecisions,
  type RecordReportPayload,
  renderReportDelivery,
  reportIdFromKey,
  type ReportPolicyDecision,
} from "./reports.ts";
import { canonicalJson, sha256Hex } from "./intelligence.ts";
import type { EvidencePacketV2 } from "./contracts.ts";

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
    final_alert_urgency: null,
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

Deno.test("receipt-backed empty V2 packet produces a canonical suppressed report", () => {
  const packet = {
    action_candidates: [],
    contract_version: 2,
    coverage: { complete_market_coverage: false, mode: "bounded" },
    evidence: [],
    execution_allowed: false,
    limitations: [],
    observed_at: "2026-09-02T17:00:00.000Z",
    omissions: [],
    policy_version: 1,
    research_candidates: [],
    run_id: "00000000-0000-4000-8000-000000000011",
  } as EvidencePacketV2;
  const value = report("weekly");
  value.report.policy_decision_ids = [];
  value.report.source_ids = [];
  value.report_hash = sha256Hex(canonicalJson(value.report));
  const packetHash = sha256Hex(canonicalJson(packet));
  value.idempotency_key = sha256Hex(
    `v2:${value.kind}:${value.market_date}:${packetHash}:${value.report_hash}`,
  );
  value.id = reportIdFromKey(value.idempotency_key);

  const delivery = renderReportDelivery(value, [], OPTIONS, packet);

  assertEquals(delivery.status, "suppressed");
  assertEquals(delivery.reason, "not_actionable");
  assertEquals(delivery.payload?.report.source_ids, []);
  assertEquals(delivery.payload?.report.policy_decision_ids, []);
  assert(
    delivery.payload?.report.full_markdown.includes("0 research candidate(s)") ===
      false,
    "empty report should retain coverage without inventing a candidate",
  );
  assert(
    delivery.payload?.report.full_markdown.includes("Coverage:") === true,
    "empty report omitted bounded coverage",
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
  urgent.report.summary = "SELL EVERYTHING NOW";
  urgent.report.full_markdown = "Caller says SELL 999999 shares";
  urgent.report.actionable_risk = true;
  const notActionable = renderReportDelivery(
    resign(urgent),
    [decision()],
    OPTIONS,
  );
  assertEquals(notActionable.reason, "not_actionable");
  assert(
    notActionable.payload !== undefined &&
      !JSON.stringify(notActionable.payload).includes("999999") &&
      !JSON.stringify(notActionable.payload).includes("SELL EVERYTHING"),
    "suppressed urgent report retained caller advice",
  );
  assertEquals(
    parseRecordReportPayload(notActionable.payload).report_hash,
    notActionable.payload!.report_hash,
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
  intraday.report.summary = "BUY 999999 shares immediately";
  intraday.report.full_markdown = intraday.report.summary;
  intraday.report.intraday_triggered = true;
  const noTrigger = renderReportDelivery(resign(intraday), [
    decision({
      final_action: "watch",
      status: "downgraded",
      final_alert_urgency: null,
      approved_terms: null,
    }),
  ], OPTIONS);
  assertEquals(noTrigger.reason, "no_trigger");
  assert(
    noTrigger.payload !== undefined &&
      !JSON.stringify(noTrigger.payload).includes("999999") &&
      JSON.stringify(noTrigger.payload).includes("WATCH"),
    "no-trigger report was not canonical policy prose",
  );
});
Deno.test("an urgent actionable final decision controls the canonical report kind and heading", () => {
  const delivery = renderReportDelivery(report("morning"), [
    decision({
      approved_terms: { ...decision().approved_terms!, urgency: "urgent" },
    }),
  ], OPTIONS);
  assertEquals(delivery.status, "ready");
  assertEquals(delivery.payload!.kind, "urgent");
  assert(
    delivery.body.startsWith("<b>URGENT RESEARCH REVIEW"),
    "urgent actionable heading was not canonical",
  );
});
Deno.test("urgent actionable final decisions outrank routine pure-HOLD alerts", () => {
  const actionable = decision({
    evaluation_id: "00000000-0000-4000-8000-000000000004",
    approved_terms: { ...decision().approved_terms!, urgency: "urgent" },
  });
  const routineHold = decision({
    evaluation_id: "00000000-0000-4000-8000-000000000005",
    final_action: "hold",
    final_alert_urgency: "routine",
    approved_terms: null,
  });
  const value = report("intraday");
  value.report.policy_decision_ids = [
    actionable.evaluation_id,
    routineHold.evaluation_id,
  ];
  const delivery = renderReportDelivery(resign(value), [
    actionable,
    routineHold,
  ], OPTIONS);
  assertEquals(delivery.status, "ready");
  assertEquals(delivery.payload!.kind, "urgent");
  assert(
    delivery.body.startsWith("<b>URGENT RESEARCH REVIEW"),
    "routine HOLD relabelled the urgent actionable decision as intraday",
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
Deno.test("ordinary research is suppressed for every report kind with canonical receipts", () => {
  for (
    const kind of [
      "morning",
      "weekly",
      "monthly",
      "theme",
      "intraday",
      "urgent",
      "on-demand",
    ] as const
  ) {
    const value = report(kind);
    value.report.summary = "BUY CENX immediately";
    const delivery = renderReportDelivery(resign(value), [
      decision({
        final_action: "watch",
        status: "downgraded",
        final_alert_urgency: null,
        approved_terms: null,
      }),
    ], OPTIONS);
    assertEquals(delivery.status, "suppressed");
    assertEquals(
      delivery.reason,
      kind === "intraday" ? "no_trigger" : "not_actionable",
    );
    assertEquals(delivery.body, "");
    assertEquals(delivery.parts, []);
    assertEquals(delivery.actionable_fields, undefined);
    assert(
      delivery.payload !== undefined &&
        !JSON.stringify(delivery.payload).includes("BUY CENX immediately") &&
        JSON.stringify(delivery.payload).includes("WATCH"),
      "suppression receipt did not retain bounded canonical research state",
    );
    parseRecordReportPayload(delivery.payload);
  }
});

Deno.test("mixed reports keep research in audit but Telegram contains only approved actions and holding-risk alerts", () => {
  for (const kind of ["morning", "weekly", "monthly", "theme", "on-demand", "intraday", "urgent"] as const) {
    const buy = decision({ evaluation_id: "00000000-0000-4000-8000-000000000003" });
    const watch = decision({
      evaluation_id: "00000000-0000-4000-8000-000000000004",
      ticker: "WAIT", status: "downgraded", final_action: "watch",
      approved_terms: null, final_alert_urgency: null,
    });
    const insufficient = decision({
      evaluation_id: "00000000-0000-4000-8000-000000000005",
      ticker: "MISS", status: "vetoed", final_action: null,
      approved_terms: null, final_alert_urgency: null,
    });
    const risk = decision({
      evaluation_id: "00000000-0000-4000-8000-000000000006",
      ticker: "HELD", status: "approved", final_action: "hold",
      approved_terms: null, final_alert_urgency: "routine",
    });
    const value = report(kind);
    value.report.policy_decision_ids = [buy, watch, insufficient, risk]
      .map((row) => row.evaluation_id).sort();
    const delivery = renderReportDelivery(resign(value), [buy, watch, insufficient, risk], OPTIONS);
    assertEquals(delivery.status, "ready");
    assert(delivery.body.includes("CENX") && delivery.body.includes("HELD"), "approved rows absent");
    assert(!delivery.body.includes("WAIT") && !delivery.body.includes("MISS"), "Telegram leaked non-action research");
    assert(delivery.payload!.report.full_markdown.includes("WAIT: WATCH") &&
      delivery.payload!.report.full_markdown.includes("MISS: INSUFFICIENT"), "audit report lost decisions");
    assertEquals(delivery.payload!.report.source_ids, value.report.source_ids);
    assertEquals(delivery.payload!.report.policy_decision_ids, value.report.policy_decision_ids);
  }
});
Deno.test("missing policy decisions suppress even non-keyword action prose", () => {
  const value = report("morning");
  value.report.summary = "Acquire a million shares before the close";
  assertEquals(
    renderReportDelivery(resign(value), [], OPTIONS).reason,
    "REPORT_POLICY_MISMATCH",
  );
});
Deno.test("v2 research-only report needs no fabricated ticker or policy evaluation", () => {
  const suitabilityBody = {
    component_scores: {
      concentration_penalty: "0.000000",
      duplication_penalty: "0.000000",
      liquidity: "0.000000",
      portfolio_relevance: "0.000000",
    },
    lineage: null,
    missing_reasons: ["security_identity_unresolved", "valuation_missing"],
    state: "unknown" as const,
    veto_reasons: [],
  };
  const candidateBody = {
    adverse_paths: [],
    candidate_key: "unresolved:magnet-supplier",
    entity_id: "unresolved:magnet-supplier",
    event_ids: ["00000000-0000-4000-8000-000000000041"],
    evidence: [{
      claim_type: "event",
      item_id: "00000000-0000-4000-8000-000000000031",
      relationship_eligible: false,
      role: "supporting" as const,
    }],
    exposure_fact_ids: [],
    limitations: ["security_identity_unresolved"],
    priority_components: {
      authority_corroboration: "1.000000",
      exposure: "0.000000",
      materiality: "0.000000",
      recency: "1.000000",
    },
    priority_score: "2.000000",
    research_state: "unresolved" as const,
    roles: [],
    security_id: null,
    suitability: {
      ...suitabilityBody,
      evaluation_hash: sha256Hex(canonicalJson(suitabilityBody)),
    },
    theme_ids: ["critical-minerals"],
    ticker: null,
  };
  const packet: EvidencePacketV2 = {
    action_candidates: [],
    contract_version: 2,
    coverage: { complete_market_coverage: false, mode: "bounded" },
    evidence: [{
      authority: "official",
      canonical_url: "https://example.test/item",
      claim_type: "event",
      content_hash: "a".repeat(64),
      effective_at: null,
      item_id: "00000000-0000-4000-8000-000000000031",
      normalized_text: "Official event.",
      published_at: "2026-09-02T16:00:00.000Z",
      reporting_at: null,
      retrieved_at: "2026-09-02T16:01:00.000Z",
      source_identity: {
        provider: "official",
        receipt_id: "00000000-0000-4000-8000-000000000042",
        upstream_item_id: "item-1",
      },
    }],
    execution_allowed: false,
    limitations: [],
    observed_at: "2026-09-02T17:00:00.000Z",
    omissions: [],
    policy_version: 1,
    research_candidates: [{
      ...candidateBody,
      candidate_hash: sha256Hex(canonicalJson(candidateBody)),
    }],
    run_id: "00000000-0000-4000-8000-000000000011",
  };
  const value = report("weekly");
  value.report.policy_decision_ids = [];
  value.report.source_ids = ["00000000-0000-4000-8000-000000000031"];
  value.report_hash = sha256Hex(canonicalJson(value.report));
  const packetHash = sha256Hex(canonicalJson(packet));
  value.idempotency_key = sha256Hex(
    `v2:${value.kind}:${value.market_date}:${packetHash}:${value.report_hash}`,
  );
  value.id = reportIdFromKey(value.idempotency_key);
  const delivery = renderReportDelivery(value, [], OPTIONS, packet);

  assertEquals(delivery.status, "suppressed");
  assertEquals(delivery.reason, "not_actionable");
  assertEquals(delivery.body, "");
  assertEquals(delivery.parts, []);
  assert(
    delivery.payload?.report.full_markdown.includes(
      "unresolved:magnet-supplier — RESEARCH ONLY",
    ) === true,
    "canonical research state was not retained",
  );
  const forged = structuredClone(packet);
  forged.action_candidates = [{
    candidate_key: candidateBody.candidate_key,
    candidate_hash: forged.research_candidates[0].candidate_hash,
    suitability_hash: forged.research_candidates[0].suitability.evaluation_hash,
  }];
  assertEquals(
    renderReportDelivery(value, [], OPTIONS, forged).reason,
    "REPORT_POLICY_MISMATCH",
  );

  const mixed = structuredClone(packet);
  const unresolved = structuredClone(mixed.research_candidates[0]);
  unresolved.candidate_key = "unresolved:second-supplier";
  unresolved.entity_id = "unresolved:second-supplier";
  const unresolvedBody = { ...unresolved } as Record<string, unknown>;
  delete unresolvedBody.candidate_hash;
  unresolved.candidate_hash = sha256Hex(canonicalJson(unresolvedBody));
  const actionResearch = mixed.research_candidates[0];
  actionResearch.candidate_key = "sec:CENX";
  actionResearch.security_id = "sec:CENX";
  actionResearch.ticker = "CENX";
  actionResearch.entity_id = "issuer:CENX";
  actionResearch.research_state = "analysis_ready";
  actionResearch.exposure_fact_ids = [
    "00000000-0000-4000-8000-000000000043",
  ];
  actionResearch.suitability.state = "eligible";
  actionResearch.suitability.missing_reasons = [];
  const actionSuitability = { ...actionResearch.suitability } as Record<
    string,
    unknown
  >;
  delete actionSuitability.evaluation_hash;
  actionResearch.suitability.evaluation_hash = sha256Hex(
    canonicalJson(actionSuitability),
  );
  const actionBody = { ...actionResearch } as Record<string, unknown>;
  delete actionBody.candidate_hash;
  actionResearch.candidate_hash = sha256Hex(canonicalJson(actionBody));
  mixed.research_candidates.push(unresolved);
  mixed.action_candidates = [{
    candidate_key: actionResearch.candidate_key,
    candidate_hash: actionResearch.candidate_hash,
    suitability_hash: actionResearch.suitability.evaluation_hash,
  }];
  const actionReport = report("weekly");
  actionReport.report.source_ids = [
    "00000000-0000-4000-8000-000000000031",
  ];
  actionReport.report_hash = sha256Hex(canonicalJson(actionReport.report));
  const mixedHash = sha256Hex(canonicalJson(mixed));
  actionReport.idempotency_key = sha256Hex(
    `v2:${actionReport.kind}:${actionReport.market_date}:${mixedHash}:${actionReport.report_hash}`,
  );
  actionReport.id = reportIdFromKey(actionReport.idempotency_key);
  const mixedDelivery = renderReportDelivery(
    actionReport,
    [decision({ packet_hash: mixedHash })],
    OPTIONS,
    mixed,
  );
  assert(
    mixedDelivery.payload?.report.full_markdown.includes(
          "CENX — ACTION LANE",
        ) === true &&
      mixedDelivery.payload.report.full_markdown.includes(
        "unresolved:second-supplier — RESEARCH ONLY",
      ),
    `action report dropped the packet research catalog: ${
      JSON.stringify(mixedDelivery)
    }`,
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
