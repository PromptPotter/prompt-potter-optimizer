"use client";
// The install-global optimizer manifest, read as one shape. Four surfaces need it —
// the dashboard canvas, the chat hero's outermost level, the ingest optimizer
// section and the node detail — and each had spelled the same `useFetch` + cast for
// itself, so a fourth reader meant a fourth copy of the same three lines.
//
// `enabled` false parks the read: the chat hero fetches it only once zoomed out to
// the optimizer level, and a parked hook must not spend the round-trip.

import { fetchPipeline } from "@/lib/api";
import type { PipelineDoc } from "@/components/workflow";
import { useFetch } from "./useFetch";

export interface OptimizerPipeline {
  doc: PipelineDoc | null;
  loading: boolean;
  error: string | null;
}

export function useOptimizerPipeline(enabled = true): OptimizerPipeline {
  const { data, loading, error } = useFetch<PipelineDoc>(
    enabled ? (signal) => fetchPipeline(signal).then((p) => p as PipelineDoc) : null,
    [enabled],
  );
  return { doc: data, loading, error };
}
