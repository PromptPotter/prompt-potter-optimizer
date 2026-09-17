"use client";
// ONE round of the VIEWED cycle, as the surfaces read it: which source speaks for it, that
// source's document, the round's candidate rows, and a row's per-sample lines.
//
// The source pick is `useRoundSource`'s (live → `dashboard.json`, historical → `round_NNNN.json`,
// never both), and the rows are the shared spine `derivations/round-candidates.ts` builds — so a
// surface asking for a round can no longer stitch one half of it onto the other's.

import { useMemo } from "react";
import { groupByRound, roundCandidates, samplesForRow } from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useWorkspace } from "@/lib/workspace";
import type { ElectedRow, RoundResult, SampleRow } from "@/lib/types";
import type { ReadFailure } from "@/lib/hooks/useRead";

export interface RoundRows {
  // The round in flight — its rows come from `dash`, and no file was fetched.
  live: boolean;
  doc: RoundResult | null;
  loading: boolean;
  failure: ReadFailure | null;
  rows: ElectedRow[];
  // A row by position in the round, or by the label it was minted with.
  row: (at: number | string) => ElectedRow | null;
  samples: (row: ElectedRow | null) => SampleRow[];
}

export function useRoundRows(round: number | null): RoundRows {
  const { dash } = useCycleStream();
  // Round files follow the VIEWED leaf hop — so an L4 inner loop's candidate resolves its
  // `round_NNNN.json` from the inner cycle's dir, not the outer root's empty `rounds/`.
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
