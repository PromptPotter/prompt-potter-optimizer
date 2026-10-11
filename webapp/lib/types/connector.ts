import type {
  BackendHealthResponse,
  BackendResponse,
  CapabilityMenu,
  NestedPipelineRef,
  NodeConfigParam,
  NodeOutputSchema,
  NodeReach,
} from "@/lib/api";
import type { PipelineView } from "./pipeline";

export type PipelineStatus = "unbound" | "loading" | "ok" | "error";

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
  optimizerKnobs: Record<string, Record<string, unknown>> | null;
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
  nests: NestedPipelineRef | null;
}
