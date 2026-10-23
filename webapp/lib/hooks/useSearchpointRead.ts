"use client";

import { useMemo } from "react";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  type ObserveConfig,
  type ObservePoint,
} from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useRound } from "@/lib/hooks/useRound";
import type { ReadFailure } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";

export interface SearchpointRead {
  cfg: ObserveConfig | null;
  loading: boolean;
  failure: ReadFailure | null;
  unfiled: boolean;
}

export function useSearchpointRead(
  point: ObservePoint | null,
  nodeId?: string | null,
): SearchpointRead {
  const { dash } = useCycleStream();
  // From the workspace, NOT `dash`, which nulls on a unit switch and would starve the round read.
  const { viewedPath } = useWorkspace();
  const { unfiled, doc, loading, failure } = useRound(viewedPath, point?.round ?? null);

  const key = point?.key ?? null;
  const title = point?.title ?? "";
  // Null while filed, so the memo below does not re-run every poll.
  const liveDash = unfiled ? dash : null;
  const cfg = useMemo(() => {
    if (key === null) return null;
    if (!unfiled) return candidateObserveConfig(doc, key, title, nodeId);
    const live = liveCandidateObserveConfig(liveDash, key, nodeId);
    return live && { ...live, label: title };
  }, [key, title, nodeId, unfiled, liveDash, doc]);

  return { cfg, loading, failure, unfiled };
}
