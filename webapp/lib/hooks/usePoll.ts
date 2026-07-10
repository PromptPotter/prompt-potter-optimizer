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

  // The single in-flight AbortController — a new tick aborts the prior one.
  const abortRef = useRef<AbortController | null>(null);

  const runTick = useCallback(() => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    void tickRef.current(ctrl.signal);
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
