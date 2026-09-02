import type { PipelineDoc, PipelineView, PipelineViewNode } from "@/components/workflow";

// The nodes a pipeline actually RUNS. `input` / `output` are synthetic terminals the
// server adds so the graph has arrow ends (`domain/pipeline_parsing.py`), and no node
// list wants them.
export function interiorNodes(view: PipelineView | null | undefined): PipelineViewNode[] {
  return (view?.nodes ?? []).filter((n) => n.kind !== "io");
}

// The node that runs something ELSE — the optimizer's `l1_score`, which fires the whole
// campaign pipeline under it. The server names this per campaign (`nests.node`); the optimizer
// manifest has no parent to be named by, so its one is derived the way the server derives it:
// the measurement node is what runs another pipeline. Shared by all three hosts of that
// picture, since a host deriving `null` of its own drops the nesting glyph on one surface only.
export function measurementNode(doc: PipelineDoc | null | undefined): string | null {
  return interiorNodes(doc?.view).find((n) => n.kind === "measurement")?.id ?? null;
}

export interface OriginPrompt {
  fields: Record<string, unknown>;
  version: string;
  // How many prompts this node declares. A node running several (`checkin` runs two)
  // shows the first and SAYS there are more — a silently dropped one reads as a node
  // with a single prompt.
  count: number;
}

// The node's STATIC starting prompt out of the served optimizer manifest. A
// searchpoint carries only the optimizer's evolved DELTA, so a node whose prompt has
// never been mutated has nothing in `resolved_pipeline_params` — this is the floor
// underneath that, and the reason `resolved_prompts` is on the wire.
//
// Lowest version wins: `"{node}/1"` is the prompt the node opens with. Sorted
// numerically, because `"10"` sorts before `"2"` as a string.
export function nodeOriginPrompt(
  doc: PipelineDoc | null | undefined,
  nodeId: string | null | undefined,
): OriginPrompt | null {
  const all = doc?.resolved_prompts;
  if (!all || !nodeId) return null;
  const mine = Object.keys(all)
    .filter((k) => k.slice(0, k.lastIndexOf("/")) === nodeId)
    .sort((a, b) => Number(a.slice(a.lastIndexOf("/") + 1)) - Number(b.slice(b.lastIndexOf("/") + 1)));
  const first = mine[0];
  if (first === undefined) return null;
  const fields = all[first];
  if (!fields) return null;
  return { fields, version: first.slice(first.lastIndexOf("/") + 1), count: mine.length };
}
