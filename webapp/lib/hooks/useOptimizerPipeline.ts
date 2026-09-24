"use client";
// The install-global optimizer manifest, read as one shape by every surface.

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
