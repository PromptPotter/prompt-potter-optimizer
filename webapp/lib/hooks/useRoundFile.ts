"use client";
// Lazy per-round file fetch. Deep audit surfaces (FreqChart bucket data,
// ScoringInspector composite + hits, OptimizerNodeDetail node blocks) need
// one round_NNNN.json at a time — not the full eager array. The summary
// surfaces (FitnessPanel, TrendChart, TopStrip sparkline, LineageTree) read
// `dash.rounds[]` directly and never hit this hook.
//
// Addressed by the viewed CYCLE PATH, not bare `(campaign, cycle)` ids: the
// round file follows the LEAF hop the dashboard shows, so an L4 inner loop's
// `rounds/round_NNNN.json` reads from the inner cycle's dir (via `?descend=`)
// instead of the outer root's empty `rounds/`. `fetchCycleFileByPath` mirrors
// `fetchDashboardByPath` — the same seam the live poll already rides.
//
// Rides `usePathKeyedFetch` for the stamp discipline (the loaded doc is returned
// only once its stamp matches the current key, so a unit/round switch never
// flashes the prior fetch's payload). The key folds the round number in beside
// the encoded path, so the fetch re-runs when either the viewed cycle or the
// round changes.

import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import type { RoundFileDoc } from "../poll";
import { usePathKeyedFetch } from "./usePathKeyedFetch";

export interface UseRoundFileState {
  doc: RoundFileDoc | null;
  loading: boolean;
  error: string | null;
}

function roundKey(path: CyclePath | null, round: number | null): string | null {
  if (!path || round == null) return null;
  return `${encodeCyclePath(path)}\x1f${round}`;
}

function roundPath(round: number): string {
  return `rounds/round_${String(round).padStart(4, "0")}.json`;
}

export function useRoundFile(
  path: CyclePath | null,
  round: number | null,
): UseRoundFileState {
  const { value, loading, error } = usePathKeyedFetch<RoundFileDoc | null>(
    roundKey(path, round),
    path,
    null,
    async (p, signal) => {
      const resp = await fetchCycleFileByPath(p, "cycle", roundPath(round!), signal);
      return resp.content ? (JSON.parse(resp.content) as RoundFileDoc) : null;
    },
  );
  return { doc: value, loading, error };
}
