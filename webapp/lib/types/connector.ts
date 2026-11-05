import type {
  BackendHealthResponse,
  BackendResponse,
  CapabilityMenu,
  CellTerm,
  NestedPipelineRef,
  NodeConfigParam,
  NodeOutputSchema,
  NodeReach,
  OptimizerSetup,
  PipelineView,
} from "@/lib/api/types";
import type { ReadFailure } from "@/lib/read-cache";

// A failed read IS its `ReadFailure`, so every surface renders its sentence through `FailureLine`.
export type PipelineStatus = "unbound" | "loading" | "ok" | ReadFailure;

export interface NodeSchemaReading {
  // Never infer the read's state from a null map: an in-flight read and an empty node both hold no rows.
  status: PipelineStatus;
  config: Record<string, NodeConfigParam[]> | null;
  output: Record<string, NodeOutputSchema | null> | null;
  // Served (`is_single_node`), never counted off the rows: they cover every DECLARED node.
  isSingleNode: boolean;
}

export interface ConnectorView {
  connector: string | null;
  backendType: string | null;
  // served: `CampaignPipelineResponse.self_optimization`; false until the resolution lands.
  selfOptimization: boolean;
  optimizer: string | null;
  optimizerSetup: OptimizerSetup | null;
  view: PipelineView | null;
  schema: NodeSchemaReading;
  active: BackendResponse | null;
  others: BackendResponse[];
  baseUrl: string | null;
  isTls: boolean | null;
  // Real reachability, distinct from the stream's `isLive` (is the optimizer scoring through it now).
  health: BackendHealthResponse | null;
  // Null is UNKNOWN: an unread node must not draw as shut.
  reach: Record<string, NodeReach> | null;
  // Empty is UNKNOWN (an unresolved catalogue), never "this model supports nothing".
  modelCapabilities: CapabilityMenu;
  nests: NestedPipelineRef[];
  // served: `CampaignPipelineResponse.cell_terms`, the terms its connector adds to the core's.
  cellTerms: readonly CellTerm[];
}
