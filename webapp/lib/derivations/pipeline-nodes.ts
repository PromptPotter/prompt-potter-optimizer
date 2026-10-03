import type { PipelineDoc, PipelineView, PipelineViewNode } from "@/components/workflow";

// `input` / `output` are synthetic terminals the server adds for arrow ends
// (`domain/pipeline_parsing.py`).
export function interiorNodes(view: PipelineView | null | undefined): PipelineViewNode[] {
  return (view?.nodes ?? []).filter((n) => n.kind !== "io");
}

// The server names this per campaign (`nests.node`); the optimizer manifest has no parent to
// name it, so it is derived as the server does — the measurement node runs another pipeline.
export function measurementNode(doc: PipelineDoc | null | undefined): string | null {
  return interiorNodes(doc?.view).find((n) => n.kind === "measurement")?.id ?? null;
}

export interface OriginPrompt {
  fields: Record<string, unknown>;
  version: string;
  // A node running several prompts shows the first and SAYS there are more.
  count: number;
}

// The floor under a never-mutated prompt: a searchpoint carries only the evolved DELTA.
// Lowest version wins, sorted numerically (`"10"` sorts before `"2"` as a string).
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
