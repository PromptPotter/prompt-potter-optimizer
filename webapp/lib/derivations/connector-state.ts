// A null `health` probe means not probed yet ("probing"), never down.

import type { BackendHealthResponse } from "@/lib/api";
import { BACKEND_REACHABILITY_INFO } from "@/lib/api/types.generated";
import { shownData, type ReadResult } from "@/lib/hooks/useRead";
import type { PipelineStatus } from "@/lib/types";

// A failed revalidation keeps the body it painted over, so a failure is a read with nothing to show.
export function pipelineReadStatus(read: ReadResult<unknown>): PipelineStatus {
  if (read.status === "idle") return "unbound";
  if (shownData(read) !== null) return "ok";
  return read.status === "failed" ? read.failure : "loading";
}

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
