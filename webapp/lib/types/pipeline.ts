// The Optimizer card's own types; graph shapes are re-exported from the generated wire types.

export type {
  OptimizerPipelineResponse as PipelineDoc,
  PipelineView,
  PipelineViewEdge,
  PipelineViewNode,
} from "@/lib/api";

// ONE record per node kind: label, caption and CSS suffix stay one closed set, the server's
// (`pipeline_schema.py::PipelineViewNode.kind`).
const NODE_KINDS: Record<string, { label: string; role: string }> = {
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

// Absent kind resolves to `tool`, the producer's own fallback (`pipeline_parsing.py::_derive_node_kind`).
export function nodeKind(kind: string | undefined): {
  label: string;
  role: string;
  cls: string;
} {
  const key = kind || "tool";
  return { ...(NODE_KINDS[key] ?? { label: key, role: "Pipeline node." }), cls: `kind-${key}` };
}

// An unresolved read is "…", never "idle", which would claim the node never fired.
export function nodeSubLabel(kind: string, model: string | null, loading: boolean): string {
  if (kind === "io") return "";
  if (kind === "measurement") return nodeKind(kind).label;
  if (model) return model;
  return loading ? "…" : "idle";
}

// As written by AuditTrailProjection._handle_llm_call; shared by `current_round.nodes` and
// `round_NNNN.json::nodes`.
export interface NodeDataLike {
  model?: string;
  duration_s?: number;
  round?: number;
  timestamp?: string;
  usage?: { input?: number; output?: number; reasoning?: number };
  input?: { template_name?: string };
  output?: { candidates?: { idx?: number; stats?: Record<string, unknown> }[] };
}
