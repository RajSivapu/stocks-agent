export type RateLimitStage = "pre_auth" | "owner";

export interface DashboardRateLimiter {
  check(stage: RateLimitStage, key: string, now: Date): number | null;
}

interface WindowState {
  startedAt: number;
  count: number;
}

export function clientNetworkSignal(request: Request): string {
  for (const name of ["cf-connecting-ip", "x-forwarded-for"]) {
    const raw = request.headers.get(name)?.split(",", 1)[0]?.trim() ?? "";
    if (/^[0-9a-fA-F:.]{3,64}$/.test(raw)) return raw;
  }
  return "unknown";
}

export function createDashboardRateLimiter(
  limits: Readonly<Record<RateLimitStage, number>> = { pre_auth: 30, owner: 120 },
  windowMs = 60_000,
): DashboardRateLimiter {
  const windows = new Map<string, WindowState>();
  return {
    check(stage, key, now) {
      const instant = now.valueOf();
      if (!Number.isFinite(instant)) return 60;
      const mapKey = `${stage}:${key}`;
      const current = windows.get(mapKey);
      if (!current || instant - current.startedAt >= windowMs) {
        windows.set(mapKey, { startedAt: instant, count: 1 });
        return null;
      }
      current.count += 1;
      if (current.count <= limits[stage]) return null;
      return Math.max(1, Math.ceil((windowMs - (instant - current.startedAt)) / 1_000));
    },
  };
}
