// A null `health` probe means not probed yet ("probing"), never down.

import {
  type BackendHealthResponse,
  type CampaignPipelineResponse,
  BACKEND_REACHABILITY_INFO,
} from "@/lib/api/types";
import { shownData } from "@/lib/hooks/useRead";
import type { ReadResult } from "@/lib/read-cache";
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
      isSingleNode: false,
    };
  }
  return {
    read,
    config: served?.node_config_schema ?? null,
    output: served?.node_output_schema ?? null,
    isSingleNode: !!served?.is_single_node,
  };
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
