const PUBLIC_KEYS = ["status", "schema_version"] as const;
const OPERATOR_KEYS = [
  "status",
  "component_status",
  "deployed_versions",
  "provider_adapter",
  "missed_runs",
  "quota_pressure",
  "backup",
  "restore",
  "projection",
] as const;

const FORBIDDEN_KEY = /(?:^|_)(?:email|telegram|chat|user|owner|ticker|symbol|position|shares?|quantity|cost_basis|recommendation|rendered|message|prompt|token|secret|authorization|credential)(?:$|_)/i;

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  label: string,
): void {
  const observed = Object.keys(value);
  if (observed.length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new Error(`${label} has unexpected fields`);
  }
}

function rejectPrivateKeys(value: unknown): void {
  if (Array.isArray(value)) {
    if (value.length > 0) throw new Error("operator health cannot contain row lists");
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (FORBIDDEN_KEY.test(key)) throw new Error("operator health contains a private field");
    rejectPrivateKeys(child);
  }
}

function status(
  value: unknown,
  allowed: readonly string[],
  label: string,
): string {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function count(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > 1_000_000) {
    throw new Error(`${label} is invalid`);
  }
  return Number(value);
}

function version(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) {
    throw new Error(`${label} is invalid`);
  }
  return Number(value);
}

function nullableTimestamp(value: unknown, label: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || value.length > 40 || !Number.isFinite(Date.parse(value))) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

export type PublicHealth = { status: "ok"; schema_version: 1 };

export function parsePublicHealth(value: unknown): PublicHealth {
  const row = record(value, "public health");
  exactKeys(row, PUBLIC_KEYS, "public health");
  if (row.status !== "ok" || row.schema_version !== 1) {
    throw new Error("public health is invalid");
  }
  return { status: "ok", schema_version: 1 };
}

export function parseOperatorHealth(value: unknown): Record<string, unknown> {
  rejectPrivateKeys(value);
  const row = record(value, "operator health");
  exactKeys(row, OPERATOR_KEYS, "operator health");

  const components = record(row.component_status, "component status");
  exactKeys(
    components,
    ["database", "scheduler", "provider_adapter", "backup", "restore", "projections"],
    "component status",
  );
  status(components.database, ["ok", "degraded"], "database status");
  status(components.scheduler, ["ok", "degraded"], "scheduler status");
  status(components.provider_adapter, ["ok", "degraded"], "provider status");
  status(components.backup, ["ok", "stale", "missing"], "backup status");
  status(components.restore, ["ok", "stale", "missing"], "restore status");
  status(components.projections, ["ok", "degraded"], "projection status");

  const versions = record(row.deployed_versions, "deployed versions");
  exactKeys(versions, ["database_schema", "provider_contract"], "deployed versions");
  version(versions.database_schema, "database schema version");
  version(versions.provider_contract, "provider contract version");

  const provider = record(row.provider_adapter, "provider adapter");
  exactKeys(provider, ["provider", "active", "unavailable"], "provider adapter");
  if (provider.provider !== "claude") throw new Error("provider adapter is invalid");
  count(provider.active, "active provider count");
  count(provider.unavailable, "unavailable provider count");

  const missed = record(row.missed_runs, "missed runs");
  exactKeys(missed, ["last_24_hours", "last_7_days"], "missed runs");
  count(missed.last_24_hours, "24-hour missed runs");
  count(missed.last_7_days, "7-day missed runs");

  const quota = record(row.quota_pressure, "quota pressure");
  exactKeys(quota, ["month_invocations", "configured_limit"], "quota pressure");
  count(quota.month_invocations, "monthly invocation count");
  count(quota.configured_limit, "configured invocation limit");

  const backup = record(row.backup, "backup health");
  exactKeys(backup, ["age_hours", "last_success_at"], "backup health");
  if (backup.age_hours !== null) count(backup.age_hours, "backup age");
  nullableTimestamp(backup.last_success_at, "backup timestamp");

  const restore = record(row.restore, "restore health");
  exactKeys(restore, ["age_days", "last_verified_at"], "restore health");
  if (restore.age_days !== null) count(restore.age_days, "restore age");
  nullableTimestamp(restore.last_verified_at, "restore timestamp");

  const projection = record(row.projection, "projection health");
  exactKeys(projection, ["checked", "failed", "paused"], "projection health");
  count(projection.checked, "checked projection count");
  count(projection.failed, "failed projection count");
  count(projection.paused, "paused projection count");
  status(row.status, ["ok", "degraded"], "operator health status");
  return structuredClone(row);
}
