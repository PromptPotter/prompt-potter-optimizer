"use client";
// One optimizer manifest, read as one shape by every surface. The name is the served
// `ConnectorView.optimizer` — which manifest the viewed course runs — or null to read nothing.

import { fetchPipeline } from "@/lib/api";
import type { PipelineDoc } from "@/lib/types";
import { readyData, useRead } from "./useRead";

export interface OptimizerPipeline {
  doc: PipelineDoc | null;
  loading: boolean;
  error: string | null;
}

export function useOptimizerPipeline(optimizer: string | null): OptimizerPipeline {
  const read = useRead(
    optimizer
      ? {
          key: `optimizer-pipeline|${optimizer}`,
          fetch: (signal) => fetchPipeline(optimizer, signal).then((p) => p as PipelineDoc),
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
