import { readBoundedJson } from "../_shared/bounded-json.ts";
import { jsonResponse } from "../_shared/errors.ts";
import { fireClaudeRoutine, type FireReceipt } from "./claude-fire.ts";
import { runMaintenanceCycle } from "./maintenance.ts";
import { sendTelegramParts } from "../market-briefing-gateway/_shared/telegram.ts";
import {
  type AdjustedBar,
  fetchAdjustedHistory,
} from "../market-briefing-gateway/_shared/market-data.ts";
import type { SchedulerRepository } from "./repository.ts";

type Fire = (
  endpoint: string,
  token: string,
  requestId: string,
) => Promise<FireReceipt>;
export type SchedulerDependencies = {
  repository: SchedulerRepository;
  schedulerSecret: string;
  now?: () => Date;
  fire?: Fire;
  telegramToken: string;
  sendTelegram?: typeof sendTelegramParts;
  fetchHistory?: (ticker: string) => Promise<AdjustedBar[]>;
};

function error(status: number, code: string): Response {
  return jsonResponse(status, { ok: false, error: { code } });
}

async function secureEqual(left: string, right: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const aa = new Uint8Array(a);
  const bb = new Uint8Array(b);
  let difference = left.length ^ right.length;
  for (let index = 0; index < aa.length; index += 1) {
    difference |= aa[index] ^ bb[index];
  }
  return difference === 0;
}

function limitFrom(value: unknown): number {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid");
  }
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).length !== 1 || !Object.hasOwn(row, "limit") ||
    !Number.isInteger(row.limit) || Number(row.limit) < 1 ||
    Number(row.limit) > 20
  ) throw new Error("invalid");
  return Number(row.limit);
}

export function createRunSchedulerHandler(dependencies: SchedulerDependencies) {
  const now = dependencies.now ?? (() => new Date());
  const fire = dependencies.fire ?? fireClaudeRoutine;
  const sendTelegram = dependencies.sendTelegram ?? sendTelegramParts;
  const fetchHistory = dependencies.fetchHistory ??
    ((ticker: string) => fetchAdjustedHistory(ticker, "1y"));
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") return error(405, "METHOD_NOT_ALLOWED");
    const authorization = request.headers.get("authorization") ?? "";
    if (
      !(await secureEqual(
        authorization,
        `Bearer ${dependencies.schedulerSecret}`,
      ))
    ) {
      return error(401, "UNAUTHORIZED");
    }
    let limit: number;
    try {
      limit = limitFrom(await readBoundedJson(request, 1024));
    } catch {
      return error(400, "INVALID_REQUEST");
    }
    try {
      const observedAt = now();
      const maintenance = await runMaintenanceCycle(
        dependencies.repository,
        observedAt,
        limit,
        dependencies.telegramToken,
        sendTelegram,
        fetchHistory,
      );
      const slots = await dependencies.repository.claimDueSlots(
        observedAt.toISOString(),
        limit,
      );
      const counts = {
        triggered: 0,
        trigger_failed: 0,
        trigger_unknown: 0,
        holiday: 0,
      };
      for (const slot of slots) {
        if (slot.holiday) {
          await dependencies.repository.publishHoliday(slot.slot_id);
          counts.holiday += 1;
          continue;
        }
        if (!slot.attempt_id) throw new Error("missing trigger attempt");
        const secret = await dependencies.repository.readTriggerSecret(
          slot.attempt_id,
        );
        if (secret.trigger_request_id !== slot.trigger_request_id) {
          throw new Error("trigger identity mismatch");
        }
        const receipt = await fire(
          secret.endpoint,
          secret.token,
          slot.trigger_request_id,
        );
        await dependencies.repository.recordTriggerResult(
          slot.attempt_id,
          receipt,
        );
        counts[receipt.status] += 1;
      }
      return jsonResponse(200, {
        ok: true,
        claimed: slots.length,
        counts,
        maintenance,
      });
    } catch {
      return error(500, "SCHEDULER_UNAVAILABLE");
    }
  };
}
