import type { ReactNode } from "react";

import type { ResourceState } from "../api/useDashboardResource";

export function AsyncView<T>({ state, children }: { state: ResourceState<T>; children(data: T): ReactNode }) {
  if (state.status === "loading") {
    return <section className="state-card" aria-live="polite"><p>Loading receipt-backed view…</p></section>;
  }
  if (state.status === "error") {
    return (
      <section className="state-card error-card" role="alert">
        <h1>This view is temporarily unavailable</h1>
        <p>{state.error.message}</p>
      </section>
    );
  }
  return children(state.envelope.data);
}
