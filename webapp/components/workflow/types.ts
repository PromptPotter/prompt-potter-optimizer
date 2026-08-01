// Shared workflow types — consumed by WorkflowCanvas (optimizer L1/L2/L3
// graph) and TargetPipelineHero (chat-pane connector graph). The wire shape
// mirrors PipelineView in promptpotter/domain/pipeline_schema.py — keep them
// in sync.

import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api";

export interface PipelineViewNode {
  id: string;
  label: string;
  // "io" | "llm" | "tool" | "retriever" | "cache" | "measurement" | "phase"
  kind?: string;
}

// Human label for a PipelineViewNode.kind — the ONE kind→label map (the node
// detail badge and the canvas sublabel were spelling "system step" separately).
export function nodeKindLabel(kind: string | undefined): string {
  switch (kind) {
    case "llm":
      return "LLM";
    case "measurement":
      return "system step";
    case "io":
      return "I/O";
    default:
      return kind ?? "";
  }
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
  // Per-node typed config surface (model / provider / reasoning_effort / …), served
  // by `/optimizer-pipeline` so the canvas node-detail renders the optimizer's own
  // knobs through the canonical config element instead of a hand-rolled chip strip +
  // JSON dump. Read-only there (the optimizer's own pipeline is edited by hand).
  node_config_schema?: Record<string, NodeConfigParam[]>;
  node_output_schema?: Record<string, NodeOutputSchema | null>;
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
