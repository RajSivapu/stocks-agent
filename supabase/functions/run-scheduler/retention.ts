export type RetentionRepository = {
  applyRetention(maintenanceId: string): Promise<Record<string, unknown>>;
};

export type RetentionReceipt = {
  status: "completed";
  pairing_codes: number;
  callback_tokens: number;
  telegram_updates: number;
  pairing_deliveries: number;
  commands_compacted: number;
  evidence_compacted: number;
  submissions_compacted: number;
  tombstones_expired: number;
};

const RECEIPT_KEYS = [
  "status",
  "pairing_codes",
  "callback_tokens",
  "telegram_updates",
  "pairing_deliveries",
  "commands_compacted",
  "evidence_compacted",
  "submissions_compacted",
  "tombstones_expired",
] as const;

function parseReceipt(value: unknown): RetentionReceipt {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("retention receipt is invalid");
  }
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).length !== RECEIPT_KEYS.length ||
    RECEIPT_KEYS.some((key) => !Object.hasOwn(row, key)) ||
    row.status !== "completed"
  ) throw new Error("retention receipt is invalid");
  for (const key of RECEIPT_KEYS.slice(1)) {
    const count = row[key];
    if (!Number.isSafeInteger(count) || Number(count) < 0 || Number(count) > 1_000_000) {
      throw new Error("retention receipt is invalid");
    }
  }
  return structuredClone(row) as RetentionReceipt;
}

export async function runRetentionCycle(
  repository: RetentionRepository,
  newId: () => string = () => crypto.randomUUID(),
): Promise<RetentionReceipt> {
  const maintenanceId = newId();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(maintenanceId)) {
    throw new Error("retention maintenance identifier is invalid");
  }
  return parseReceipt(await repository.applyRetention(maintenanceId));
}
