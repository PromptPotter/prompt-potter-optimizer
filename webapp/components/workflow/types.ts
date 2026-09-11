// The `Optimizer` card's own types — the `/optimizer-pipeline` envelope and the node-kind
// vocabulary every graph surface reads. The graph shapes themselves are re-exported from
// the generated wire types, never mirrored here.

export type {
  OptimizerPipelineResponse as PipelineDoc,
  PipelineView,
  PipelineViewEdge,
  PipelineViewNode,
} from "@/lib/api";

// ONE record per node kind. Its three projections — the chip label, the sentence under a
// node's header, and the CSS suffix — are one closed set; hold them apart and a caller
// styles a kind another caller captions differently. The set is the server's
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

// Absent kind resolves to `tool` — the PRODUCER's own fallback
// (`pipeline_parsing.py::_derive_node_kind`), so the browser cannot name it something the
// server would not.
export function nodeKind(kind: string | undefined): {
  label: string;
  role: string;
  cls: string;
} {
  const key = kind || "tool";
  return { ...(NODE_KINDS[key] ?? { label: key, role: "Pipeline node." }), cls: `kind-${key}` };
}

// What a node's dot says UNDER its name. `measurement` runs no model and says what it is
// instead; an unresolved read is "…" rather than "idle", which would claim a node has never
// fired when the answer has simply not arrived yet.
export function nodeSubLabel(kind: string, model: string | null, loading: boolean): string {
  if (kind === "io") return "";
  if (kind === "measurement") return nodeKind(kind).label;
  if (model) return model;
  return loading ? "…" : "idle";
}

// One node block as written by AuditTrailProjection._handle_llm_call
// (promptpotter/infrastructure/projections/audit_trail.py). Shared by
// dashboard.json::current_round.nodes and round_NNNN.json::nodes.
export interface NodeDataLike {
  model?: string;
  duration_s?: number;
  round?: number;
  timestamp?: string;
  usage?: { input?: number; output?: number; reasoning?: number };
  input?: { template_name?: string };
  output?: { candidates?: { idx?: number; stats?: Record<string, unknown> }[] };
}
