import type { PipelineView, PipelineViewNode } from "@/lib/types";

// `input` / `output` are synthetic terminals the server adds (`domain/pipeline_parsing.py`).
export function interiorNodes(view: PipelineView | null | undefined): PipelineViewNode[] {
  return (view?.nodes ?? []).filter((n) => n.kind !== "io");
}
