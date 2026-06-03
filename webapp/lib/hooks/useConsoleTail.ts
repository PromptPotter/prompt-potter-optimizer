// Tails the active cycle's `output.log` at 1 s polling and returns the last
// N lines + any fetch error. Owns the data half of the console pane per the
// fetch-in-a-hook anatomy (webapp/CLAUDE.md): the component stays a pure
// renderer plus its own scroll/sticky-bottom DOM concerns.
//
// The LiveDashboardView projection writes `output.log` as an ANSI-colored
// line stream, so a consumer renders it as a terminal view of the optimizer's
// narration. Cycle change wipes immediately (render-phase guard) so the prior
// cycle's tail can't linger during the first new fetch; an in-flight tick
// discards its result once the viewed unit has moved on (`unitRef` guard).

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCycleFile } from "../api";
import { usePoll } from "./usePoll";

const MAX_LINES = 200;
const POLL_MS = 1000;

export interface ConsoleTail {
  lines: string[];
  error: string | null;
}

export function useConsoleTail(campaignId: string | null, cycleId: string | null): ConsoleTail {
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Cycle change ⇒ wipe immediately so the prior cycle's tail can't linger
  // visible during the first new fetch (render-phase guarded reset).
  const [prevCycleId, setPrevCycleId] = useState<string | null>(cycleId);
  if (cycleId !== prevCycleId) {
    setPrevCycleId(cycleId);
    setLines([]);
    setError(null);
  }

  // Latest viewed unit — lets a tick already in flight when the operator
  // switches cycles discard its now-stale result.
  const unit = campaignId && cycleId ? `${campaignId}::${cycleId}` : "";
  const unitRef = useRef(unit);
  useEffect(() => {
    unitRef.current = unit;
  }, [unit]);

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (!campaignId || !cycleId) return;
      const launchedFor = unit;
      try {
        const r = await fetchCycleFile(campaignId, cycleId, "cycle", "output.log", signal);
        if (signal.aborted || unitRef.current !== launchedFor) return;
        // Split, trim the trailing empty (the log ends with a newline), keep
        // the last MAX_LINES so the DOM can't grow unbounded.
        const split = (r.content ?? "").split("\n");
        if (split.length > 0 && split[split.length - 1] === "") split.pop();
        setLines(split.length > MAX_LINES ? split.slice(-MAX_LINES) : split);
        setError(null);
      } catch (e) {
        if ((e as Error).name === "AbortError" || signal.aborted) return;
        if (unitRef.current !== launchedFor) return;
        setError((e as Error).message);
      }
    },
    [campaignId, cycleId, unit],
  );
  usePoll(tick, { intervalMs: POLL_MS, enabled: !!campaignId && !!cycleId });

  return { lines, error };
}
