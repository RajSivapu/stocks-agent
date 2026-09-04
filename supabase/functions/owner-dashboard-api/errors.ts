export type DashboardErrorCode =
  | "unauthorized"
  | "owner_only"
  | "not_found"
  | "rate_limited"
  | "temporarily_unavailable"
  | "invalid_request";

export class DashboardHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: DashboardErrorCode,
    readonly publicMessage: string,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(publicMessage);
    this.name = "DashboardHttpError";
  }
}

export function errorEnvelope(requestId: string, error: DashboardHttpError) {
  return {
    contract_version: 1 as const,
    request_id: requestId,
    error: {
      code: error.code,
      message: error.publicMessage.slice(0, 160),
    },
  };
}
