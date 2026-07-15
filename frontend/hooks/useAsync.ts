"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/types/api";
import { toApiError } from "@/lib/api-client";

type AsyncState<T> = {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
};

/**
 * Load once on mount, with loading and error states the pages can render
 * honestly. `deps` re-runs the load; results from a superseded run are
 * discarded so a slow response cannot overwrite a newer one.
 */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const run = useRef(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const ticket = ++run.current;
    setLoading(true);
    setError(null);

    load()
      .then((next) => {
        if (run.current === ticket) setData(next);
      })
      .catch((err: unknown) => {
        if (run.current === ticket) setError(toApiError(err));
      })
      .finally(() => {
        if (run.current === ticket) setLoading(false);
      });
    // `load` is deliberately not a dependency: callers pass an inline
    // `useCallback` and re-running on its identity would refetch every render.
    // The caller-supplied `deps` are the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload };
}
