import type { CommandOperation } from "./portfolio.ts";

export type CommandStatus = "submitted" | "previewed" | "confirmed" | "applied" |
  "cancelled" | "expired" | "rejected" | "error";

export interface CommandPreview {
  command_id: string;
  preview_digest: string;
  expires_at: string;
  operation: CommandOperation;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  warnings: string[];
}

export type ApiSuccess<T> = { ok: true; data: T };
export type ApiFailure = { ok: false; error: { code: string } };
export type ApiResponse<T> = ApiSuccess<T> | ApiFailure;

export interface MachineCredential {
  public_id: string;
  secret_digest: string;
}
