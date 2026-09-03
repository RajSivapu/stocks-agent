export interface ListedObject {
  key: string;
  size: number;
  uploaded: Date;
}

export interface MonitorLimits {
  maxAgeHours: number;
  storageAlertBytes: number;
  plannedMonthlyClassAOps: number;
  plannedMonthlyClassBOps: number;
  classAAlertOps: number;
  classBAlertOps: number;
}

export interface MonitorState {
  stale: boolean;
  capacityWarning: boolean;
  newestAgeHours: number | null;
  objectCount: number;
  totalBytes: number;
}

interface R2ObjectsPage {
  objects: ListedObject[];
  truncated: boolean;
  cursor?: string;
}

interface R2BucketLike {
  list(options: { prefix: string; limit: number; cursor?: string }): Promise<R2ObjectsPage>;
}

interface Env {
  BACKUP_BUCKET: R2BucketLike;
  BACKUP_PREFIX: string;
  MAX_AGE_HOURS: string;
  STORAGE_ALERT_BYTES: string;
  PLANNED_MONTHLY_CLASS_A_OPS: string;
  PLANNED_MONTHLY_CLASS_B_OPS: string;
  CLASS_A_ALERT_OPS: string;
  CLASS_B_ALERT_OPS: string;
  TELEGRAM_BOT_TOKEN: string;
  OPERATIONAL_TELEGRAM_CHAT_ID: string;
}

const STALE_MESSAGE =
  "BACKUP STALE: the newest encrypted stock-agent backup is older than 36 hours. Check the private backup workflow and R2.";
const CAPACITY_MESSAGE =
  "BACKUP CAPACITY WARNING: configured stock-agent R2 storage or operation usage is approaching its monthly allowance.";

function positiveInteger(value: string, label: string): number {
  if (!/^[1-9][0-9]{0,15}$/.test(value)) throw new Error(`${label} is invalid`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label} is invalid`);
  return parsed;
}

export function evaluateBackupState(
  objects: ListedObject[],
  nowMs: number,
  limits: MonitorLimits,
): MonitorState {
  const newestMs = objects.reduce(
    (latest, object) => Math.max(latest, object.uploaded.getTime()),
    Number.NEGATIVE_INFINITY,
  );
  const newestAgeHours = Number.isFinite(newestMs)
    ? Math.max(0, (nowMs - newestMs) / 3_600_000)
    : null;
  const totalBytes = objects.reduce((total, object) => total + object.size, 0);
  return {
    stale: newestAgeHours === null || newestAgeHours > limits.maxAgeHours,
    capacityWarning:
      totalBytes >= limits.storageAlertBytes ||
      limits.plannedMonthlyClassAOps >= limits.classAAlertOps ||
      limits.plannedMonthlyClassBOps >= limits.classBAlertOps,
    newestAgeHours,
    objectCount: objects.length,
    totalBytes,
  };
}

async function listAll(bucket: R2BucketLike, prefix: string): Promise<ListedObject[]> {
  if (!/^stock-agent\/[a-z0-9/_-]+\/$/.test(prefix)) throw new Error("backup prefix is invalid");
  const objects: ListedObject[] = [];
  let cursor: string | undefined;
  do {
    const page = await bucket.list({ prefix, limit: 1000, ...(cursor ? { cursor } : {}) });
    for (const object of page.objects) {
      if (!object.key.startsWith(prefix) || !object.key.endsWith(".age")) {
        throw new Error("backup listing escaped its prefix contract");
      }
      objects.push(object);
    }
    if (page.truncated && !page.cursor) throw new Error("truncated R2 listing has no cursor");
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return objects;
}

async function sendFixedAlert(env: Env, text: string): Promise<void> {
  if (text !== STALE_MESSAGE && text !== CAPACITY_MESSAGE) throw new Error("alert text is not fixed");
  if (!/^\d{6,12}:[A-Za-z0-9_-]{20,}$/.test(env.TELEGRAM_BOT_TOKEN)) {
    throw new Error("Telegram operational credential is invalid");
  }
  if (!/^-?[1-9][0-9]{0,15}$/.test(env.OPERATIONAL_TELEGRAM_CHAT_ID)) {
    throw new Error("Telegram operational destination is invalid");
  }
  const response = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: env.OPERATIONAL_TELEGRAM_CHAT_ID,
        text,
        disable_web_page_preview: true,
      }),
    },
  );
  if (!response.ok) throw new Error("Telegram operational alert failed");
}

export async function runMonitor(env: Env, nowMs = Date.now()): Promise<MonitorState> {
  const limits: MonitorLimits = {
    maxAgeHours: positiveInteger(env.MAX_AGE_HOURS, "maximum age"),
    storageAlertBytes: positiveInteger(env.STORAGE_ALERT_BYTES, "storage threshold"),
    plannedMonthlyClassAOps: positiveInteger(env.PLANNED_MONTHLY_CLASS_A_OPS, "planned Class A operations"),
    plannedMonthlyClassBOps: positiveInteger(env.PLANNED_MONTHLY_CLASS_B_OPS, "planned Class B operations"),
    classAAlertOps: positiveInteger(env.CLASS_A_ALERT_OPS, "Class A threshold"),
    classBAlertOps: positiveInteger(env.CLASS_B_ALERT_OPS, "Class B threshold"),
  };
  const objects = await listAll(env.BACKUP_BUCKET, env.BACKUP_PREFIX);
  const state = evaluateBackupState(objects, nowMs, limits);
  if (state.stale) await sendFixedAlert(env, STALE_MESSAGE);
  if (state.capacityWarning) await sendFixedAlert(env, CAPACITY_MESSAGE);
  return state;
}

export default {
  async scheduled(_controller: unknown, env: Env): Promise<void> {
    await runMonitor(env);
  },
};
