import type { PipelineView, PipelineViewNode } from "@/components/workflow";

// The nodes a pipeline actually RUNS. `input` / `output` are synthetic terminals the
// server adds so the graph has arrow ends (`domain/pipeline_parsing.py`), and no node
// list wants them.
export function interiorNodes(view: PipelineView | null | undefined): PipelineViewNode[] {
  return (view?.nodes ?? []).filter((n) => n.kind !== "io");
}
