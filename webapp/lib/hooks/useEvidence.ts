"use client";
// The Compare tab's one read. Not on the 2 s poll — a selection changes when the operator changes
// it, so this is `useRead`, one shot per (selection, metric, ranking, winnerChain).
//
// `subjects` are opaque address strings (`lib/api/reads.ts::subjectKey` owns the grammar) and
// `metric` an opaque selector — a catalogue key or a composed `expr:…`, both owned by the server.
// The one failure that survives rather than blanking the pane is `invalid` — a rejected expression
// is the operator's own half-typed input, not a dead read — and `useRead` owns that, so there is
// no last-good state machine here.

import { fetchEvidence } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";

// The selection travels as one joined string. `|` is the separator because no part of a subject
// address can contain it — a comma can (`;samples=3,7,11`), and splitting on one would tear an
// address in half.
const SEP = "|";

export interface EvidenceRead {
  evidence: Evidence | null;
  loading: boolean;
  error: string | null;
  /** Set only when `error` is a rejected metric — render it beside the input, not as a dead pane. */
  invalidMetric: string | null;
}

export function useEvidence(
  subjects: readonly string[],
  ranking: boolean,
  winnerChain: boolean,
  config: boolean,
  metric: string,
  // `row,col` over two served factors, empty for none. Part of the key rather than a client-side
  // grouping because the cells are POOLED server-side, so changing an axis is a different read.
  grid: string,
): EvidenceRead {
  // Sorted so the same SET refetches once however the operator got there.
  const selection = [...subjects].sort().join(SEP);
  const read = useRead(
    selection
      ? {
          key: [selection, ranking, winnerChain, config, metric, grid].join("\x1f"),
          fetch: (signal) =>
            fetchEvidence(
              selection.split(SEP),
              { ranking, winnerChain, config, metric, grid },
              signal,
            ),
        }
      : null,
    { surface: "evidence", survive: "invalid" },
  );

  if (read.status === "ready") {
    return { evidence: read.data, loading: false, error: null, invalidMetric: null };
  }
  if (read.status !== "failed") {
    return { evidence: null, loading: read.status === "loading", error: null, invalidMetric: null };
  }
  // With nothing kept, `invalid` is a dead read like any other: `/evidence` also 400s on an
  // unmeasured selection, which is what a campaign whose origin has not run produces.
  if (read.failure.kind === "invalid" && read.kept !== null) {
    return { evidence: read.kept, loading: false, error: null, invalidMetric: read.failure.message };
  }
  return { evidence: null, loading: false, error: read.failure.message, invalidMetric: null };
}
