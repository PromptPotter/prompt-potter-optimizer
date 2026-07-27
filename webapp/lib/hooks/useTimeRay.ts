"use client";
// The time-ray's fetch + paging lifecycle for the viewed course.
//
// Two windows, two lifetimes: the HEAD is polled (new events land there, its ETag rides
// the family's mtimes); OLDER windows are fetched on demand and held in memory — a deeper
// page is strictly below the cursor by construction, so the head never overlaps it. No
// SSE join, no `since=` on the tail: one live channel, one history channel
// (webapp/CLAUDE.md § "/ray is the CHRONOLOGY").

import { useCallback, useMemo, useRef, useState } from "react";
import { fetchTimeRay } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { usePoll } from "@/lib/hooks/usePoll";
import { useRevalidation } from "@/lib/revalidate";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import type { RayItem } from "@/lib/api/types";

const POLL_MS = 5000;
const WINDOW = 200;

interface RayState {
  // The course this state was loaded for. Async continuations compare against it, so a
  // response that survived a course switch cannot write the old course's chronology into
  // the new course's state (webapp/CLAUDE.md § State reset on prop change).
  key: string;
  // Windows older than the head, oldest-first, already concatenated in order.
  older: RayItem[];
  head: RayItem[];
  // Cursor for the window before everything loaded — null at the family's beginning.
  cursor: string | null;
  loaded: boolean;
  failed: boolean;
}

const EMPTY: Omit<RayState, "key"> = {
  older: [],
  head: [],
  cursor: null,
  loaded: false,
  failed: false,
};

export interface TimeRayState {
  /** The whole loaded span, oldest-first. */
  items: RayItem[];
  loaded: boolean;
  failed: boolean;
  /** Older records exist below what is loaded. */
  hasMore: boolean;
  loadOlder: () => void;
  /** Wall clock, advanced once per poll tick — the input the head's age test needs. A pure
   *  derivation cannot call `Date.now()`, and a 304 re-renders nothing, so without this the
   *  "no progress for Xm" reading would freeze at whatever it said when progress stopped. */
  nowMs: number;
}

export function useTimeRay(path: CyclePath | null, enabled: boolean): TimeRayState {
  const key = path ? encodeCyclePath(path) : "";
  const { authed, onAuthError } = useAuthGate();
  const [state, setState] = useState<RayState>(() => ({ key, ...EMPTY }));
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Render-phase guarded reset on course switch (webapp/CLAUDE.md § State reset on prop
  // change): the prior course's chronology clears in the same commit, so a drill-in never
  // paints the outer run's events under the inner run's header. `seq` rides into
  // `revalidateOn` so the new course fetches within a frame rather than waiting out the
  // interval — the timer itself must not restart, or a switch-happy operator never polls.
  const [prevKey, setPrevKey] = useState(key);
  const [seq, setSeq] = useState(0);
  if (key !== prevKey) {
    setPrevKey(key);
    setState({ key, ...EMPTY });
    setSeq((s) => s + 1);
  }

  // The head ETag, stamped WITH the key it belongs to. Stamping rather than clearing on
  // switch is what keeps this out of the render phase (`react-hooks/refs`): a mismatched
  // stamp reads as "no validator", and only the tick ever writes. Replaying a stale ETag
  // would 304 into an empty window.
  const etagRef = useRef<{ key: string; etag: string | null }>({ key: "", etag: null });
  const loadingOlderRef = useRef(false);

  // `usePoll` keeps the latest tick in a ref, so re-creating this on a course switch swaps
  // the closure without restarting the timer.
  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (!path) return;
      setNowMs(Date.now());
      const known = etagRef.current.key === key ? etagRef.current.etag : null;
      try {
        const res = await fetchTimeRay(path, { limit: WINDOW }, known, signal);
        if (signal.aborted) return;
        etagRef.current = { key, etag: res.validator };
        if (res.kind === "not_modified") {
          setState((prev) =>
            prev.key !== key || prev.loaded ? prev : { ...prev, loaded: true },
          );
          return;
        }
        setState((prev) => {
          if (prev.key !== key) return prev;
          return {
            // A refetch replaces the HEAD only: the head can only have grown at its newest
            // end, and the cursor stays whatever the oldest loaded window reported — a
            // fresh head window's cursor describes a boundary already paged past.
            key,
            older: prev.older,
            head: res.data.items,
            cursor: prev.older.length > 0 ? prev.cursor : res.data.cursor_prev,
            loaded: true,
            failed: false,
          };
        });
      } catch (e) {
        if (signal.aborted) return;
        onAuthError(e);
        setState((prev) =>
          prev.key !== key ? prev : { ...prev, loaded: true, failed: true },
        );
      }
    },
    [path, key, onAuthError],
  );

  usePoll(tick, {
    intervalMs: POLL_MS,
    enabled: enabled && authed && !!path,
    tickOnFocus: true,
    revalidateOn: useRevalidation() + seq,
  });

  const cursor = state.cursor;
  const loadOlder = useCallback(() => {
    if (!path || !cursor || loadingOlderRef.current) return;
    loadingOlderRef.current = true;
    void fetchTimeRay(path, { limit: WINDOW, before: cursor })
      .then((res) => {
        if (res.kind !== "ok") return;
        setState((prev) => {
          if (prev.key !== key) return prev;
          return {
            ...prev,
            older: [...res.data.items, ...prev.older],
            cursor: res.data.cursor_prev,
          };
        });
      })
      .catch((e: unknown) => onAuthError(e))
      .finally(() => {
        loadingOlderRef.current = false;
      });
  }, [path, key, cursor, onAuthError]);

  const items = useMemo(() => [...state.older, ...state.head], [state.older, state.head]);

  return {
    items,
    loaded: state.loaded,
    failed: state.failed,
    hasMore: cursor !== null,
    loadOlder,
    nowMs,
  };
}
