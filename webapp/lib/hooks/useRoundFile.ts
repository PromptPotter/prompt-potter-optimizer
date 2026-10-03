"use client";
// Lazy per-round file fetch, addressed by CYCLE PATH so an L4 inner loop reads its own `rounds/`.

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

// The AUDIT TWIN: the per-node LLM I/O lives only here — the round document has no `nodes`.
export function useRoundAudit(
  path: CyclePath | null,
  round: number | null,
): RoundFileState<RoundAuditDoc> {
  return useCycleJson<RoundAuditDoc>(path, round, "audit", ".runtime/cache/rounds/");
}
