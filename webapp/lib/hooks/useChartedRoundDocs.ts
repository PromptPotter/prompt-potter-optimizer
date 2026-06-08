"use client";
// Mode-gated bulk round-file fetch for the fitness chart's "fixed sample set"
// mode. The default chart reads `dash` only (the render-cost guard); recomputing
// every bar's accuracy over a chosen sample subset needs the per-(candidate,
// sample) hit matrix, which lives in each closed round's `round_NNNN.json::
// all_candidate_results`. So this hook fetches those files — but ONLY when
// `enabled` (a sample set is active). When disabled it holds an empty map and
// fires nothing, so the common path stays `dash`-only.
//
// Peer of `useRoundFile` (single round); this one fans out over the charted
// rounds and stamps its result map with the key it was fetched for so a unit /
// round-set switch never surfaces the prior fetch under the new key.

import { useEffect, useState } from "react";
import { fetchCycleFile } from "../api";
import type { RoundFileDoc } from "../poll";

const EMPTY: ReadonlyMap<number, RoundFileDoc | null> = new Map();

function roundPath(round: number): string {
  return `rounds/round_${String(round).padStart(4, "0")}.json`;
}

export function useChartedRoundDocs(
  campaignId: string | null,
  cycleId: string | null,
  rounds: number[],
  enabled: boolean,
): ReadonlyMap<number, RoundFileDoc | null> {
  // Stable primitive key — the `rounds` array gets a fresh identity each render,
  // so depend on its sorted-unique string form, not the array, to avoid refetch
  // loops. The effect reconstructs the round list from the key.
  const roundsKey = [...new Set(rounds)].sort((a, b) => a - b).join(",");
  const key = enabled && campaignId && cycleId && roundsKey ? `${campaignId}\x1f${cycleId}\x1f${roundsKey}` : null;
  const [loaded, setLoaded] = useState<{ key: string | null; docs: Map<number, RoundFileDoc | null> }>({
    key: null,
    docs: new Map(),
  });

  useEffect(() => {
    if (!key || !campaignId || !cycleId) return;
    const ac = new AbortController();
    const list = roundsKey.split(",").map(Number);
    (async () => {
      const entries = await Promise.all(
        list.map(async (r) => {
          try {
            const res = await fetchCycleFile(campaignId, cycleId, "cycle", roundPath(r), ac.signal);
            const doc = res.content ? (JSON.parse(res.content) as RoundFileDoc) : null;
            return [r, doc] as const;
          } catch {
            return [r, null] as const;
          }
        }),
      );
      if (ac.signal.aborted) return;
      setLoaded({ key, docs: new Map(entries) });
    })();
    return () => ac.abort();
  }, [key, campaignId, cycleId, roundsKey]);

  if (!enabled) return EMPTY;
  // Until the loaded stamp matches the requested key, hold the empty map (the
  // recompute falls back to per-candidate accuracy for that frame, no flash).
  return loaded.key === key ? loaded.docs : EMPTY;
}
