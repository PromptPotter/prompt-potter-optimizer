"use client";
// The Compare tab's one read. `subjects` and `metric` are opaque, server-owned grammar.

import { fetchEvidence } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";

// No subject address can contain `|`; a comma it can (`;samples=3,7,11`).
const SEP = "|";

export interface EvidenceRead {
  evidence: Evidence | null;
  loading: boolean;
  error: string | null;
  // Render beside the input, not as a dead pane.
  invalidMetric: string | null;
}

export function useEvidence(
  subjects: readonly string[],
  ranking: boolean,
  winnerChain: boolean,
  config: boolean,
  metric: string,
  // `row,col` over two served factors. Pooled server-side, so a new axis is a new read.
  grid: string,
): EvidenceRead {
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
