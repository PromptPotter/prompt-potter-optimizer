"use client";
// The Compare tab's one read. Not on the 2 s poll — a selection changes when the operator changes
// it, so this is `useFetch`, one shot per (selection, metric, ranking).
//
// `metric` is opaque: a catalogue key or a composed `expr:…`, both owned by the server. The one
// failure that survives rather than blanking the pane is `invalid` — a rejected expression is the
// operator's own half-typed input, not a dead read — and `useFetch` owns that, so there is no
// last-good state machine here.

import { fetchEvidence } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useFetch } from "@/lib/hooks/useFetch";

export interface EvidenceRead {
  evidence: Evidence | null;
  loading: boolean;
  error: string | null;
  /** Set only when `error` is a rejected metric — render it beside the input, not as a dead pane. */
  invalidMetric: string | null;
}

export function useEvidence(
  campaignIds: readonly string[],
  ranking: boolean,
  metric: string,
): EvidenceRead {
  const key = [...campaignIds].sort().join(",");
  const { data, loading, error, kind } = useFetch<Evidence>(
    key ? (signal) => fetchEvidence(key.split(","), { ranking, metric }, signal) : null,
    [key, ranking, metric],
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
