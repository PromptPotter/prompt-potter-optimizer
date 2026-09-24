"use client";
// ONE round of the VIEWED cycle: its selected source, document, candidate rows and sample lines.

import { useMemo } from "react";
import { groupByRound, roundCandidates, samplesForRow } from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useWorkspace } from "@/lib/workspace";
import type { ElectedRow, RoundResult, SampleRow } from "@/lib/types";
import type { ReadFailure } from "@/lib/hooks/useRead";

export interface RoundRows {
  live: boolean;
  doc: RoundResult | null;
  loading: boolean;
  failure: ReadFailure | null;
  rows: ElectedRow[];
  row: (at: number | string) => ElectedRow | null;
  samples: (row: ElectedRow | null) => SampleRow[];
}

export function useRoundRows(round: number | null): RoundRows {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const { live, doc, loading, failure } = useRoundSource(viewedPath, round, dash);

  const rows = useMemo(() => {
    if (round == null) return [];
    return groupByRound(roundCandidates(dash)).get(round) ?? [];
  }, [dash, round]);

  return useMemo(
    () => ({
      live,
      doc,
      loading,
      failure,
      rows,
      row: (at) =>
        (typeof at === "number" ? rows[at] : rows.find((r) => r.label === at)) ?? null,
      samples: (row) => (row ? samplesForRow(row, dash, doc) : []),
    }),
    [live, doc, loading, failure, rows, dash],
  );
}
