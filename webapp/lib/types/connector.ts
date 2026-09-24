// The connector state: one join of `/backends`, the campaign's resolved pipeline and the live
// per-node observations, made in `lib/hooks/useConnector.ts` and nowhere else.

import type {
  BackendHealthResponse,
  BackendResponse,
  ModelCapability,
  NestedPipelineRef,
  NodeConfigParam,
  NodeOutputSchema,
  NodeReach,
} from "@/lib/api";
import type { NodeDataLike, PipelineView } from "@/components/workflow";

export type PipelineStatus = "unbound" | "loading" | "ok" | "error";

export interface ConnectorView {
  connector: string | null;
  backendType: string | null;
  view: PipelineView | null;
  // Never infer the read's state from `view` being null.
  pipelineStatus: PipelineStatus;
  active: BackendResponse | null;
  others: BackendResponse[];
  baseUrl: string | null;
  isTls: boolean | null;
  currentNodes: Record<string, NodeDataLike>;
  isLive: boolean;
  // `dashboard.json::state`; the target LLM is called only during "scoring" and "origin".
  phase: string | null;
  // Real reachability, distinct from `isLive` (is the optimizer scoring through it right now).
  health: BackendHealthResponse | null;
  nodeConfigSchema: Record<string, NodeConfigParam[]> | null;
  // Null is UNKNOWN: an unread node must not draw as shut.
  reach: Record<string, NodeReach> | null;
  // Served, not counted off the config rows: those cover every DECLARED node, and a check-in
  // declares its connector's whole pipeline while running one step.
  isSingleNode: boolean;
  nodeOutputSchema: Record<string, NodeOutputSchema | null> | null;
  // Empty is UNKNOWN (an unresolved catalogue), never "this model supports nothing".
  modelCapabilities: Record<string, ModelCapability>;
  // Served, never guessed from a node name.
  nests: NestedPipelineRef | null;
}
