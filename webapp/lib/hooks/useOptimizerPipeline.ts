import { optimizerPipelineRead } from "@/lib/api";
import { pipelineReadStatus } from "@/lib/derivations";
import type { PipelineDoc, PipelineStatus } from "@/lib/types";
import { useConnector } from "./useConnector";
import { readyData, useRead } from "./useRead";

interface OptimizerPipeline {
  doc: PipelineDoc | null;
  status: PipelineStatus;
}

// The campaign's pipeline names the optimizer, so until it reads, the manifest's status is the campaign's.
export function useOptimizerPipeline(enabled = true): OptimizerPipeline {
  const { optimizer, schema } = useConnector();
  const read = useRead(enabled && optimizer ? optimizerPipelineRead(optimizer) : null);
  return {
    doc: readyData(read),
    status: schema.status === "ok" ? pipelineReadStatus(read) : schema.status,
  };
}
