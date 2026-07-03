// Single source of the connector reachability verdict. The ConnectorInspector LED
// (dot + footer) and the cross-tab CriticalAlertBanner are the two consumers —
// they MUST agree, so the predicate lives here once, not inlined in each.
//
// `health` is the `/backends/{id}/health` probe (`BackendHealth | null`):
//   null            → not probed yet / no backend resolved → "probing"
//   status==="live" → reachable                            → "live"
//   anything else   → unreachable                          → "down"

import type { BackendHealth } from "@/lib/api";

export interface ConnectorReachability {
  reachable: boolean;
  // The LED's red state and the banner's trigger — the single predicate.
  down: boolean;
  // CSS state class on the connector dot.
  stateCls: "live" | "offline" | "idle";
  // Short human label (popover header + LED aria-label).
  stateLabel: string;
}

export function connectorReachability(health: BackendHealth | null): ConnectorReachability {
  const reachable = health?.status === "live";
  const down = health != null && !reachable;
  return {
    reachable,
    down,
    stateCls: reachable ? "live" : down ? "offline" : "idle",
    stateLabel: reachable ? "reachable" : down ? "unreachable" : "probing…",
  };
}

// An L4 self-optimization (pp-self) unit: its `backend_type` is PromptPotter
// itself, not a TermNorm-shaped HTTP backend. The connector/pipeline/sample
// panels are built for the HTTP shape and degrade to misleading empty states
// ("no backend selected" / "no nodes loaded") for it, because pp-self has no
// registered backend, no `cache.json` roster, and no llm/tool `view`. Panels
// branch on THIS so they read as "self-optimization — drill into the inner run"
// instead of "misconfigured". The real per-sample data lives in the inner cycle.
export function isSelfOptimization(backendType: string | null | undefined): boolean {
  return backendType === "promptpotter";
}
