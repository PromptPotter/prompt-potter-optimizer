// A null `health` probe means not probed yet ("probing"), never down.

import type {
  BackendHealthResponse,
  CampaignPipelineResponse,
  PipelineViewNode,
} from "@/lib/api";
import { BACKEND_REACHABILITY_INFO } from "@/lib/api/types.generated";
import { shownData, type ReadResult } from "@/lib/hooks/useRead";
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

type SchemaScope = "target" | "optimizer";

const READINGS: Record<SchemaScope, WeakMap<ReadResult<CampaignPipelineResponse>, NodeSchemaReading>> =
  { target: new WeakMap(), optimizer: new WeakMap() };

function readingOf(
  read: ReadResult<CampaignPipelineResponse>,
  scope: SchemaScope,
): NodeSchemaReading {
  const served = shownData(read);
  if (scope === "optimizer") {
    const setup = served?.optimizer_setup;
    return {
      read,
      config: setup?.node_config_schema ?? null,
      output: setup?.node_output_schema ?? null,
      singleNode: null,
    };
  }
  return {
    read,
    config: served?.node_config_schema ?? null,
    output: served?.node_output_schema ?? null,
    singleNode: served ? served.single_node : null,
  };
}

// The node a one-call editor opens: the served chain's one node as the view draws it, never the
// view's first, which is a phase drawn ahead of the chain.
export function oneCallNode(
  pipeline: Pick<CampaignPipelineResponse, "single_node" | "view">,
): PipelineViewNode | undefined {
  const { single_node: node, view } = pipeline;
  return node === null ? undefined : view?.nodes.find((n) => n.id === node);
}

// The node rows a pipeline read serves for one scope, beside the read itself: a pending read is
// never an empty node. Derived once per read, so every consumer holds the one identity.
export function nodeSchemaReading(
  read: ReadResult<CampaignPipelineResponse>,
  scope: SchemaScope = "target",
): NodeSchemaReading {
  const memo = READINGS[scope];
  let reading = memo.get(read);
  if (!reading) {
    reading = readingOf(read, scope);
    memo.set(read, reading);
  }
  return reading;
}
