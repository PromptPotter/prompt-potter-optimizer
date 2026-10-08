// The Optimizer card's own types; graph shapes are re-exported from the generated wire types.

import type { PipelineViewNode } from "@/lib/api";

export type {
  OptimizerPipelineResponse as PipelineDoc,
  PipelineView,
  PipelineViewEdge,
  PipelineViewNode,
} from "@/lib/api";

// ONE record per node kind, TOTAL over the served set (`pipeline_schema.py::ViewKind`): a kind the
// server adds fails to compile here instead of drawing an uncaptioned node.
export type ViewKind = PipelineViewNode["kind"];

const NODE_KINDS: Record<ViewKind, { label: string; role: string }> = {
  llm: { label: "LLM", role: "LLM call — runs the prompt below against each query." },
  measurement: {
    label: "system step",
    role: "System step — runs a whole pipeline rather than a prompt.",
  },
  retriever: {
    label: "retriever",
    role: "Retriever — ranks candidates from the index by similarity. No prompt.",
  },
  tool: {
    label: "tool",
    role: "Tool — fetches external context (e.g. web search) for downstream nodes. No prompt.",
  },
  cache: { label: "cache", role: "Cache — short-circuits the pipeline on a known hit. No prompt." },
  io: { label: "I/O", role: "Pipeline terminal." },
};

export function nodeKind(kind: ViewKind): { label: string; role: string; cls: string } {
  return { ...NODE_KINDS[kind], cls: `kind-${kind}` };
}

// An unresolved read is "…", never "idle", which would claim the node never fired.
export function nodeSubLabel(kind: ViewKind, model: string | null, loading: boolean): string {
  if (kind === "io") return "";
  if (kind === "measurement") return nodeKind(kind).label;
  if (model) return model;
  return loading ? "…" : "idle";
}
