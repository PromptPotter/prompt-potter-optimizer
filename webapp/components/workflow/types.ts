// Shared workflow types — consumed by WorkflowCanvas (optimizer L1/L2/L3
// graph) and TargetPipelineHero (chat-pane connector graph). The wire shape
// mirrors PipelineView in promptpotter/domain/pipeline_schema.py — keep them
// in sync.

export interface PipelineViewNode {
  id: string;
  label: string;
  // "io" | "llm" | "tool" | "retriever" | "cache" | "measurement" | "phase"
  kind?: string;
}

export interface PipelineViewEdge {
  from: string;
  to: string;
  // "forward" | "loop" | "directive" | "escalate"
  kind?: string;
  label?: string;
}

export interface PipelineView {
  nodes: PipelineViewNode[];
  edges: PipelineViewEdge[];
}

export interface PipelineDoc {
  view?: PipelineView;
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

// One node block as written by AuditTrailView._handle_llm_call
// (promptpotter/infrastructure/projections/audit_trail.py). Shared by
// dashboard.json::current_round.nodes and round_NNNN.json::nodes.
export interface NodeDataLike {
  model?: string;
  duration_s?: number;
  round?: number;
  timestamp?: string;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  input?: { template_name?: string };
  output?: { candidates?: { idx?: number; stats?: Record<string, unknown> }[] };
}
