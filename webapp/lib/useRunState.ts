"use client";
// Polls GET /campaigns/{c}/cycles/{cid}/runstate for the VIEWED cycle, so run
// controls + state badges reflect the run you're looking at — not the single
// active-pointer cycle. The endpoint is non-cached (the dashboard route is
// 304-cached, useless while a paused run leaves the file static). Re-ticks on
// the revalidation bump so a pause/resume/stop flips the state at once.

import { useState } from "react";
import { fetchRunState, type CycleRunState } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { useRevalidation } from "@/lib/revalidate";

const RUNSTATE_POLL_MS = 3000;

export function useRunState(
  campaignId: string | null,
  cycleId: string | null,
): CycleRunState | null {
  const enabled = !!campaignId && !!cycleId;
  const [state, setState] = useState<CycleRunState | null>(null);

  // Render-phase guarded reset: drop the prior cycle's state the instant the
  // viewed cycle changes, so a stale state never renders under the new unit.
  const key = `${campaignId}/${cycleId}`;
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setState(null);
  }

  const revalidateOn = useRevalidation();
  usePoll(
    async (signal) => {
      if (!campaignId || !cycleId) return;
      try {
        setState(await fetchRunState(campaignId, cycleId, signal));
      } catch {
        setState(null);
      }
    },
    { intervalMs: RUNSTATE_POLL_MS, enabled, revalidateOn, pauseWhenHidden: true },
  );

  return enabled ? state : null;
}
