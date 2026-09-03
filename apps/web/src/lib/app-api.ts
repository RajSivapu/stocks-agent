import type { SupabaseClient } from "@supabase/supabase-js";
import {
  parseCommandPreviewReceipt,
  type CommandInput,
  type CommandPreviewReceipt,
} from "@stocks-agent/contracts";

const MAX_RESPONSE_BYTES = 64 * 1024;

export type CommandReceipt = {
  id: string;
  operation: CommandInput["operation"];
  status: "submitted" | "previewed" | "confirmed" | "applied" |
    "cancelled" | "expired" | "error";
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  warnings: string[];
  result: Record<string, unknown> | null;
  errorCode: string | null;
  expiresAt: string;
  confirmedAt: string | null;
  appliedAt: string | null;
  createdAt: string;
};

export type AppliedCommand = {
  command_id: string;
  status: "applied";
  result: Record<string, unknown>;
  duplicate?: boolean;
};

export interface CommandClient {
  preview(command: CommandInput): Promise<CommandPreviewReceipt>;
  confirm(
    commandId: string,
    previewDigest: string,
    operation: CommandInput["operation"],
  ): Promise<AppliedCommand>;
  lookup(commandId: string): Promise<CommandReceipt | null>;
}

export type RunRequestReceipt = {
  status: "queued";
  slotId: string;
  phase: "on-demand";
  marketDate: string;
  expectedBy: string;
  telegram: "suppressed";
};

export interface RunControlClient {
  requestRun(): Promise<RunRequestReceipt>;
}

export type ConnectionCreateReceipt = {
  connectionId: string;
  publicId: string;
  provider: "claude";
  status: "disabled";
  contractVersion: 2;
  gatewayCredential: string;
  gatewayUrl: string;
  credentialDisplay: "once";
};

export type ConnectionHandshakeReceipt = {
  connectionId: string;
  status: "testing";
  handshakeId: string;
  triggerRequestId: string;
  duplicate: boolean;
};

export type ConnectionStatusReceipt = {
  connectionId: string;
  status: "active" | "revoked";
};

export type PairingCodeReceipt = {
  pairingId: string;
  status: "issued";
  code: string;
  expiresAt: string;
};

export type SettingsUpdate = {
  display_name: string;
  timezone: string;
  notify_pre_market: boolean;
  notify_intraday: boolean;
  notify_post_market: boolean;
  notify_operational: boolean;
  schedule_pre_market: boolean;
  schedule_intraday: boolean;
  schedule_post_market: boolean;
};

export interface ConnectionClient {
  createConnection: (consentVersion: string) => Promise<ConnectionCreateReceipt>;
  beginConnectionHandshake: (connectionId: string, triggerUrl: string, triggerToken: string) => Promise<ConnectionHandshakeReceipt>;
  activateConnection: (connectionId: string) => Promise<ConnectionStatusReceipt>;
  revokeConnection: (connectionId: string) => Promise<ConnectionStatusReceipt>;
  requestPairingCode: () => Promise<PairingCodeReceipt>;
  unlinkTelegram: () => Promise<{ status: "unlinked" }>;
}

export interface SettingsClient {
  updateSettings: (settings: SettingsUpdate) => Promise<{ status: "updated" }>;
}

export type AppApiClient = CommandClient & RunControlClient & ConnectionClient & SettingsClient;

export class AppApiError extends Error {
  constructor(readonly code: string) {
    super("Stock Agent request was not completed");
    this.name = "AppApiError";
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  return value as Record<string, unknown>;
}

function commandPath(operation: CommandInput["operation"]): string {
  if (operation === "correct_transaction") return "/portfolio/correction/preview";
  if (operation === "plan" || operation === "cancel_plan") return "/plans/preview";
  return "/portfolio/preview";
}

function confirmationPath(operation: CommandInput["operation"]): string {
  if (operation === "correct_transaction") return "/portfolio/correction/confirm";
  if (operation === "plan" || operation === "cancel_plan") return "/plans/confirm";
  return "/portfolio/confirm";
}

async function accessToken(client: SupabaseClient): Promise<string> {
  const session = await client.auth.getSession();
  const token = session.data.session?.access_token;
  if (session.error || !token || token.length > 16_384) throw new AppApiError("SESSION_UNAVAILABLE");
  return token;
}

async function readEnvelope(response: Response): Promise<Record<string, unknown>> {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_RESPONSE_BYTES) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    throw new AppApiError("INVALID_RESPONSE");
  }
  const envelope = record(value);
  if (!response.ok || envelope.ok !== true) {
    const error = envelope.error;
    const code = error && typeof error === "object" && !Array.isArray(error) &&
        typeof (error as Record<string, unknown>).code === "string"
      ? (error as Record<string, unknown>).code as string
      : "REQUEST_FAILED";
    throw new AppApiError(code);
  }
  return record(envelope.data);
}

function parseApplied(value: unknown): AppliedCommand {
  const row = record(value);
  if (
    typeof row.command_id !== "string" || row.status !== "applied" ||
    !row.result || typeof row.result !== "object" || Array.isArray(row.result) ||
    (row.duplicate !== undefined && typeof row.duplicate !== "boolean")
  ) throw new AppApiError("INVALID_RESPONSE");
  return {
    command_id: row.command_id,
    status: "applied",
    result: row.result as Record<string, unknown>,
    ...(typeof row.duplicate === "boolean" ? { duplicate: row.duplicate } : {}),
  };
}

function parseRunReceipt(value: unknown): RunRequestReceipt {
  const row = record(value);
  if (
    row.status !== "queued" || row.phase !== "on-demand" || row.telegram !== "suppressed" ||
    typeof row.slot_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(row.slot_id) ||
    typeof row.market_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(row.market_date) ||
    typeof row.expected_by !== "string" || !Number.isFinite(Date.parse(row.expected_by))
  ) throw new AppApiError("INVALID_RESPONSE");
  return {
    status: "queued", slotId: row.slot_id, phase: "on-demand",
    marketDate: row.market_date, expectedBy: row.expected_by, telegram: "suppressed",
  };
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_RE.test(value)) throw new AppApiError("INVALID_RESPONSE");
  return value;
}

function isoDateTime(value: unknown): string {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  return value;
}

function parseConnectionCreate(value: unknown, gatewayUrl: string): ConnectionCreateReceipt {
  const row = record(value);
  const connectionId = uuid(row.connection_id);
  const publicId = uuid(row.public_id);
  if (row.provider !== "claude" || row.status !== "disabled" || row.contract_version !== 2 ||
      row.credential_display !== "once" || typeof row.gateway_credential !== "string" ||
      row.gateway_credential !== `${publicId}.${row.gateway_credential.split(".").at(-1) ?? ""}` ||
      !new RegExp(`^${publicId.replaceAll("-", "\\-")}\\.[A-Za-z0-9_-]{43}$`, "i").test(row.gateway_credential)) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  return {
    connectionId, publicId, provider: "claude", status: "disabled", contractVersion: 2,
    gatewayCredential: row.gateway_credential, gatewayUrl, credentialDisplay: "once",
  };
}

function parseHandshake(value: unknown): ConnectionHandshakeReceipt {
  const row = record(value);
  if (row.status !== "testing" || typeof row.duplicate !== "boolean") {
    throw new AppApiError("INVALID_RESPONSE");
  }
  return {
    connectionId: uuid(row.connection_id), status: "testing",
    handshakeId: uuid(row.handshake_id), triggerRequestId: uuid(row.trigger_request_id),
    duplicate: row.duplicate,
  };
}

function parseConnectionStatus(value: unknown, expected: "active" | "revoked"): ConnectionStatusReceipt {
  const row = record(value);
  if (row.status !== expected) throw new AppApiError("INVALID_RESPONSE");
  return { connectionId: uuid(row.connection_id), status: expected };
}

function parsePairingCode(value: unknown): PairingCodeReceipt {
  const row = record(value);
  if (row.status !== "issued" || typeof row.code !== "string" || !/^[A-HJ-NP-Z2-9]{10}$/.test(row.code)) {
    throw new AppApiError("INVALID_RESPONSE");
  }
  return {
    pairingId: uuid(row.pairing_id), status: "issued", code: row.code,
    expiresAt: isoDateTime(row.expires_at),
  };
}

export function createCommandClient(
  client: SupabaseClient,
  projectUrl: string,
  lookup: (commandId: string) => Promise<CommandReceipt | null>,
  fetcher: typeof fetch = fetch,
): AppApiClient {
  const parsed = new URL(projectUrl);
  if (parsed.protocol !== "https:" || !/^[a-z0-9-]+\.supabase\.co$/.test(parsed.hostname)) {
    throw new Error("PUBLIC_CONFIG_UNAVAILABLE");
  }
  const endpoint = `${parsed.origin}/functions/v1/app-api`;
  const send = async (method: "PATCH" | "POST", path: string, body: Record<string, unknown>) => {
    const response = await fetcher(`${endpoint}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${await accessToken(client)}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(10_000),
    });
    return readEnvelope(response);
  };
  const post = (path: string, body: Record<string, unknown>) => send("POST", path, body);
  return {
    async preview(command) {
      const value = await post(commandPath(command.operation), {
        idempotency_key: crypto.randomUUID(),
        command,
      });
      try {
        return parseCommandPreviewReceipt(value);
      } catch {
        throw new AppApiError("INVALID_RESPONSE");
      }
    },
    async confirm(commandId, previewDigest, operation) {
      return parseApplied(await post(confirmationPath(operation), {
        command_id: commandId,
        preview_digest: previewDigest,
      }));
    },
    async requestRun() {
      return parseRunReceipt(await post("/runs/on-demand", {}));
    },
    async createConnection(consentVersion) {
      return parseConnectionCreate(await post("/connections/create", {
        provider: "claude", consent_version: consentVersion,
      }), `${parsed.origin}/functions/v1/agent-gateway`);
    },
    async beginConnectionHandshake(connectionId, triggerUrl, triggerToken) {
      return parseHandshake(await post("/connections/handshake", {
        connection_id: connectionId, trigger_url: triggerUrl, trigger_token: triggerToken,
      }));
    },
    async activateConnection(connectionId) {
      return parseConnectionStatus(await post("/connections/activate", {
        connection_id: connectionId,
      }), "active");
    },
    async revokeConnection(connectionId) {
      return parseConnectionStatus(await post("/connections/revoke", {
        connection_id: connectionId,
      }), "revoked");
    },
    async requestPairingCode() {
      return parsePairingCode(await post("/telegram/pairing-code", {}));
    },
    async unlinkTelegram() {
      const value = await post("/telegram/unlink", {});
      if (value.status !== "unlinked") throw new AppApiError("INVALID_RESPONSE");
      return { status: "unlinked" };
    },
    async updateSettings(settings) {
      const value = await send("PATCH", "/settings", settings);
      if (value.status !== "updated") throw new AppApiError("INVALID_RESPONSE");
      return { status: "updated" };
    },
    lookup,
  };
}
