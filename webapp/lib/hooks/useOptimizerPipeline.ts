"use client";

import { optimizerPipelineRead } from "@/lib/api";
import type { PipelineDoc } from "@/lib/types";
import { readyData, useRead } from "./useRead";

interface OptimizerPipeline {
  doc: PipelineDoc | null;
  loading: boolean;
  error: string | null;
}

export function useOptimizerPipeline(optimizer: string | null): OptimizerPipeline {
  const read = useRead(optimizer ? optimizerPipelineRead(optimizer) : null);
  return {
    doc: readyData(read),
    loading: read.status === "loading",
    error: read.status === "failed" ? read.failure.message : null,
  };
}
