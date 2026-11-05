import type { NodeConfigParam, PipelineView, PipelineViewNode } from "@/lib/api/types";

// `input` / `output` are synthetic terminals the server adds (`domain/pipeline_parsing.py`).
export function interiorNodes(view: PipelineView | null | undefined): PipelineViewNode[] {
  return (view?.nodes ?? []).filter((n) => n.kind !== "io");
}

// What a node calls is its served `model` row; "" for a node with none, which calls no model.
export function nodeModel(
  rows: Record<string, NodeConfigParam[]> | null | undefined,
  id: string,
): string {
  const value = rows?.[id]?.find((p) => p.key === "model")?.value;
  return typeof value === "string" ? value : "";
}
