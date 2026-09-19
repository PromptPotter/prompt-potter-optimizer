"use client";
// The install-global optimizer manifest, read as one shape. Four surfaces need it —
// the dashboard canvas, the chat hero's outermost level, the ingest optimizer
// section and the node detail — and each had spelled the same fetch + cast for
// itself, so a fourth reader meant a fourth copy of the same three lines.
//
// `enabled` false parks the read: the chat hero fetches it only once zoomed out to
// the optimizer level, and a parked hook must not spend the round-trip.

import { fetchPipeline } from "@/lib/api";
import type { PipelineDoc } from "@/components/workflow";
import { readyData, useRead } from "./useRead";

export interface OptimizerPipeline {
  doc: PipelineDoc | null;
  loading: boolean;
  error: string | null;
}

export function useOptimizerPipeline(enabled = true): OptimizerPipeline {
  const read = useRead(
    enabled
      ? {
          key: "optimizer-pipeline",
          fetch: (signal) => fetchPipeline(signal).then((p) => p as PipelineDoc),
        }
      : null,
    { surface: "optimizer-pipeline" },
  );
  return {
    doc: readyData(read),
    loading: read.status === "loading",
    error: read.status === "failed" ? read.failure.message : null,
  };
}
