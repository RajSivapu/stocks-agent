import { useEffect, useState } from "react";

import type { DashboardEnvelope } from "@stocks-agent/dashboard-contracts";

import type { DashboardClient } from "./client";

export type ResourceState<T> =
  | { status: "loading"; envelope: null; error: null }
  | { status: "ready"; envelope: DashboardEnvelope<T>; error: null }
  | { status: "error"; envelope: null; error: Error };

export function useDashboardResource<T>(
  client: DashboardClient,
  path: string,
  token: string,
  onError?: (error: Error) => void,
): ResourceState<T> {
  const [state, setState] = useState<ResourceState<T>>({ status: "loading", envelope: null, error: null });
  useEffect(() => {
    let active = true;
    setState({ status: "loading", envelope: null, error: null });
    void client.get<T>(path, token).then((envelope) => {
      if (active) setState({ status: "ready", envelope, error: null });
    }).catch((error: unknown) => {
      if (active) {
        const bounded = error instanceof Error ? error : new Error("Dashboard unavailable.");
        setState({ status: "error", envelope: null, error: bounded });
        onError?.(bounded);
      }
    });
    return () => {
      active = false;
      setState({ status: "loading", envelope: null, error: null });
    };
  }, [client, onError, path, token]);
  return state;
}
