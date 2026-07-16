// Quiet per-round degradation notices — the webapp twin of the CLI's yellow
// "round degraded" line (`presentation/views/live/phase.py`). The loud
// cross-tab banner (`critical-alert.ts`) only surfaces the `critical` grade; a
// `degraded` round (transient backend noise on an otherwise-sound pipeline) is
// real and operator-relevant but NOT abort-worthy, so it stays quiet — an amber
// chip per round, never a banner. Without this the `degraded` grade was
// CLI-visible and webapp-invisible, breaking graded-surfacing parity.
//
// Pure + reader-side: reads the backend-computed `health` verdict off each round
// summary and never recomputes it (R-36). Sits in the Vitest derivation scope.

import type { DegradationHealth } from "@/lib/api/types";
import { fmtPct0 } from "@/lib/format";
import type { DashboardSnapshot } from "@/lib/poll";

// The backend-computed health verdict for one round, typed off the generated
// mirror — the one adapter every consumer reads through (gate decision,
// degraded notices, critical banner). Never re-parse `rounds[].health` loose.
export function roundHealthAt(
  dash: DashboardSnapshot | null,
  round: number,
): DegradationHealth | null {
  return dash?.rounds?.find((r) => r.round === round)?.health ?? null;
}

export interface DegradedRoundNotice {
  round: number;
  // One-line reason, e.g. "transient noise on entity_profiling" or
  // "under-probed origin (wide CI)". Built from the verdict's structured fields.
  detail: string;
}

function noticeDetail(
  reasons: string[],
  dominantNode: string | null,
  degradedRate: number,
): string {
  if (reasons.includes("untested")) {
    return "under-probed — too few samples for a confident read";
  }
  const where = dominantNode ? ` on ${dominantNode}` : "";
  return `transient backend noise${where} on ${fmtPct0(degradedRate)} of samples — fine to keep going`;
}

// Rounds the backend graded `degraded`, oldest→newest. `critical` is owned by
// the banner; `healthy` and unmeasured (`null`) rounds yield nothing.
export function degradedRoundNotices(dash: DashboardSnapshot | null): DegradedRoundNotice[] {
  const out: DegradedRoundNotice[] = [];
  for (const r of dash?.rounds ?? []) {
    if (r.health?.grade !== "degraded") continue;
    out.push({
      round: r.round,
      detail: noticeDetail(r.health.reasons, r.health.dominant_node, r.health.degraded_rate),
    });
  }
  out.sort((a, b) => a.round - b.round);
  return out;
}
