"use client";
// One-shot fetch — the non-polling sibling of usePoll. Owns the
// AbortController, cancellation, and loading/error/data state that every
// hand-rolled `useEffect` fetch otherwise repeats. Re-fetches when `deps`
// change. `fetcher === null` means "not ready to fetch" — data stays
// null, loading false; the caller's guard for an unresolved unit.
//
// Key-scoped per webapp/CLAUDE.md: a `deps` change blanks data/error in
// the same render (render-phase guarded reset), so the gap before the new
// fetch lands shows loading, never the prior key's stale result. `deps`
// are compared by value — pass primitives, as you would `useEffect` deps.

import { useEffect, useRef, useState } from "react";

interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useFetch<T>(
  fetcher: ((signal: AbortSignal) => Promise<T>) | null,
  deps: readonly unknown[],
): FetchState<T> {
  const key = JSON.stringify(deps);

  // Latest fetcher in a ref — its closure identity changes every render,
  // but the fetch must re-run only when `key` changes, not every render.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const [state, setState] = useState<FetchState<T>>(() => ({
    data: null,
    error: null,
    loading: fetcher !== null,
  }));
  // Render-phase reset — `deps` changed ⇒ drop the prior key's data now,
  // before paint (React's sanctioned "adjust state when a prop changes").
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setState({ data: null, error: null, loading: fetcher !== null });
  }

  useEffect(() => {
    const fn = fetcherRef.current;
    if (!fn) return;
    const ac = new AbortController();
    let cancelled = false;
    fn(ac.signal)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((e) => {
        if (cancelled || ac.signal.aborted) return;
        setState({ data: null, error: (e as Error).message, loading: false });
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [key]);

  return state;
}
