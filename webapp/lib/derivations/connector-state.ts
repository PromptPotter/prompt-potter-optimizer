// A null `health` probe means not probed yet ("probing"), never down.

import type { BackendHealthResponse } from "@/lib/api";
import type { PipelineStatus } from "@/lib/types";

export function pipelineReadStatus(read: {
  bound: boolean;
  loading: boolean;
  failed: boolean;
}): PipelineStatus {
  if (!read.bound) return "unbound";
  if (read.loading) return "loading";
  return read.failed ? "error" : "ok";
}

export interface ConnectorReachability {
  reachable: boolean;
  down: boolean;
  stateCls: "live" | "offline" | "idle";
  stateLabel: string;
}

export function connectorReachability(health: BackendHealthResponse | null): ConnectorReachability {
  const reachable = health?.status === "live";
  const down = health != null && !reachable;
  return {
    reachable,
    down,
    stateCls: reachable ? "live" : down ? "offline" : "idle",
    stateLabel: reachable ? "reachable" : down ? "unreachable" : "probing…",
  };
}
