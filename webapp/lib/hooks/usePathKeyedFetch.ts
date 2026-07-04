"use client";
// Stamp-disciplined fetch keyed on a viewed CYCLE PATH. The lazy per-round file,
// the leaf cycle index, and any other pane that follows the drilled-in leaf all
// share the same hazard: the path array is a fresh identity every render while
// following, and a drill-in must never flash the PRIOR cycle's payload under the
// new header. This hook owns that discipline once —
//   • the fetch effect keys on the stable `key` STRING alone (not the array), so
//     it re-runs only when the viewed cycle actually changes;
//   • the current path + fetcher ride refs synced in an effect (never during
//     render), so the keyed effect reads the latest without depending on their
//     per-render identity;
//   • the loaded value is stamped with the key it was fetched for and returned
//     only once the stamp matches — until then `empty`, so a unit switch can
//     never surface the last fetch's data.
// `key` null (e.g. depth 1, where the leaf IS the root and the caller already
// holds the value) short-circuits to `empty` with no fetch. `empty` MUST be a
// stable reference (module const / null) — it is read inside the keyed effect.

import { useEffect, useRef, useState } from "react";
import type { CyclePath } from "../ids";

export interface PathKeyed<T> {
  value: T;
  loading: boolean;
  error: string | null;
}

interface Loaded<T> {
  key: string | null;
  value: T;
}

export function usePathKeyedFetch<T>(
  key: string | null,
  path: CyclePath | null,
  empty: T,
  fetcher: (path: CyclePath, signal: AbortSignal) => Promise<T>,
): PathKeyed<T> {
  const [loaded, setLoaded] = useState<Loaded<T>>({ key: null, value: empty });
  const [error, setError] = useState<string | null>(null);
  // Latest path + fetcher without depending on their per-render identity — the
  // keyed effect reads these, and the fetcher closes over any extra inputs
  // (e.g. the round number) that are folded into `key`.
  const pathRef = useRef<CyclePath | null>(path);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    pathRef.current = path;
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    const p = pathRef.current;
    if (!key || !p) return;
    const ac = new AbortController();
    (async () => {
      try {
        const value = await fetcherRef.current(p, ac.signal);
        if (ac.signal.aborted) return;
        setLoaded({ key, value });
        setError(null);
      } catch (e) {
        if (ac.signal.aborted) return;
        setLoaded({ key, value: empty });
        setError((e as Error).message);
      }
    })();
    return () => ac.abort();
    // `empty` is a stable reference by contract; keying on it would refetch spuriously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const matched = loaded.key === key;
  return {
    value: matched ? loaded.value : empty,
    loading: key != null && !matched,
    error: matched ? error : null,
  };
}
