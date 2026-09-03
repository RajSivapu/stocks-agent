import { useCallback, useEffect, useState } from "react";

export type LoadState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error" };

export function useRepositoryData<T>(loader: () => Promise<T>) {
  const [state, setState] = useState<LoadState<T>>({ kind: "loading" });
  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: await loader() });
    } catch {
      setState({ kind: "error" });
    }
  }, [loader]);
  useEffect(() => { void load(); }, [load]);
  return { state, reload: load };
}
