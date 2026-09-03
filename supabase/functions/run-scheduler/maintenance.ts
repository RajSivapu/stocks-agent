import {
  sendTelegramParts,
  TelegramDeliveryError,
} from "../market-briefing-gateway/_shared/telegram.ts";
import type {
  ClaimedOperationalAlert,
  ClaimedPublication,
  DeliveryStatus,
  SchedulerRepository,
} from "./repository.ts";
import {
  type DueDecision,
  gradeDecision,
} from "../market-briefing-gateway/_shared/outcomes.ts";
import type { AdjustedBar } from "../market-briefing-gateway/_shared/market-data.ts";

type Sender = typeof sendTelegramParts;
type HistoryFetcher = (ticker: string) => Promise<AdjustedBar[]>;

export const OPERATIONAL_COPY: Readonly<Record<string, string>> = Object.freeze(
  {
    EXPECTED_RUN_MISSED:
      "⚙️ Stock Agent missed an expected analysis window. Review Connections and Runs before relying on today’s output.",
    PROVIDER_DISCONNECTED:
      "⚙️ Stock Agent’s analysis provider is disconnected. Reconnect it in Settings to resume scheduled research.",
    LEDGER_PROJECTION_MISMATCH:
      "⚙️ Stock Agent detected a portfolio record integrity issue. Record changes are paused; contact the operator.",
    RUN_PARTIAL:
      "⚙️ Stock Agent could not complete an analysis run. Review Runs before relying on its output.",
    BACKUP_STALE:
      "⚙️ Stock Agent’s recovery backup is out of date. New invitations and production changes are paused.",
  },
);

export type MaintenanceCycleResult = {
  maintenance: Record<string, unknown>;
  publications: Record<DeliveryStatus, number>;
  operational_alerts: Record<DeliveryStatus, number>;
  outcome_grades: Record<string, unknown>;
};

function uuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
      .test(value);
}

function chat(value: unknown): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,15}$/.test(value);
}

function validPublication(value: ClaimedPublication): void {
  if (
    !uuid(value.publication_id) || !uuid(value.lease_token) ||
    !chat(value.chat_id) ||
    !Array.isArray(value.parts) || value.parts.length < 1 ||
    value.parts.length > 4 ||
    value.parts.some((part) =>
      typeof part !== "string" || part.length < 1 || part.length > 3500
    )
  ) {
    throw new Error("invalid publication claim");
  }
}

function validOperationalAlert(value: ClaimedOperationalAlert): string {
  if (
    !uuid(value.alert_id) || !uuid(value.lease_token) || !chat(value.chat_id) ||
    typeof value.code !== "string" ||
    !Object.hasOwn(OPERATIONAL_COPY, value.code)
  ) {
    throw new Error("invalid operational alert claim");
  }
  return OPERATIONAL_COPY[value.code];
}

async function deliver(
  parts: string[],
  chatId: string,
  telegramToken: string,
  sender: Sender,
): Promise<{ status: DeliveryStatus; messageIds: number[] }> {
  try {
    return {
      status: "delivered",
      messageIds: await sender(parts, chatId, telegramToken),
    };
  } catch (error) {
    const delivery = error instanceof TelegramDeliveryError
      ? error
      : new TelegramDeliveryError("ambiguous", []);
    return {
      status: delivery.kind === "definitive"
        ? "delivery_failed"
        : "delivery_unknown",
      messageIds: delivery.partialMessageIds,
    };
  }
}

function emptyCounts(): Record<DeliveryStatus, number> {
  return { delivered: 0, delivery_failed: 0, delivery_unknown: 0 };
}

function validatedDueDecision(
  value: unknown,
): DueDecision & { owner_id: string } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid due decision");
  }
  const row = value as Record<string, unknown>;
  const keys = [
    "owner_id",
    "suggestion_id",
    "decision_date",
    "ticker",
    "bucket",
    "final_action",
    "confidence",
    "policy_version",
    "decision_price",
    "entry_zone_low",
    "entry_zone_high",
    "stop",
    "target",
    "invalidation_price",
    "completed_horizons",
  ];
  const decimal = (candidate: unknown, nullable = false) =>
    (nullable && candidate === null) ||
    (typeof candidate === "string" &&
      /^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(candidate) && candidate.length <= 40);
  if (
    Object.keys(row).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(row, key)) ||
    !uuid(row.owner_id) || !Number.isSafeInteger(row.suggestion_id) ||
    Number(row.suggestion_id) <= 0 ||
    typeof row.decision_date !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(row.decision_date) ||
    typeof row.ticker !== "string" ||
    !/^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/.test(row.ticker) ||
    row.ticker.length > 15 ||
    !["core", "growth", "speculative"].includes(String(row.bucket)) ||
    !["buy", "add", "hold", "reduce", "sell", "watch", "avoid"].includes(
      String(row.final_action),
    ) ||
    !["low", "medium", "high"].includes(String(row.confidence)) ||
    !Number.isSafeInteger(row.policy_version) ||
    Number(row.policy_version) < 1 ||
    !decimal(row.decision_price) || !decimal(row.entry_zone_low, true) ||
    !decimal(row.entry_zone_high, true) || !decimal(row.stop, true) ||
    !decimal(row.target, true) || !decimal(row.invalidation_price, true) ||
    !Array.isArray(row.completed_horizons) ||
    row.completed_horizons.some((item) => ![5, 21, 63].includes(Number(item)))
  ) {
    throw new Error("invalid due decision");
  }
  return structuredClone(row) as unknown as DueDecision & { owner_id: string };
}

export async function runMaintenanceCycle(
  repository: SchedulerRepository,
  now: Date,
  limit: number,
  telegramToken: string,
  sender: Sender = sendTelegramParts,
  fetchHistory: HistoryFetcher = () =>
    Promise.reject(new Error("history fetcher unavailable")),
): Promise<MaintenanceCycleResult> {
  const instant = now.toISOString();
  const maintenance = await repository.runMaintenance(instant);
  const publicationCounts = emptyCounts();
  const operationalCounts = emptyCounts();
  const due = (await repository.readDueDecisions(instant, limit)).map(
    validatedDueDecision,
  );
  const tickers = [
    ...new Set(due.flatMap((decision) => [
      decision.ticker,
      decision.ticker === "VXUS" ? "VXUS" : "VOO",
    ])),
  ];
  const histories = new Map(
    await Promise.all(tickers.map(async (ticker) =>
      [
        ticker,
        await fetchHistory(ticker).catch(() => []),
      ] as const
    )),
  );
  const grades = due.flatMap((decision) => {
    const { owner_id, ...input } = decision;
    const benchmark = input.ticker === "VXUS" ? "VXUS" : "VOO";
    return ([5, 21, 63] as const)
      .filter((horizon) => !input.completed_horizons.includes(horizon))
      .map((horizon) => ({
        owner_id,
        ...gradeDecision(
          input,
          histories.get(input.ticker) ?? [],
          histories.get(benchmark) ?? [],
          horizon,
        ),
      }));
  });
  const outcomeGrades = grades.length > 0
    ? await repository.applyOutcomeGrades(grades)
    : { inserted: 0, updated: 0, incomplete: 0 };

  for (
    const publication of await repository.claimPublications(instant, limit)
  ) {
    validPublication(publication);
    const result = await deliver(
      publication.parts,
      publication.chat_id,
      telegramToken,
      sender,
    );
    await repository.finishPublication(
      publication.publication_id,
      publication.lease_token,
      result.status,
      result.messageIds,
    );
    publicationCounts[result.status] += 1;
  }

  for (const alert of await repository.claimOperationalAlerts(instant, limit)) {
    const copy = validOperationalAlert(alert);
    const result = await deliver([copy], alert.chat_id, telegramToken, sender);
    await repository.finishOperationalAlert(
      alert.alert_id,
      alert.lease_token,
      result.status,
      result.messageIds,
    );
    operationalCounts[result.status] += 1;
  }
  return {
    maintenance,
    publications: publicationCounts,
    operational_alerts: operationalCounts,
    outcome_grades: outcomeGrades,
  };
}
