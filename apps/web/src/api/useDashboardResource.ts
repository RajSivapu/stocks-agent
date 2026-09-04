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
): ResourceState<T> {
  const [state, setState] = useState<ResourceState<T>>({ status: "loading", envelope: null, error: null });
  useEffect(() => {
    let active = true;
    setState({ status: "loading", envelope: null, error: null });
    void client.get<T>(path, token).then((envelope) => {
      if (active) setState({ status: "ready", envelope, error: null });
    }).catch((error: unknown) => {
      if (active) setState({ status: "error", envelope: null, error: error instanceof Error ? error : new Error("Dashboard unavailable.") });
    });
    return () => {
      active = false;
      setState({ status: "loading", envelope: null, error: null });
    };
  }, [client, path, token]);
  return state;
}
