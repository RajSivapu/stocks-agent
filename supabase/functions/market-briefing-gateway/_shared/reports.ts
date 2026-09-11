import { canonicalJson, sha256Hex } from "./intelligence.ts";
import type { ApprovedTerms } from "./policy.ts";
import {
  type EvidencePacket,
  type EvidencePacketV2,
  isEvidencePacketV2,
} from "./contracts.ts";

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const KINDS = [
  "morning",
  "urgent",
  "weekly",
  "monthly",
  "theme",
  "on-demand",
  "intraday",
] as const;
export type ReportKind = typeof KINDS[number];

export interface ReportBody {
  title: string;
  summary: string;
  full_markdown: string;
  source_ids: string[];
  policy_decision_ids: string[];
  comparison_ids: string[];
  actionable_risk: boolean;
  material_thesis_change: boolean;
  intraday_triggered: boolean;
  suggestion_only: boolean;
}

export interface RecordReportPayload {
  id: string;
  idempotency_key: string;
  packet_id: string;
  market_date: string;
  kind: ReportKind;
  report: ReportBody;
  report_hash: string;
  rendered_text: string;
  rendered_hash: string;
}

export type ReportSuppressionReason =
  | "no_trigger"
  | "not_actionable"
  | "REPORT_POLICY_MISMATCH";

export interface RenderedReportDelivery {
  status: "ready" | "suppressed";
  body: string;
  parts: string[];
  reason?: ReportSuppressionReason;
  payload?: RecordReportPayload;
  actionable_fields?: Array<{
    evaluation_id: string;
    candidate_id: string;
    ticker: string;
    action: string;
    quantity: string | null;
    entry_low: string | null;
    entry_high: string | null;
    stop: string | null;
    target: string | null;
    urgency: "urgent" | "routine";
  }>;
}

export interface ReportPolicyDecision {
  evaluation_id: string;
  candidate_id: string;
  run_id: string;
  packet_id: string;
  packet_hash: string;
  ticker: string;
  status: "approved" | "downgraded" | "vetoed";
  final_action:
    | "buy"
    | "add"
    | "reduce"
    | "sell"
    | "hold"
    | "watch"
    | "avoid"
    | null;
  final_alert_urgency: "urgent" | "routine" | null;
  approved_terms: ApprovedTerms | null;
}

export function parseReportDecisions(
  value: unknown,
  runId: string,
  packetId: string,
  ids: readonly string[],
): ReportPolicyDecision[] {
  if (
    !UUID.test(runId) || !UUID.test(packetId) || !Array.isArray(value) ||
    value.length !== ids.length || value.length === 0 || value.length > 96
  ) {
    throw new Error("REPORT_POLICY_MISMATCH");
  }
  const seen = new Set<string>();
  const result = value.map((item): ReportPolicyDecision => {
    const row = object(item, "persisted decision");
    if (
      row.run_id !== runId || row.packet_id !== packetId ||
      typeof row.evaluation_id !== "string" ||
      !ids.includes(row.evaluation_id) || seen.has(row.evaluation_id) ||
      !UUID.test(String(row.candidate_id)) ||
      !HASH.test(String(row.packet_hash)) ||
      !/^[A-Z][A-Z0-9.-]{0,9}$/.test(String(row.ticker)) ||
      !["approved", "downgraded", "vetoed"].includes(String(row.status)) ||
      (row.final_action !== null &&
        !["buy", "add", "reduce", "sell", "hold", "watch", "avoid"].includes(
          String(row.final_action),
        ))
    ) {
      throw new Error("REPORT_POLICY_MISMATCH");
    }
    seen.add(row.evaluation_id);
    const actionable = row.status === "approved" &&
      ["buy", "add", "reduce", "sell"].includes(String(row.final_action));
    let terms: ApprovedTerms | null = null;
    if (actionable) {
      const proposed = object(row.approved_terms, "approved terms");
      exact(proposed, [
        "quantity",
        "entry_low",
        "entry_high",
        "stop",
        "target",
        "urgency",
      ], "approved terms");
      for (
        const field of ["quantity", "entry_low", "entry_high", "stop", "target"]
      ) {
        if (
          proposed[field] !== null && (typeof proposed[field] !== "string" ||
            !/^\d{1,15}(\.\d{1,8})?$/.test(proposed[field] as string) ||
            Number(proposed[field]) <= 0)
        ) {
          throw new Error("REPORT_POLICY_MISMATCH");
        }
      }
      if (
        proposed.quantity === null ||
        !["urgent", "routine"].includes(String(proposed.urgency))
      ) throw new Error("REPORT_POLICY_MISMATCH");
      terms = proposed as unknown as ApprovedTerms;
    }
    const finalAlertUrgency = row.final_alert_urgency === null
      ? null
      : (typeof row.final_alert_urgency === "string" &&
          ["urgent", "routine"].includes(row.final_alert_urgency)
        ? row.final_alert_urgency as "urgent" | "routine"
        : (() => {
          throw new Error("REPORT_POLICY_MISMATCH");
        })());
    if (
      finalAlertUrgency !== null &&
      (row.status !== "approved" || row.final_action !== "hold" ||
        row.approved_terms !== null)
    ) throw new Error("REPORT_POLICY_MISMATCH");
    return {
      evaluation_id: row.evaluation_id,
      candidate_id: String(row.candidate_id),
      run_id: runId,
      packet_id: packetId,
      packet_hash: String(row.packet_hash),
      ticker: String(row.ticker),
      status: row.status as ReportPolicyDecision["status"],
      final_action: row.final_action as ReportPolicyDecision["final_action"],
      final_alert_urgency: finalAlertUrgency,
      approved_terms: terms,
    };
  });
  if (new Set(result.map((row) => row.packet_hash)).size !== 1) {
    throw new Error("REPORT_POLICY_MISMATCH");
  }
  return result.sort((a, b) => a.evaluation_id.localeCompare(b.evaluation_id));
}

export interface ReportDeliveryOptions {
  dashboardBaseUrl: string;
  allowedDashboardOrigins: readonly string[];
  scheduled: boolean;
}

function isFriday(marketDate: string): boolean {
  return new Date(`${marketDate}T12:00:00.000Z`).getUTCDay() === 5;
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exact(
  row: Record<string, unknown>,
  keys: readonly string[],
  path: string,
): void {
  if (
    Object.keys(row).length !== keys.length || keys.some((key) => !(key in row))
  ) throw new Error(`${path} has invalid fields`);
}

function bounded(
  value: unknown,
  path: string,
  max: number,
  allowEmpty = false,
): string {
  if (
    typeof value !== "string" ||
    new TextEncoder().encode(value).byteLength > max ||
    (!allowEmpty && value.trim().length === 0)
  ) throw new Error(`${path} must be bounded`);
  return value;
}

function identifiers(value: unknown, path: string): string[] {
  if (
    !Array.isArray(value) || value.length > 96 ||
    value.some((item) => typeof item !== "string" || !UUID.test(item))
  ) throw new Error(`${path} must contain canonical UUIDs`);
  const result = value as string[];
  if (
    new Set(result).size !== result.length ||
    result.some((item, index) => index > 0 && result[index - 1] > item)
  ) throw new Error(`${path} must be sorted and unique`);
  return [...result];
}

export function reportIdFromKey(key: string): string {
  if (!HASH.test(key)) {
    throw new Error("idempotency_key must be a lowercase SHA-256 hash");
  }
  const value = key.slice(0, 32).split("");
  value[12] = "5";
  value[16] = "8";
  const hex = value.join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${
    hex.slice(16, 20)
  }-${hex.slice(20)}`;
}

export function parseRecordReportPayload(value: unknown): RecordReportPayload {
  const row = object(value, "report payload");
  exact(row, [
    "id",
    "idempotency_key",
    "packet_id",
    "market_date",
    "kind",
    "report",
    "report_hash",
    "rendered_text",
    "rendered_hash",
  ], "report payload");
  const kind = bounded(row.kind, "kind", 20) as ReportKind;
  if (!KINDS.includes(kind)) throw new Error("kind is invalid");
  const id = bounded(row.id, "id", 36);
  const packetId = bounded(row.packet_id, "packet_id", 36);
  if (!UUID.test(id) || !UUID.test(packetId)) {
    throw new Error("report identifiers must be UUIDs");
  }
  const key = bounded(row.idempotency_key, "idempotency_key", 64);
  if (!HASH.test(key)) {
    throw new Error("idempotency_key must be a lowercase SHA-256 hash");
  }
  if (id !== reportIdFromKey(key)) {
    throw new Error("report id does not match idempotency_key");
  }
  const marketDate = bounded(row.market_date, "market_date", 10);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(marketDate) ||
    new Date(`${marketDate}T00:00:00Z`).toISOString().slice(0, 10) !==
      marketDate
  ) throw new Error("market_date is invalid");
  const reportRow = object(row.report, "report");
  exact(reportRow, [
    "title",
    "summary",
    "full_markdown",
    "source_ids",
    "policy_decision_ids",
    "comparison_ids",
    "actionable_risk",
    "material_thesis_change",
    "intraday_triggered",
    "suggestion_only",
  ], "report");
  const report: ReportBody = {
    title: bounded(reportRow.title, "title", 200),
    summary: bounded(reportRow.summary, "summary", 1_000),
    full_markdown: bounded(reportRow.full_markdown, "full_markdown", 14_000),
    source_ids: identifiers(reportRow.source_ids, "source_ids"),
    policy_decision_ids: identifiers(
      reportRow.policy_decision_ids,
      "policy_decision_ids",
    ),
    comparison_ids: identifiers(reportRow.comparison_ids, "comparison_ids"),
    actionable_risk: reportRow.actionable_risk === true,
    material_thesis_change: reportRow.material_thesis_change === true,
    intraday_triggered: reportRow.intraday_triggered === true,
    suggestion_only: true,
  };
  if (
    reportRow.suggestion_only !== true ||
    typeof reportRow.actionable_risk !== "boolean" ||
    typeof reportRow.material_thesis_change !== "boolean" ||
    typeof reportRow.intraday_triggered !== "boolean"
  ) throw new Error("report authority fields are invalid");
  const reportHash = bounded(row.report_hash, "report_hash", 64);
  if (
    !HASH.test(reportHash) || reportHash !== sha256Hex(canonicalJson(report))
  ) throw new Error("report_hash does not match canonical report");
  const renderedText = bounded(row.rendered_text, "rendered_text", 14_000);
  const renderedHash = bounded(row.rendered_hash, "rendered_hash", 64);
  if (!HASH.test(renderedHash) || renderedHash !== sha256Hex(renderedText)) {
    throw new Error("rendered_hash does not match rendered text");
  }
  return {
    id,
    idempotency_key: key,
    packet_id: packetId,
    market_date: marketDate,
    kind,
    report,
    report_hash: reportHash,
    rendered_text: renderedText,
    rendered_hash: renderedHash,
  };
}

function escaped(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(
    ">",
    "&gt;",
  ).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function compact(value: string, max: number): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}

function compactUtf8(value: string, maxBytes: number): string {
  const text = value.replace(/\s+/g, " ").trim();
  const encoder = new TextEncoder();
  if (encoder.encode(text).byteLength <= maxBytes) return text;
  let result = "";
  for (const character of text) {
    if (encoder.encode(`${result}${character}…`).byteLength > maxBytes) break;
    result += character;
  }
  return `${result.trimEnd()}…`;
}

function compactValues(values: readonly string[], empty: string): string {
  const unique = [...new Set(values.filter((value) => value.trim()))];
  const visible = unique.slice(0, 1).map((value) => compactUtf8(value, 48));
  if (unique.length > visible.length) {
    visible.push(`+${unique.length - visible.length} more`);
  }
  return visible.join(", ") || empty;
}

function coverageSummary(coverage: Record<string, unknown>): string {
  const fields: string[] = [];
  for (
    const key of [
      "accepted_item_count",
      "complete_market_coverage",
      "coverage_status",
      "mode",
      "reference_status",
      "source_request_count",
    ]
  ) {
    const value = coverage[key];
    if (typeof value === "boolean" || Number.isSafeInteger(value)) {
      fields.push(`${key}=${String(value)}`);
    } else if (typeof value === "string" && value.trim()) {
      fields.push(`${key}=${compactUtf8(value, 64)}`);
    }
  }
  return fields.join("; ") || "bounded authenticated packet";
}

function reportUrl(
  id: string,
  dashboardBaseUrl: string,
  allowedOrigins: readonly string[],
): string {
  let base: URL;
  try {
    base = new URL(dashboardBaseUrl);
  } catch {
    throw new Error("dashboard URL must use HTTPS");
  }
  if (
    base.protocol !== "https:" || base.username || base.password ||
    (base.port && base.port !== "443")
  ) throw new Error("dashboard URL must use HTTPS");
  const allowed = allowedOrigins.some((origin) => {
    try {
      const candidate = new URL(origin);
      return candidate.protocol === "https:" &&
        candidate.origin === base.origin && candidate.pathname === "/" &&
        !candidate.search && !candidate.hash;
    } catch {
      return false;
    }
  });
  if (!allowed) throw new Error("dashboard origin is not allowlisted");
  return `${base.origin}/reports/${id}`;
}

function researchCatalogMarkdown(packet: EvidencePacketV2): string {
  const nextReview = typeof packet.coverage.next_review_at === "string" &&
      packet.coverage.next_review_at.trim()
    ? packet.coverage.next_review_at
    : "unavailable";
  const actionKeys = new Set(
    packet.action_candidates.map((candidate) => candidate.candidate_key),
  );
  const lines = [
    `Coverage: ${coverageSummary(packet.coverage)}`,
    `Next review: ${compactUtf8(nextReview, 64)}`,
  ];
  for (const candidate of packet.research_candidates) {
    const identity = compactUtf8(candidate.ticker ?? candidate.candidate_key, 80);
    const missing = compactValues(
      candidate.suitability.missing_reasons,
      "none",
    );
    const opposing = candidate.evidence.filter((evidence) =>
      evidence.role === "opposing"
    ).map((evidence) => evidence.item_id);
    const invalidation = compactValues([
      ...new Set([
        ...candidate.adverse_paths,
        ...candidate.limitations,
        ...candidate.suitability.veto_reasons,
      ]),
    ].sort(), "none recorded");
    lines.push(
      "",
      `## ${identity} — ${
        actionKeys.has(candidate.candidate_key)
          ? "ACTION LANE"
          : "RESEARCH ONLY"
      }`,
      `Research state: ${compactUtf8(candidate.research_state, 32)}`,
      `Suitability: ${candidate.suitability.state} (${missing})`,
      `Opposing evidence: ${compactValues(opposing, "none retained")}`,
      `Invalidation: ${invalidation}`,
    );
  }
  return lines.join("\n");
}

export function renderReportDelivery(
  input: RecordReportPayload,
  finalEvaluations: readonly ReportPolicyDecision[],
  options: ReportDeliveryOptions,
  researchPacket?: EvidencePacket,
): RenderedReportDelivery {
  if (!options) throw new Error("report delivery options are required");
  const value = parseRecordReportPayload(input);
  let decisions: ReportPolicyDecision[];
  let packetHash: string;
  let researchOnlyPacket: EvidencePacketV2 | null = null;
  let v2ReportPacket: EvidencePacketV2 | null = null;
  try {
    if (value.report.policy_decision_ids.length === 0) {
      if (
        finalEvaluations.length !== 0 || !researchPacket ||
        !isEvidencePacketV2(researchPacket) ||
        researchPacket.action_candidates.length !== 0
      ) throw new Error("REPORT_POLICY_MISMATCH");
      const evidenceIds = [
        ...new Set(
          researchPacket.research_candidates.flatMap((candidate) =>
            candidate.evidence.map((evidence) => evidence.item_id)
          ),
        ),
      ].sort();
      const packetEvidenceIds = researchPacket.evidence.map((evidence) =>
        evidence.item_id
      ).sort();
      if (
        canonicalJson(evidenceIds) !== canonicalJson(value.report.source_ids) ||
        canonicalJson(packetEvidenceIds) !== canonicalJson(evidenceIds) ||
        (researchPacket.research_candidates.length === 0 &&
          researchPacket.evidence.length !== 0)
      ) {
        throw new Error("REPORT_POLICY_MISMATCH");
      }
      decisions = [];
      researchOnlyPacket = researchPacket;
      v2ReportPacket = researchPacket;
      packetHash = sha256Hex(canonicalJson(researchPacket));
    } else {
      if (value.report.source_ids.length === 0) {
        throw new Error("REPORT_POLICY_MISMATCH");
      }
      decisions = parseReportDecisions(
        finalEvaluations,
        finalEvaluations[0]?.run_id,
        value.packet_id,
        value.report.policy_decision_ids,
      );
      packetHash = decisions[0].packet_hash;
      if (researchPacket) {
        if (
          !isEvidencePacketV2(researchPacket) ||
          sha256Hex(canonicalJson(researchPacket)) !== packetHash
        ) throw new Error("REPORT_POLICY_MISMATCH");
        const evidenceIds = [
          ...new Set(
            researchPacket.research_candidates.flatMap((candidate) =>
              candidate.evidence.map((evidence) => evidence.item_id)
            ),
          ),
        ].sort();
        if (
          canonicalJson(evidenceIds) !== canonicalJson(value.report.source_ids)
        ) throw new Error("REPORT_POLICY_MISMATCH");
        v2ReportPacket = researchPacket;
      }
    }
    const key = sha256Hex(
      `v2:${value.kind}:${value.market_date}:${packetHash}:${value.report_hash}`,
    );
    if (value.idempotency_key !== key) {
      throw new Error("REPORT_POLICY_MISMATCH");
    }
  } catch {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: "REPORT_POLICY_MISMATCH",
    };
  }
  if (researchOnlyPacket) {
    const scheduledFridayStatus = value.kind === "weekly" &&
      options.scheduled && isFriday(value.market_date);
    const lines = [
      `# ${value.kind.toUpperCase()} RESEARCH — ${value.market_date}`,
      "",
      researchCatalogMarkdown(researchOnlyPacket),
    ];
    lines.push("", "Suggestion only; review manually. No order was placed.");
    const fullMarkdown = lines.join("\n");
    if (new TextEncoder().encode(fullMarkdown).byteLength > 14_000) {
      return {
        status: "suppressed",
        body: "",
        parts: [],
        reason: "REPORT_POLICY_MISMATCH",
      };
    }
    const canonicalReport: ReportBody = {
      ...value.report,
      title: `${value.kind.toUpperCase()} RESEARCH — ${value.market_date}`,
      summary: scheduledFridayStatus
        ? `${researchOnlyPacket.research_candidates.length} research candidate(s) reviewed; no policy-approved action today.`
        : `${researchOnlyPacket.research_candidates.length} research candidate(s); no action is eligible.`,
      full_markdown: fullMarkdown,
      actionable_risk: false,
      material_thesis_change: false,
      intraday_triggered: false,
    };
    const reportHash = sha256Hex(canonicalJson(canonicalReport));
    const key = sha256Hex(
      `v2:${value.kind}:${value.market_date}:${packetHash}:${reportHash}`,
    );
    const payload: RecordReportPayload = {
      ...value,
      id: reportIdFromKey(key),
      idempotency_key: key,
      report: canonicalReport,
      report_hash: reportHash,
      rendered_text: fullMarkdown,
      rendered_hash: sha256Hex(fullMarkdown),
    };
    if (
      options.scheduled &&
      (value.kind === "morning" || scheduledFridayStatus)
    ) {
      const heading = value.kind === "weekly"
        ? "FRIDAY POST-MARKET RESEARCH"
        : "MORNING RESEARCH";
      const coverage = scheduledFridayStatus &&
          !researchOnlyPacket.coverage.complete_market_coverage
        ? "\nMarket data coverage was incomplete."
        : "";
      const auditLink = scheduledFridayStatus
        ? `\n${
          reportUrl(
            payload.id,
            options.dashboardBaseUrl,
            options.allowedDashboardOrigins,
          )
        }`
        : "";
      const body = compact(
        `<b>${heading} — ${value.market_date}</b>\n${
          researchOnlyPacket.research_candidates.length
        } research candidate(s) reviewed; no policy-approved action today.` +
          coverage +
          "\n\nSuggestion only; review manually. No order was placed." +
          auditLink,
        1_200,
      );
      return {
        status: "ready",
        body,
        parts: [body],
        payload: {
          ...payload,
          rendered_text: body,
          rendered_hash: sha256Hex(body),
        },
      };
    }
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: value.kind === "intraday" ? "no_trigger" : "not_actionable",
      payload,
    };
  }
  const actionableFields = decisions.filter((row) =>
    row.approved_terms !== null
  ).map((row) => ({
    evaluation_id: row.evaluation_id,
    candidate_id: row.candidate_id,
    ticker: row.ticker,
    action: row.final_action!,
    ...row.approved_terms!,
  }));
  const finalAlertTriggered = decisions.some((row) =>
    row.final_alert_urgency !== null
  );
  const effectiveUrgency: "urgent" | "routine" | null =
    decisions.some((row) =>
        row.final_alert_urgency === "urgent" ||
        row.approved_terms?.urgency === "urgent"
      )
      ? "urgent"
      : (decisions.some((row) => row.final_alert_urgency === "routine")
        ? "routine"
        : null);
  const finalKind: ReportKind = effectiveUrgency === "urgent"
    ? "urgent"
    : (effectiveUrgency === "routine" ? "intraday" : value.kind);
  const urgent = effectiveUrgency === "urgent";
  const scheduledFridayNoAction = options.scheduled &&
    finalKind === "weekly" &&
    isFriday(value.market_date) &&
    !finalAlertTriggered &&
    decisions.every((row) => row.approved_terms === null);
  const heading = scheduledFridayNoAction
    ? "FRIDAY POST-MARKET RESEARCH"
    : (effectiveUrgency === "urgent"
    ? "URGENT RESEARCH REVIEW"
    : (effectiveUrgency === "routine"
      ? "INTRADAY RESEARCH"
      : (urgent
        ? "URGENT RESEARCH REVIEW"
        : `${finalKind.toUpperCase()} RESEARCH`)));
  const lines = decisions.map((row) => {
    if (row.final_alert_urgency !== null) {
      return `${row.ticker}: POLICY-APPROVED ${row.final_alert_urgency.toUpperCase()} ALERT. Manual review required.`;
    }
    const terms = row.approved_terms;
    if (!terms) {
      return `${row.ticker}: ${
        row.final_action === null
          ? "INSUFFICIENT"
          : row.final_action.toUpperCase()
      }. No action terms approved.`;
    }
    return `${row.ticker}: ${
      row.final_action!.toUpperCase()
    } ${terms.quantity} shares; ` +
      `entry ${terms.entry_low ?? "unavailable"}–${
        terms.entry_high ?? "unavailable"
      }; ` +
      `stop ${terms.stop ?? "unavailable"}; target ${
        terms.target ?? "unavailable"
      }; ${terms.urgency}.`;
  });
  const telegramLines = decisions.filter((row) =>
    row.approved_terms !== null || row.final_alert_urgency !== null
  ).map((row) => {
    if (row.final_alert_urgency !== null) {
      return `${row.ticker}: POLICY-APPROVED ${row.final_alert_urgency.toUpperCase()} ALERT. Manual review required.`;
    }
    const terms = row.approved_terms!;
    return `${row.ticker}: ${row.final_action!.toUpperCase()} ${terms.quantity} shares; ` +
      `entry ${terms.entry_low ?? "unavailable"}–${terms.entry_high ?? "unavailable"}; ` +
      `stop ${terms.stop ?? "unavailable"}; target ${terms.target ?? "unavailable"}; ${terms.urgency}.`;
  });
  const reportDetail = lines.join("\n\n") +
    (v2ReportPacket
      ? `\n\n## Research catalog\n\n${researchCatalogMarkdown(v2ReportPacket)}`
      : "") +
    "\n\nSuggestion only; review manually. No order was placed.";
  if (new TextEncoder().encode(reportDetail).byteLength > 14_000) {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: "REPORT_POLICY_MISMATCH",
    };
  }
  const approvedReport: ReportBody = {
    ...value.report,
    title: `${heading} — ${value.market_date}`,
    summary: compact(
      (telegramLines.length > 0 ? telegramLines : lines).join(" ") +
        (scheduledFridayNoAction
          ? " No policy-approved action today."
          : ""),
      720,
    ),
    full_markdown: reportDetail,
    actionable_risk: urgent,
    material_thesis_change: urgent,
    intraday_triggered: actionableFields.length > 0 || finalAlertTriggered,
  };
  const approvedHash = sha256Hex(canonicalJson(approvedReport));
  const approvedKey = sha256Hex(
    `v2:${finalKind}:${value.market_date}:${packetHash}:${approvedHash}`,
  );
  const approvedId = reportIdFromKey(approvedKey);
  const coverageDisclosure = scheduledFridayNoAction && v2ReportPacket &&
      !v2ReportPacket.coverage.complete_market_coverage
    ? "\nMarket data coverage was incomplete."
    : "";
  let body = `<b>${heading} — ${value.market_date}</b>\n${
    escaped(approvedReport.summary)
  }${coverageDisclosure}\n\nSuggestion only; review manually. No order was placed.`;
  if (["weekly", "monthly", "theme"].includes(finalKind)) {
    body += `\n${
      reportUrl(
        approvedId,
        options.dashboardBaseUrl,
        options.allowedDashboardOrigins,
      )
    }`;
  }
  body = compact(body, 1_200);
  const canonicalPayload: RecordReportPayload = {
    ...value,
    kind: finalKind,
    id: approvedId,
    idempotency_key: approvedKey,
    report: approvedReport,
    report_hash: approvedHash,
    rendered_text: body,
    rendered_hash: sha256Hex(body),
  };
  if (
    actionableFields.length === 0 && !finalAlertTriggered &&
    (finalKind !== "morning" || !options.scheduled) &&
    !scheduledFridayNoAction
  ) {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: finalKind === "intraday" ? "no_trigger" : "not_actionable",
      payload: canonicalPayload,
    };
  }
  if (finalKind === "urgent" && !urgent) {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: "not_actionable",
      payload: canonicalPayload,
    };
  }
  return {
    status: "ready",
    body,
    parts: [body],
    payload: canonicalPayload,
    ...(actionableFields.length > 0
      ? { actionable_fields: actionableFields }
      : {}),
  };
}
