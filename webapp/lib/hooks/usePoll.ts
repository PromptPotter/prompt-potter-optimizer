"use client";
// One polling primitive. Owns the lifecycle boilerplate every poll loop
// repeated: the interval, a `visibilitychange` pause/resume, an optional
// `focus` wake, cleanup, and a per-key AbortController. The caller keeps
// its own `tick` body — `usePoll` never inspects or batches it.

import { useCallback, useEffect, useRef } from "react";

const SOLE_KEY: readonly string[] = [""];

interface PollOptions {
  intervalMs: number;
  // The keys this loop fans out over, read fresh each tick; omitted is one anonymous key.
  // Each runs and skips on its own, so a slow fetch holds back nothing but itself.
  keys?: () => readonly string[];
  // Pause the timer (and abort the in-flight ticks) while the tab is hidden.
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
  tick: (signal: AbortSignal, key: string) => void | Promise<void>,
  opts: PollOptions,
): void {
  const {
    intervalMs,
    keys,
    pauseWhenHidden = true,
    tickOnFocus = false,
    enabled = true,
    revalidateOn,
  } = opts;

  // Latest tick + key source in refs — the caller's closures change identity every
  // render (they close over poll state), but the interval must NOT reset
  // each render. Only `intervalMs`/`enabled`/… restart the timer.
  const tickRef = useRef(tick);
  const keysRef = useRef(keys);
  useEffect(() => {
    tickRef.current = tick;
    keysRef.current = keys;
  });

  // One in-flight AbortController per RUNNING key; teardown (stop) aborts them all. A timer
  // firing while a key still runs SKIPS THAT KEY rather than aborting it — else a fetch
  // slower than `intervalMs` (a large `/tree` under load: ~10s vs a 5s poll) is killed
  // before it can ever resolve and the surface hangs on "Loading…" forever, while skipping
  // the whole tick would hold every other key to the slowest one's cadence. A unit switch
  // still refreshes: the render-guarded consumer ignores a stale-key result, and the next
  // timer tick (once this one settles) fetches the new key.
  const inFlightRef = useRef(new Map<string, AbortController>());

  const runTick = useCallback(() => {
    for (const key of keysRef.current?.() ?? SOLE_KEY) {
      if (inFlightRef.current.has(key)) continue;
      const ctrl = new AbortController();
      inFlightRef.current.set(key, ctrl);
      void Promise.resolve(tickRef.current(ctrl.signal, key)).finally(() => {
        if (inFlightRef.current.get(key) === ctrl) inFlightRef.current.delete(key);
      });
    }
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
      // An aborted key is no longer in flight — clear synchronously so a following
      // start() (tab re-show / re-enable) isn't skipped waiting on an abort's `.finally`.
      for (const c of inFlightRef.current.values()) c.abort();
      inFlightRef.current.clear();
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
