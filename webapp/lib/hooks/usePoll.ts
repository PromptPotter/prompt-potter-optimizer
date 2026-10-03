"use client";
// The one polling primitive: interval, hidden-tab pause, focus wake, per-key abort.

import { useCallback, useEffect, useRef } from "react";

const SOLE_KEY: readonly string[] = [""];

interface PollOptions {
  intervalMs: number;
  // Read fresh each tick; each key runs and skips on its own.
  keys?: () => readonly string[];
  pauseWhenHidden?: boolean; // default true
  tickOnFocus?: boolean; // default false
  enabled?: boolean; // default true
  // A `lib/revalidate.ts` bump forces one immediate tick.
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

  // Refs, so a caller's per-render closure never restarts the timer.
  const tickRef = useRef(tick);
  const keysRef = useRef(keys);
  useEffect(() => {
    tickRef.current = tick;
    keysRef.current = keys;
  });

  // A tick SKIPS a still-running key, never aborts it: a fetch slower than `intervalMs` would
  // otherwise never resolve.
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
      // Cleared synchronously, or the next start() skips keys awaiting an abort's `.finally`.
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

  const prevReval = useRef(revalidateOn);
  useEffect(() => {
    if (revalidateOn === prevReval.current) return;
    prevReval.current = revalidateOn;
    if (!enabled || (pauseWhenHidden && document.hidden)) return;
    runTick();
  }, [revalidateOn, enabled, pauseWhenHidden, runTick]);
}
