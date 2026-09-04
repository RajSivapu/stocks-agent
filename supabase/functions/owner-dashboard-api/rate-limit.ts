export type RateLimitStage = "pre_auth" | "owner";

export interface DashboardRateLimiter {
  check(stage: RateLimitStage, key: string, now: Date): number | null;
}

interface WindowState {
  startedAt: number;
  count: number;
}

export function clientNetworkSignal(request: Request): string {
  const cloudflare = request.headers.get("cf-connecting-ip")?.trim() ?? "";
  if (/^[0-9a-fA-F:.]{3,64}$/.test(cloudflare)) return cloudflare;
  const forwarded = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim() ?? "";
  if (/^[0-9a-fA-F:.]{3,64}$/.test(forwarded)) return forwarded;
  return "unknown";
}

export function createDashboardRateLimiter(
  limits: Readonly<Record<RateLimitStage, number>> = { pre_auth: 30, owner: 120 },
  windowMs = 60_000,
  maxEntries = 4_096,
): DashboardRateLimiter {
  const windows = new Map<string, WindowState>();
  return {
    check(stage, key, now) {
      const instant = now.valueOf();
      if (!Number.isFinite(instant)) return 60;
      for (const [candidate, state] of windows) {
        if (instant - state.startedAt >= windowMs) windows.delete(candidate);
      }
      const mapKey = `${stage}:${key}`;
      const current = windows.get(mapKey);
      if (!current) {
        if (windows.size >= maxEntries) return Math.max(1, Math.ceil(windowMs / 1_000));
        windows.set(mapKey, { startedAt: instant, count: 1 });
        return null;
      }
      current.count += 1;
      if (current.count <= limits[stage]) return null;
      return Math.max(1, Math.ceil((windowMs - (instant - current.startedAt)) / 1_000));
    },
  };
}
