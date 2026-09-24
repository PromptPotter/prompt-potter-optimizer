// Twin of the CLI's "round degraded" line (`presentation/terminal/live/phase.py`). A `degraded`
// round is a quiet chip, never a banner — the banner (`critical-alert.ts`) owns `critical`.

import type { DegradationHealth } from "@/lib/api/types";
import { fmtPct0 } from "@/lib/format";
import type { DashboardSnapshot } from "@/lib/poll";

// The one adapter every consumer reads through; never re-parse `rounds[].health` loose.
export function roundHealthAt(
  dash: DashboardSnapshot | null,
  round: number,
): DegradationHealth | null {
  return dash?.rounds?.find((r) => r.round === round)?.health ?? null;
}

// Keyed on the generated union, so a cause the backend adds is a compile error here rather than
// a raw enum member on screen.
export const HEALTH_CAUSE_LABEL: Record<NonNullable<DegradationHealth["cause"]>, string> = {
  origin_unmeasured: "the origin was not measured",
  origin_incomplete: "the origin is missing cells",
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
  detail: string;
}

export function degradedRoundNotices(dash: DashboardSnapshot | null): DegradedRoundNotice[] {
  const out: DegradedRoundNotice[] = [];
  for (const r of dash?.rounds ?? []) {
    if (r.health?.grade !== "degraded") continue;
    out.push({
      round: r.round,
      // The verdict's own sentence: the browser is not handed what decided the grade.
      detail:
        r.health.suggested_action ??
        `degraded on ${fmtPct0(r.health.degraded_rate)} of samples`,
    });
  }
  out.sort((a, b) => a.round - b.round);
  return out;
}
