import { canonicalJson, sha256Hex } from "./intelligence.ts";

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

export interface RenderedReportDelivery {
  status: "ready" | "suppressed";
  body: string;
  parts: string[];
  reason?: "no_trigger" | "not_actionable";
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
    typeof value !== "string" || value.length > max ||
    (!allowEmpty && value.trim().length === 0)
  ) throw new Error(`${path} must be bounded`);
  return value;
}

function identifiers(value: unknown, path: string): string[] {
  if (
    !Array.isArray(value) || value.length > 96 ||
    value.some((item) =>
      typeof item !== "string" || item.length < 1 || item.length > 100
    )
  ) throw new Error(`${path} must be bounded identifiers`);
  const result = value as string[];
  if (
    new Set(result).size !== result.length ||
    result.some((item, index) => index > 0 && result[index - 1] > item)
  ) throw new Error(`${path} must be sorted and unique`);
  return [...result];
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

export function renderReportDelivery(
  input: RecordReportPayload,
  options: {
    dashboardBaseUrl: string;
    allowedDashboardOrigins: readonly string[];
  },
): RenderedReportDelivery {
  const value = parseRecordReportPayload(input);
  if (value.kind === "intraday" && !value.report.intraday_triggered) {
    return { status: "suppressed", body: "", parts: [], reason: "no_trigger" };
  }
  if (
    value.kind === "urgent" && !value.report.actionable_risk &&
    !value.report.material_thesis_change
  ) {
    return {
      status: "suppressed",
      body: "",
      parts: [],
      reason: "not_actionable",
    };
  }
  const heading = value.kind === "urgent"
    ? "URGENT RESEARCH REVIEW"
    : `${value.kind.toUpperCase()} RESEARCH`;
  let body = `<b>${heading} — ${value.market_date}</b>\n${
    escaped(compact(value.report.summary, 720))
  }\n\nSuggestion only; review manually. No order was placed.`;
  if (["weekly", "monthly", "theme"].includes(value.kind)) {
    body += `\n${
      reportUrl(
        value.id,
        options.dashboardBaseUrl,
        options.allowedDashboardOrigins,
      )
    }`;
  }
  body = compact(body, 1_200);
  return { status: "ready", body, parts: [body] };
}
