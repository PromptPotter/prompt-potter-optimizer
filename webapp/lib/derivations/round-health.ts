// Quiet per-round degradation notices — the webapp twin of the CLI's yellow
// "round degraded" line (`presentation/views/live/phase.py`). The loud
// cross-tab banner (`critical-alert.ts`) only surfaces the `critical` grade; a
// `degraded` round is real and operator-relevant but NOT abort-worthy, so it
// stays quiet — an amber chip per round, never a banner.
//
// Pure + reader-side: reads the backend-computed `health` verdict off each round
// summary and never recomputes it. Sits in the Vitest derivation scope.

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

// WHY a round graded below healthy, in words. Keyed on the generated union, so a cause the
// backend adds is a compile error here rather than a raw enum member rendered at an operator: the
// gate card printed `verdict.cause` verbatim, and an origin held by one upstream 429 explained
// itself to the operator with the bare word "holed".
export const HEALTH_CAUSE_LABEL: Record<NonNullable<DegradationHealth["cause"]>, string> = {
  origin_unmeasured: "the origin was not measured",
  origin_incomplete: "the origin is missing cells",
  backend_unreachable: "the backend was unreachable",
  structural: "a node failed structurally",
  unscoreable: "no extractable answer",
  holed: "cells returned no measurement",
  evidence_starved: "an enricher produced no evidence",
  structural_untested: "a structural failure with no clean prior",
  persistent: "degraded for several rounds running",
  degraded: "degraded on a share of samples",
};

export interface DegradedRoundNotice {
  round: number;
  // One-line reason, e.g. "transient noise on entity_profiling" or
  // "under-probed origin (wide CI)". Built from the verdict's structured fields.
  detail: string;
}

// Rounds the backend graded `degraded`, oldest→newest. `critical` is owned by
// the banner; `healthy` and unmeasured (`null`) rounds yield nothing.
export function degradedRoundNotices(dash: DashboardSnapshot | null): DegradedRoundNotice[] {
  const out: DegradedRoundNotice[] = [];
  for (const r of dash?.rounds ?? []) {
    if (r.health?.grade !== "degraded") continue;
    out.push({
      round: r.round,
      // The verdict's own sentence. Composing one here got the structural/transient
      // split wrong, because the browser is not handed what decided the grade.
      detail:
        r.health.suggested_action ??
        `degraded on ${fmtPct0(r.health.degraded_rate)} of samples`,
    });
  }
  out.sort((a, b) => a.round - b.round);
  return out;
}
