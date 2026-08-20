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
//
// `survive` opts one failure kind out of that blanking, and only `invalid`
// is ever a sane argument: a rejected query is the CALLER's own input, so
// clearing the pane would wipe the last good read out from under the cursor
// still typing it. The kept value has to outlive the key reset, because the
// key changes (new query) before the 400 that rejects it lands — which is
// why it rides a ref rather than the reset state.

import { failureKind, type FailureKind } from "@/lib/api/client";
import { useEffect, useRef, useState } from "react";

interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  // The classification, not a status literal — `webapp/CLAUDE.md` requires callers to branch on
  // this, and flattening the failure to a string is what made them unable to. `invalid` is the
  // one a caller may want to survive: a rejected query is the user's input, not a dead read.
  kind: FailureKind | null;
}

export function useFetch<T>(
  fetcher: ((signal: AbortSignal) => Promise<T>) | null,
  deps: readonly unknown[],
  survive?: FailureKind,
): FetchState<T> {
  const key = JSON.stringify(deps);

  // Latest fetcher in a ref — its closure identity changes every render,
  // but the fetch must re-run only when `key` changes, not every render.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });
  const keptRef = useRef<T | null>(null);

  const [state, setState] = useState<FetchState<T>>(() => ({
    data: null,
    error: null,
    loading: fetcher !== null,
    kind: null,
  }));
  // Render-phase reset — `deps` changed ⇒ drop the prior key's data now,
  // before paint (React's sanctioned "adjust state when a prop changes").
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setState({ data: null, error: null, loading: fetcher !== null, kind: null });
  }

  useEffect(() => {
    const fn = fetcherRef.current;
    if (!fn) return;
    const ac = new AbortController();
    let cancelled = false;
    fn(ac.signal)
      .then((data) => {
        keptRef.current = data;
        if (!cancelled) setState({ data, error: null, loading: false, kind: null });
      })
      .catch((e) => {
        if (cancelled || ac.signal.aborted) return;
        const kind = failureKind(e);
        const data = kind === survive ? keptRef.current : null;
        setState({ data, error: (e as Error).message, loading: false, kind });
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [key, survive]);

  return state;
}
