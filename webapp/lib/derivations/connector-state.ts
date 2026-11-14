// A null `health` probe means not probed yet ("probing"), never down.

import {
  type BackendHealthResponse,
  type CampaignPipelineResponse,
  BACKEND_REACHABILITY_INFO,
} from "@/lib/api/types";
import type { NodeSchemaReading } from "@/lib/types";

interface ConnectorReachability {
  reachable: boolean;
  stateCls: "live" | "offline" | "idle";
  stateLabel: string;
}

export function connectorReachability(health: BackendHealthResponse | null): ConnectorReachability {
  const reachable = health?.status === "live";
  return {
    reachable,
    stateCls: reachable ? "live" : health ? "offline" : "idle",
    stateLabel: health ? BACKEND_REACHABILITY_INFO[health.status].label : "probing…",
  };
}

// The node rows a pipeline's body serves for one scope; no body yet holds none.
export function nodeSchemaReading(
  served: CampaignPipelineResponse | null,
  scope: "target" | "optimizer",
): Omit<NodeSchemaReading, "read"> {
  const rows = scope === "optimizer" ? served?.optimizer_setup : served;
  return {
    config: rows?.node_config_schema ?? null,
    output: rows?.node_output_schema ?? null,
    isSingleNode: scope === "target" && !!served?.is_single_node,
  };
}
