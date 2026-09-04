import type {
  AlertsView,
  IdeasView,
  RunDetailView,
  RunsView,
  SystemView,
  TodayView,
} from "../../../packages/dashboard-contracts/src/index.ts";

import { createDashboardDatabase, type DashboardDatabaseFactory, validateDashboardDatabaseUrl } from "./database.ts";
import { DashboardHttpError } from "./errors.ts";
import { classifyFreshness, NYSE_HOLIDAYS_2026 } from "./freshness.ts";
import type { DashboardReader, DashboardReadResult } from "./handler.ts";
import {
  mapCompanionResponse,
  mapIdea,
  mapPortfolio,
  mapPublicationReceipt,
  mapRun,
  mapTransactions,
} from "./mappers.ts";
import type { DashboardRoute } from "./routes.ts";

type Row = Record<string, unknown>;

const HOLDINGS = `SELECT h.ticker, h.shares, h.avg_cost, h.bucket, h.opened_at, h.stop, h.target,
       latest.normalized->>'verified_price' AS price,
       latest.normalized->>'quote_as_of' AS price_as_of,
       latest.normalized->>'quote_source' AS price_source,
       latest.normalized->>'quote_market_state' AS price_market_state,
       latest.created_at AS price_receipt_at
  FROM public.holdings h
  LEFT JOIN LATERAL (
    SELECT de.normalized, de.created_at
      FROM public.decision_evaluations de
     WHERE de.normalized->>'ticker' = h.ticker
       AND COALESCE(de.normalized->>'quote_as_of', '') <> ''
     ORDER BY de.created_at DESC LIMIT 1
  ) latest ON true
 ORDER BY h.ticker LIMIT 100`;

const PLANS = `SELECT id, ticker, bucket, amount, cadence, next_due_on, due_day, active, created_at, updated_at
  FROM public.owner_investment_plans ORDER BY active DESC, next_due_on, ticker LIMIT 100`;

const TRANSACTIONS = `SELECT id, ts, ticker, side, qty, price, source, executed_on
  FROM public.transactions WHERE ($1::bigint IS NULL OR id < $1::bigint)
 ORDER BY id DESC LIMIT 51`;

const IDEAS = `SELECT s.id, s.ticker, s.action, s.bucket, s.depth AS profile, s.entry_zone_low,
       s.entry_zone_high, s.valid_until, s.stop, s.target, s.confidence, s.bull, s.bear,
       s.decisive_factor, s.invalidation_level, s.reason, s.evaluation_id,
       de.final_action, de.policy_status, de.policy_version, de.reason_codes,
       de.evidence, de.analyst, de.checker, de.created_at
  FROM public.suggestions s
  LEFT JOIN public.decision_evaluations de ON de.id = s.evaluation_id
 WHERE ($1::text IS NULL OR de.policy_status = $1)
   AND ($2::bigint IS NULL OR s.id < $2::bigint)
 ORDER BY s.id DESC LIMIT 51`;

const COMPANION = `SELECT request_id, response, finished_at
  FROM public.market_gateway_requests
 WHERE operation = 'evaluate_and_publish' AND status = 'completed'
   AND response->'companion_analysis' IS NOT NULL
 ORDER BY finished_at DESC NULLS LAST LIMIT 1`;

const ALERTS = `SELECT p.id, p.kind, p.phase, p.status, p.rendered_body, p.rendered_hash,
       p.template_version, p.telegram_message_ids, p.attempt_count, p.created_at,
       p.delivered_at,
       (SELECT r.ticker FROM public.market_alert_drafts d
          JOIN public.market_alert_rules r ON r.source_draft_id = d.id
         WHERE d.publication_id = p.id LIMIT 1) AS rule_ticker,
       (SELECT r.state FROM public.market_alert_drafts d
          JOIN public.market_alert_rules r ON r.source_draft_id = d.id
         WHERE d.publication_id = p.id LIMIT 1) AS rule_state,
       (SELECT e.status FROM public.market_alert_events e
         WHERE e.publication_id = p.id ORDER BY e.persisted_at DESC LIMIT 1) AS event_status,
       (SELECT a.action FROM public.market_alert_actions a
         WHERE a.publication_id = p.id ORDER BY a.received_at DESC LIMIT 1) AS owner_action,
       COALESCE(
         (SELECT de.evidence FROM public.market_alert_drafts d
            JOIN public.decision_evaluations de ON de.id = d.source_evaluation_id
           WHERE d.publication_id = p.id LIMIT 1),
         (SELECT de.evidence FROM public.market_alert_events e
            JOIN public.market_alert_rules r ON r.id = e.rule_id
            JOIN public.market_alert_drafts d ON d.id = r.source_draft_id
            JOIN public.decision_evaluations de ON de.id = d.source_evaluation_id
           WHERE e.publication_id = p.id ORDER BY e.persisted_at DESC LIMIT 1)
       ) AS sources
  FROM public.market_publications p
 WHERE ($1::text IS NULL OR p.status = $1)
   AND ($2::timestamptz IS NULL OR (p.created_at, p.id) < ($2::timestamptz, $3::uuid))
 ORDER BY p.created_at DESC, p.id DESC LIMIT 51`;

const RUNS = `SELECT r.id, r.kind, r.status, r.started_at, r.finished_at, r.data_as_of,
       r.summary, r.write_counts, r.telegram_message_ids,
       (SELECT max(e.policy_version) FROM public.decision_evaluations e WHERE e.run_id = r.id) AS policy_version,
       (SELECT count(*) FROM public.decision_evaluations e WHERE e.run_id = r.id) AS evaluation_count,
       (SELECT count(*) FROM public.suggestions s WHERE s.run_id = r.id) AS suggestion_count,
       (SELECT p.status FROM public.market_publications p WHERE p.run_id = r.id LIMIT 1) AS publication_status,
       (SELECT p.telegram_message_ids FROM public.market_publications p WHERE p.run_id = r.id LIMIT 1) AS publication_message_ids
  FROM public.analysis_runs r
 WHERE ($1::text IS NULL OR r.kind = $1)
   AND ($2::timestamptz IS NULL OR (r.started_at, r.id) < ($2::timestamptz, $3::uuid))
 ORDER BY r.started_at DESC, r.id DESC LIMIT 51`;

const RUN_DETAIL = `SELECT r.id, r.kind, r.status, r.started_at, r.finished_at, r.data_as_of,
       r.summary, r.write_counts, r.telegram_message_ids,
       (SELECT max(e.policy_version) FROM public.decision_evaluations e WHERE e.run_id = r.id) AS policy_version,
       (SELECT count(*) FROM public.decision_evaluations e WHERE e.run_id = r.id) AS evaluation_count,
       (SELECT count(*) FROM public.suggestions s WHERE s.run_id = r.id) AS suggestion_count,
       (SELECT p.status FROM public.market_publications p WHERE p.run_id = r.id LIMIT 1) AS publication_status,
       (SELECT p.telegram_message_ids FROM public.market_publications p WHERE p.run_id = r.id LIMIT 1) AS publication_message_ids
  FROM public.analysis_runs r WHERE r.id = $1::uuid LIMIT 1`;

const RUN_REQUESTS = `SELECT request_id, operation, status, response_digest, attempt_count, finished_at
  FROM public.market_gateway_requests WHERE run_id = $1::uuid
 ORDER BY created_at LIMIT 50`;

const RUN_EVALUATIONS = `SELECT s.id, s.ticker, s.action, s.bucket, s.depth AS profile,
       s.entry_zone_low, s.entry_zone_high, s.valid_until, s.stop, s.target, s.confidence,
       s.bull, s.bear, s.decisive_factor, s.invalidation_level, s.evaluation_id,
       e.final_action, e.policy_status, e.policy_version, e.reason_codes, e.evidence,
       e.analyst, e.checker, e.created_at
  FROM public.decision_evaluations e
  LEFT JOIN public.suggestions s ON s.evaluation_id = e.id
 WHERE e.run_id = $1::uuid ORDER BY e.created_at LIMIT 50`;

const ACTIVE_POLICY = `SELECT version, config, activated_at FROM public.market_policy_config
 WHERE active = true ORDER BY version DESC LIMIT 1`;

const STATEMENTS = new Set([
  HOLDINGS, PLANS, TRANSACTIONS, IDEAS, COMPANION, ALERTS, RUNS, RUN_DETAIL,
  RUN_REQUESTS, RUN_EVALUATIONS, ACTIVE_POLICY,
]);

type PageKind = "transactions" | "ideas" | "alerts" | "runs";
interface PageCursor { v: 1; k: PageKind; id: string; at?: string }

function invalidCursor(): never {
  throw new DashboardHttpError(400, "invalid_request", "Invalid cursor.");
}

function decodeCursor(value: string | undefined, kind: PageKind): PageCursor | null {
  if (!value) return null;
  try {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const parsed = JSON.parse(atob(padded)) as Partial<PageCursor>;
    if (parsed.v !== 1 || parsed.k !== kind || typeof parsed.id !== "string") invalidCursor();
    if (kind === "transactions" || kind === "ideas") {
      if (!/^\d{1,20}$/.test(parsed.id)) invalidCursor();
    } else if (!/^[0-9a-f]{8}-[0-9a-f-]{27}$/.test(parsed.id) || !iso(parsed.at)) {
      invalidCursor();
    }
    return parsed as PageCursor;
  } catch (error) {
    if (error instanceof DashboardHttpError) throw error;
    return invalidCursor();
  }
}

function encodeCursor(kind: PageKind, row: Row): string {
  const payload: PageCursor = { v: 1, k: kind, id: String(row.id ?? "") };
  if (kind === "alerts") payload.at = iso(row.created_at) ?? invalidCursor();
  if (kind === "runs") payload.at = iso(row.started_at) ?? invalidCursor();
  const value = btoa(JSON.stringify(payload));
  return value.replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function page(rows: Row[], kind: PageKind): { rows: Row[]; nextCursor: string | null } {
  const visible = rows.slice(0, 50);
  return {
    rows: visible,
    nextCursor: rows.length > 50 && visible[49] ? encodeCursor(kind, visible[49]) : null,
  };
}

function iso(value: unknown): string | null {
  if (value instanceof Date && !Number.isNaN(value.valueOf())) return value.toISOString();
  if (typeof value !== "string") return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? null : parsed.toISOString();
}

function latestTimestamp(rows: readonly Row[], fields: readonly string[]): string | null {
  let latest: string | null = null;
  for (const row of rows) {
    for (const field of fields) {
      const value = iso(row[field]);
      if (value && (!latest || value > latest)) latest = value;
    }
  }
  return latest;
}

function oldestTimestamp(values: readonly (string | null)[]): string | null {
  return values.filter((value): value is string => Boolean(value)).sort().at(0) ?? null;
}

function combinedFreshness(results: readonly DashboardReadResult[]): DashboardReadResult["freshness"] {
  if (results.every((item) => item.freshness === "unavailable")) return "unavailable";
  if (results.some((item) => item.freshness === "stale")) return "stale";
  if (results.some((item) => item.freshness === "partial" || item.freshness === "unavailable")) return "partial";
  return "fresh";
}

const boundaries: TodayView["boundaries"] = {
  owner_only: true,
  suggestion_only: true,
  friend_invitations: "disabled",
  brokerage_authority: "none",
};

export function createDashboardRepository(
  databaseUrl: string,
  databaseFactory: DashboardDatabaseFactory = createDashboardDatabase,
  now: () => Date = () => new Date(),
): DashboardReader {
  validateDashboardDatabaseUrl(databaseUrl);
  const database = databaseFactory(databaseUrl);
  const query = (statement: string, parameters: readonly unknown[] = []) => {
    if (!STATEMENTS.has(statement)) throw new Error("dashboard query is not allowlisted");
    return database.query(statement, parameters);
  };
  const calendar = { holidays: NYSE_HOLIDAYS_2026 };

  async function holdings(): Promise<Row[]> {
    const rows = await query(HOLDINGS);
    const current = now();
    return rows.map((row) => {
      const classified = classifyFreshness({
        kind: "price",
        dataAsOf: iso(row.price_as_of),
        sourceMarketState: typeof row.price_market_state === "string" ? row.price_market_state : null,
      }, current, calendar);
      return {
        ...row,
        price_as_of: classified.dataAsOf,
        price_freshness: classified.freshness,
        price_display_state: classified.marketState,
      };
    });
  }

  async function portfolio(): Promise<DashboardReadResult> {
    const [holdingRows, planRows, transactionRows] = await Promise.all([
      holdings(),
      query(PLANS),
      query(TRANSACTIONS, [null]),
    ]);
    const dataAsOf = latestTimestamp(holdingRows, ["price_as_of"]);
    const mapped = mapPortfolio(holdingRows, planRows, transactionRows);
    const freshness = mapped.holdings.length === 0
      ? "unavailable"
      : mapped.holdings.some((item) => item.freshness !== "fresh") ? "partial" : "fresh";
    const marketState = holdingRows.find((row) => typeof row.price_display_state === "string")?.price_display_state;
    return { data: mapped, dataAsOf, freshness, marketState: typeof marketState === "string" ? marketState as DashboardReadResult["marketState"] : "unknown" };
  }

  async function ideas(route: DashboardRoute): Promise<DashboardReadResult> {
    const cursor = decodeCursor(route.cursor, "ideas");
    const result = page(await query(IDEAS, [route.status ?? null, cursor?.id ?? null]), "ideas");
    const mapped: IdeasView = { ideas: result.rows.map(mapIdea) };
    const dataAsOf = latestTimestamp(result.rows, ["created_at"]);
    const state = classifyFreshness({ kind: "run", phase: "on-demand", dataAsOf }, now(), calendar);
    return { data: mapped, dataAsOf, freshness: state.freshness, marketState: state.marketState, nextCursor: result.nextCursor };
  }

  async function companion(): Promise<DashboardReadResult> {
    const rows = await query(COMPANION);
    const data = mapCompanionResponse(rows[0]?.response) ?? {
      status: "not_reviewed",
      baseline_ticker: null,
      companion_ticker: null,
      role: null,
      thesis: null,
      risk_note: null,
      plan_unchanged: true,
      recurring_plan_review_eligible: false,
      horizons: [],
      contribution_history: null,
      evidence: [],
      disclaimer: "No structured companion receipt is available. This is suggestion only.",
    };
    const dataAsOf = iso(rows[0]?.finished_at);
    const state = classifyFreshness({ kind: "run", phase: "on-demand", dataAsOf }, now(), calendar);
    return { data, dataAsOf, freshness: state.freshness, marketState: state.marketState };
  }

  async function alerts(route: DashboardRoute): Promise<DashboardReadResult> {
    const cursor = decodeCursor(route.cursor, "alerts");
    const result = page(await query(ALERTS, [route.state ?? null, cursor?.at ?? null, cursor?.id ?? null]), "alerts");
    const mapped: AlertsView = { alerts: result.rows.map(mapPublicationReceipt) };
    const dataAsOf = latestTimestamp(result.rows, ["delivered_at", "created_at"]);
    const state = classifyFreshness({ kind: "brief", dataAsOf, phase: String(result.rows[0]?.phase ?? ""), status: String(result.rows[0]?.status ?? "") }, now(), calendar);
    return { data: mapped, dataAsOf, freshness: state.freshness, marketState: state.marketState, nextCursor: result.nextCursor };
  }

  async function runs(route: DashboardRoute): Promise<DashboardReadResult> {
    const cursor = decodeCursor(route.cursor, "runs");
    const result = page(await query(RUNS, [route.kind ?? null, cursor?.at ?? null, cursor?.id ?? null]), "runs");
    const mapped: RunsView = { runs: result.rows.map(mapRun) };
    const dataAsOf = latestTimestamp(result.rows, ["data_as_of", "finished_at", "started_at"]);
    const state = classifyFreshness({ kind: "run", dataAsOf, phase: String(result.rows[0]?.kind ?? ""), status: String(result.rows[0]?.status ?? "") }, now(), calendar);
    return { data: mapped, dataAsOf, freshness: state.freshness, marketState: state.marketState, nextCursor: result.nextCursor };
  }

  async function runDetail(route: DashboardRoute): Promise<DashboardReadResult> {
    const id = route.id ?? "";
    const [runRows, requests, evaluations] = await Promise.all([
      query(RUN_DETAIL, [id]),
      query(RUN_REQUESTS, [id]),
      query(RUN_EVALUATIONS, [id]),
    ]);
    const runRow = runRows[0];
    if (!runRow) throw new DashboardHttpError(404, "not_found", "Run receipt not found.");
    const run = mapRun(runRow);
    const writeCounts = runRow.write_counts && typeof runRow.write_counts === "object" && !Array.isArray(runRow.write_counts)
      ? runRow.write_counts as Record<string, number>
      : {};
    const ids = Array.isArray(runRow.telegram_message_ids)
      ? runRow.telegram_message_ids.filter((item): item is number => Number.isSafeInteger(item))
      : [];
    const incomplete: string[] = [];
    if (requests.length === 0) incomplete.push("gateway_request");
    if (!run.finished_at) incomplete.push("finished_run");
    const data: RunDetailView = {
      run,
      request_receipts: requests.slice(0, 50).map((row) => ({
        request_id: String(row.request_id ?? ""),
        operation: String(row.operation ?? ""),
        status: String(row.status ?? ""),
        response_digest: typeof row.response_digest === "string" ? row.response_digest : null,
        attempt_count: Number(row.attempt_count ?? 0),
        finished_at: iso(row.finished_at),
      })),
      evaluations: evaluations.map(mapIdea),
      write_counts: writeCounts,
      telegram_message_ids: ids,
      incomplete_stages: incomplete,
    };
    const state = classifyFreshness({ kind: "run", dataAsOf: run.data_as_of ?? run.finished_at, phase: run.kind, status: run.status }, now(), calendar);
    return { data, dataAsOf: state.dataAsOf, freshness: state.freshness, marketState: state.marketState };
  }

  async function system(): Promise<DashboardReadResult> {
    const [runRows, alertRows, policyRows] = await Promise.all([
      query(RUNS, [null, null, null]),
      query(ALERTS, [null, null, null]),
      query(ACTIVE_POLICY),
    ]);
    const latestByKind: SystemView["latest_by_kind"] = {};
    for (const row of runRows.slice(0, 50)) {
      const run = mapRun(row);
      if (!latestByKind[run.kind]) latestByKind[run.kind] = run;
    }
    const activeConfig = policyRows[0]?.config && typeof policyRows[0].config === "object"
      ? policyRows[0].config as Row
      : {};
    const alertsConfig = activeConfig.alerts_v3 && typeof activeConfig.alerts_v3 === "object"
      ? activeConfig.alerts_v3 as Row
      : {};
    const alertMode = alertsConfig.enabled === true
      ? "enabled"
      : alertsConfig.shadow === true
      ? "shadow"
      : "unavailable";
    const data: SystemView = {
      product_version: "personal-stock-agent-web-v1",
      api_version: "v1",
      policy_version: Number.isInteger(Number(policyRows[0]?.version)) ? Number(policyRows[0]?.version) : null,
      alert_mode: alertMode,
      latest_by_kind: latestByKind,
      latest_publication_status: alertRows[0] ? mapPublicationReceipt(alertRows[0]).state : null,
      boundaries,
    };
    const dataAsOf = latestTimestamp(runRows, ["data_as_of", "finished_at"]);
    const state = classifyFreshness({ kind: "run", dataAsOf, phase: String(runRows[0]?.kind ?? ""), status: String(runRows[0]?.status ?? "") }, now(), calendar);
    return { data, dataAsOf, freshness: state.freshness, marketState: state.marketState };
  }

  return {
    read: async (route): Promise<DashboardReadResult> => {
      if (route.name === "meta") {
        const policy = await query(ACTIVE_POLICY);
        return {
          data: { product: "Personal Stock Agent", api_version: "v1", contract_version: 1, boundaries },
          dataAsOf: iso(policy[0]?.activated_at),
          freshness: policy.length > 0 ? "fresh" : "unavailable",
          marketState: "unknown",
        };
      }
      if (route.name === "portfolio") return await portfolio();
      if (route.name === "transactions") {
        const cursor = decodeCursor(route.cursor, "transactions");
        const result = page(await query(TRANSACTIONS, [cursor?.id ?? null]), "transactions");
        return {
          data: { transactions: mapTransactions(result.rows) },
          dataAsOf: latestTimestamp(result.rows, ["ts"]),
          freshness: result.rows.length > 0 ? "fresh" : "unavailable",
          marketState: "unknown",
          nextCursor: result.nextCursor,
        };
      }
      if (route.name === "ideas") return await ideas(route);
      if (route.name === "companion") return await companion();
      if (route.name === "alerts") return await alerts(route);
      if (route.name === "runs") return await runs(route);
      if (route.name === "runDetail") return await runDetail(route);
      if (route.name === "system") return await system();
      if (route.name === "today") {
        const [portfolioResult, runsResult, ideasResult, companionResult, alertsResult] = await Promise.all([
          portfolio(),
          runs({ name: "runs" }),
          ideas({ name: "ideas", status: "approved" }),
          companion(),
          alerts({ name: "alerts" }),
        ]);
        const portfolioData = portfolioResult.data as unknown as ReturnType<typeof mapPortfolio>;
        const runsData = runsResult.data as unknown as RunsView;
        const ideasData = ideasResult.data as unknown as IdeasView;
        const companionData = companionResult.data as unknown as TodayView["companion"];
        const alertsData = alertsResult.data as unknown as AlertsView;
        const attention: TodayView["attention"] = [];
        for (const item of portfolioData.holdings.filter((holding) => holding.freshness !== "fresh").slice(0, 5)) {
          attention.push({
            id: `holding-${item.ticker}`,
            severity: "update",
            title: `${item.ticker} price needs a fresh receipt`,
            detail: "Derived value and performance are withheld until a supported price is available.",
            data_as_of: item.price_as_of,
            destination: "/portfolio",
          });
        }
        for (const item of alertsData.alerts.filter((alert) => ["delivery_failed", "delivery_unknown", "incomplete"].includes(alert.state)).slice(0, 5 - attention.length)) {
          attention.push({
            id: `alert-${item.id}`,
            severity: "system",
            title: "Alert receipt needs review",
            detail: `The ${item.kind} publication is ${item.state}.`,
            data_as_of: item.delivered_at ?? item.created_at,
            destination: "/alerts",
          });
        }
        const data: TodayView = {
          boundaries,
          attention,
          latest_run: runsData.runs[0] ?? null,
          portfolio: {
            value: portfolioData.totals.value,
            cost_basis: portfolioData.totals.cost_basis,
            unrealized_amount: portfolioData.totals.unrealized_amount,
            holdings: portfolioData.holdings,
          },
          market_summary: null,
          entry_zones: ideasData.ideas.filter((item) => item.entry_zone_low && item.entry_zone_high).slice(0, 10),
          companion: companionData,
        };
        const supporting = [portfolioResult, runsResult, ideasResult, companionResult, alertsResult];
        return {
          data,
          dataAsOf: oldestTimestamp(supporting.map((item) => item.dataAsOf)),
          freshness: combinedFreshness(supporting),
          marketState: runsResult.marketState,
        };
      }
      throw new Error("unsupported dashboard route");
    },
  };
}
