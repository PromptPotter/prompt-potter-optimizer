"use client";
// One polling primitive. Owns the lifecycle boilerplate every poll loop
// repeated: the interval, a `visibilitychange` pause/resume, an optional
// `focus` wake, cleanup, and a per-tick AbortController. The caller keeps
// its own `tick` body — `usePoll` never inspects or batches it.

import { useCallback, useEffect, useRef } from "react";

interface PollOptions {
  intervalMs: number;
  // Pause the timer (and abort the in-flight tick) while the tab is hidden.
  pauseWhenHidden?: boolean; // default true
  // Fire an extra tick the instant the window regains focus.
  tickOnFocus?: boolean; // default false
  // While false, no timer and no listeners — the loop is fully off.
  enabled?: boolean; // default true
  // Bump this (see lib/revalidate.ts) to force one immediate tick — e.g.
  // right after a mutation, so the loop doesn't wait for its next interval.
  revalidateOn?: number;
}

export function usePoll(
  tick: (signal: AbortSignal) => void | Promise<void>,
  opts: PollOptions,
): void {
  const {
    intervalMs,
    pauseWhenHidden = true,
    tickOnFocus = false,
    enabled = true,
    revalidateOn,
  } = opts;

  // Latest tick in a ref — the caller's closure changes identity every
  // render (it closes over poll state), but the interval must NOT reset
  // each render. Only `intervalMs`/`enabled`/… restart the timer.
  const tickRef = useRef(tick);
  useEffect(() => {
    tickRef.current = tick;
  });

  // The single in-flight AbortController; teardown (stop) aborts it.
  const abortRef = useRef<AbortController | null>(null);
  // Is a tick still running? A timer that fires mid-tick SKIPS rather than aborting —
  // else a tick slower than `intervalMs` (a large `/tree` under load: ~10s vs a 5s poll)
  // is killed before it can ever resolve, and the surface hangs on "Loading…" forever.
  // A unit switch still refreshes: the render-guarded consumer ignores a stale-key result,
  // and the next timer tick (once this one settles) fetches the new key.
  const inFlightRef = useRef(false);

  const runTick = useCallback(() => {
    if (inFlightRef.current) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    inFlightRef.current = true;
    void Promise.resolve(tickRef.current(ctrl.signal)).finally(() => {
      inFlightRef.current = false;
    });
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer != null) return;
      timer = setInterval(runTick, intervalMs);
      runTick();
    };
    const stop = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
      abortRef.current?.abort();
      abortRef.current = null;
      // The aborted tick is no longer in flight — clear synchronously so a following
      // start() (tab re-show / re-enable) isn't skipped waiting on the abort's `.finally`.
      inFlightRef.current = false;
    };
    const onVis = () => {
      if (!pauseWhenHidden) return;
      if (document.hidden) stop();
      else start();
    };
    const onFocus = () => runTick();

    if (!(pauseWhenHidden && document.hidden)) start();
    document.addEventListener("visibilitychange", onVis);
    if (tickOnFocus) window.addEventListener("focus", onFocus);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
      if (tickOnFocus) window.removeEventListener("focus", onFocus);
    };
  }, [intervalMs, pauseWhenHidden, tickOnFocus, enabled, runTick]);

  // Revalidation — an external bump fires one immediate tick. Skip the mount
  // value (the interval effect already ticked once) and a hidden tab.
  const prevReval = useRef(revalidateOn);
  useEffect(() => {
    if (revalidateOn === prevReval.current) return;
    prevReval.current = revalidateOn;
    if (!enabled || (pauseWhenHidden && document.hidden)) return;
    runTick();
  }, [revalidateOn, enabled, pauseWhenHidden, runTick]);
}
