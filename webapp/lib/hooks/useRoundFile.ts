"use client";
// Lazy per-round file fetch. Deep audit surfaces (FreqChart bucket data,
// ScoringInspector composite + hits, OptimizerNodeDetail node blocks) need
// one round_NNNN.json at a time — not the full eager array. The summary
// surfaces (the candidates card, TrendChart, TopStrip sparkline) read
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
import type { RoundAuditDoc, RoundResult } from "../types";
import { usePathKeyedFetch } from "./usePathKeyedFetch";

export interface UseRoundFileState {
  doc: RoundResult | null;
  loading: boolean;
  error: string | null;
}

export interface UseRoundAuditState {
  doc: RoundAuditDoc | null;
  loading: boolean;
  error: string | null;
}

function roundKey(path: CyclePath | null, round: number | null, kind: string): string | null {
  if (!path || round == null) return null;
  return `${encodeCyclePath(path)}\x1f${kind}\x1f${round}`;
}

function basename(round: number): string {
  return `round_${String(round).padStart(4, "0")}.json`;
}

function useCycleJson<T>(
  path: CyclePath | null,
  round: number | null,
  kind: string,
  relPath: (round: number) => string,
) {
  return usePathKeyedFetch<T | null>(
    roundKey(path, round, kind),
    path,
    null,
    async (p, signal) => {
      const resp = await fetchCycleFileByPath(p, "cycle", relPath(round!), signal);
      return resp.content ? (JSON.parse(resp.content) as T) : null;
    },
  );
}

export function useRoundFile(path: CyclePath | null, round: number | null): UseRoundFileState {
  const { value, loading, error } = useCycleJson<RoundResult>(
    path,
    round,
    "round",
    (r) => `rounds/${basename(r)}`,
  );
  return { doc: value, loading, error };
}

// The AUDIT TWIN — same basename, different tree. `rounds/round_NNNN.json` is the round
// document (`RoundResult`) and carries NO `nodes` block; the per-node LLM I/O lives only
// here, written by `AuditTrailView`. The node inspector used to read `nodes` off the round
// document, which meant it rendered nothing for every completed round.
export function useRoundAudit(path: CyclePath | null, round: number | null): UseRoundAuditState {
  const { value, loading, error } = useCycleJson<RoundAuditDoc>(
    path,
    round,
    "audit",
    (r) => `.runtime/cache/rounds/${basename(r)}`,
  );
  return { doc: value, loading, error };
}
