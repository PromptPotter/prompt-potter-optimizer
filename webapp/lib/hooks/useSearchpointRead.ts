"use client";

import { useMemo } from "react";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  samplesForRow,
  type ObserveConfig,
  type ObservePoint,
} from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useRound } from "@/lib/hooks/useRound";
import type { ReadFailure } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";
import type { SampleRow } from "@/lib/types";

export interface SearchpointRead {
  cfg: ObserveConfig | null;
  samples: SampleRow[];
  loading: boolean;
  failure: ReadFailure | null;
  unfiled: boolean;
}

const NO_SAMPLES: SampleRow[] = [];

export function useSearchpointRead(
  point: ObservePoint | null,
  nodeId?: string | null,
): SearchpointRead {
  const { dash } = useCycleStream();
  // From the workspace, NOT `dash`, which nulls on a unit switch and would starve the round read.
  const { viewedPath } = useWorkspace();
  const { unfiled, doc, loading, failure } = useRound(viewedPath, point?.round ?? null);

  const label = point?.label ?? null;
  const title = point?.title ?? "";
  // Null while filed, so the memo below does not re-run every poll.
  const liveDash = unfiled ? dash : null;
  const cfg = useMemo(() => {
    if (label === null) return null;
    if (!unfiled) return candidateObserveConfig(doc, label, title, nodeId);
    const live = liveCandidateObserveConfig(liveDash, label, nodeId);
    return live && { ...live, label: title };
  }, [label, title, nodeId, unfiled, liveDash, doc]);

  const row = point?.row ?? null;
  const rowDash = row?.source === "inflight" ? dash : null;
  const arm = row?.reading.arm;
  const samples = useMemo(
    () => (row ? samplesForRow(row, rowDash, doc) : NO_SAMPLES),
    // `row` is rebuilt every poll; these four are what a row's samples are keyed on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [row?.source, arm?.round, arm?.candidate_id, arm?.label, rowDash, doc],
  );

  return { cfg, samples, loading, failure, unfiled };
}
