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
// The key folds the round number in beside the encoded path, so the fetch re-runs
// when either the viewed cycle or the round changes.

import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import type { RoundAuditDoc, RoundResult } from "../types";
import { readyData, useRead, type ReadFailure } from "./useRead";

export interface RoundFileState<T> {
  doc: T | null;
  loading: boolean;
  failure: ReadFailure | null;
}

function basename(round: number): string {
  return `round_${String(round).padStart(4, "0")}.json`;
}

function useCycleJson<T>(
  path: CyclePath | null,
  round: number | null,
  kind: "round" | "audit",
  relPath: string,
): RoundFileState<T> {
  const read = useRead(
    path && round != null
      ? {
          key: `${encodeCyclePath(path)}\x1f${kind}\x1f${round}`,
          fetch: async (signal) => {
            const resp = await fetchCycleFileByPath(
              path,
              "cycle",
              `${relPath}${basename(round)}`,
              signal,
            );
            return resp.content ? (JSON.parse(resp.content) as T) : null;
          },
        }
      : null,
    { surface: `${kind}-file` },
  );
  return {
    doc: readyData(read),
    loading: read.status === "loading",
    failure: read.status === "failed" ? read.failure : null,
  };
}

export function useRoundFile(
  path: CyclePath | null,
  round: number | null,
): RoundFileState<RoundResult> {
  return useCycleJson<RoundResult>(path, round, "round", "rounds/");
}

// The AUDIT TWIN — same basename, different tree. `rounds/round_NNNN.json` is the round
// document (`RoundResult`) and carries NO `nodes` block; the per-node LLM I/O lives only
// here, written by `AuditTrailProjection`. The node inspector used to read `nodes` off the round
// document, which meant it rendered nothing for every completed round.
export function useRoundAudit(
  path: CyclePath | null,
  round: number | null,
): RoundFileState<RoundAuditDoc> {
  return useCycleJson<RoundAuditDoc>(path, round, "audit", ".runtime/cache/rounds/");
}
