import type { PipelineView } from "@/components/workflow/types";

// The target/backend node ids of a connector view — every node that isn't a
// pure I/O port. These are the ids the selection store uses for the backend
// node panel (TargetPipelineHero click → BackendNodeDetail). The demo/preview
// hero with no view falls back to the synthetic "llm" chip id, matching the
// hero's own placeholder. Used by ChatPane as the membership gate for which
// node clicks open the read-only detail.

export function targetNodeIds(view: PipelineView | null): string[] {
  if (!view) return ["llm"];
  return view.nodes.filter((n) => n.kind !== "io").map((n) => n.id);
}
