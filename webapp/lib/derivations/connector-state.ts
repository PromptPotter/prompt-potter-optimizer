// The one connector reachability verdict — the ConnectorInspector LED and CriticalAlertBanner
// must agree. A null `health` probe means not probed yet ("probing"), never down.

import type { BackendHealthResponse } from "@/lib/api";
import type { PipelineStatus } from "@/lib/types";

/** Unbound outranks in flight, which outranks failed — for every surface resolving a schema
 *  outside `ConnectorProvider` (`frontend-surface-contract.md::I1`). */
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

// pp-self has no registered backend, roster or llm/tool `view`, so HTTP-shaped panels branch on
// this rather than read as misconfigured; its per-sample data lives in the inner cycle.
export function isSelfOptimization(backendType: string | null | undefined): boolean {
  return backendType === "promptpotter";
}
