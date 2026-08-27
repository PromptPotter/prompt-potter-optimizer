"use client";
// The Compare tab's one read. Not on the 2 s poll — a selection changes when the operator changes
// it, so this is `useFetch`, one shot per (selection, metric, ranking, trajectory).
//
// `subjects` are opaque address strings (`lib/api/reads.ts::subjectKey` owns the grammar) and
// `metric` an opaque selector — a catalogue key or a composed `expr:…`, both owned by the server.
// The one failure that survives rather than blanking the pane is `invalid` — a rejected expression
// is the operator's own half-typed input, not a dead read — and `useFetch` owns that, so there is
// no last-good state machine here.

import { fetchEvidence } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useFetch } from "@/lib/hooks/useFetch";

// A `useFetch` dep must be a scalar the effect can compare, so the selection travels as one
// joined string. `|` is the separator because no part of a subject address can contain it —
// a comma can (`;samples=3,7,11`), and splitting on one would tear an address in half.
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
  trajectory: boolean,
  config: boolean,
  metric: string,
): EvidenceRead {
  // Sorted so the same SET refetches once however the operator got there.
  const key = [...subjects].sort().join(SEP);
  const { data, loading, error, kind } = useFetch<Evidence>(
    key
      ? (signal) =>
          fetchEvidence(key.split(SEP), { ranking, trajectory, config, metric }, signal)
      : null,
    [key, ranking, trajectory, config, metric],
    "invalid",
  );

  const invalid = error !== null && kind === "invalid";
  return {
    evidence: data,
    loading,
    error: invalid ? null : error,
    invalidMetric: invalid ? error : null,
  };
}
