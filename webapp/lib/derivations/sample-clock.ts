// What CLOCK one measured row shows — the browser's peer of `domain/scoring.py::shown_seconds`
// and of `DashboardSample.shown_s`. One per runtime, never one per renderer: both the round-wide
// samples table and the searchpoint drill-in render the same row through `SampleRowItem`, and a
// pick made at either would be a second answer to one question.
//
// A row carries two clocks and they mean different things. `elapsed_s` is what it occupied NOW —
// a true `0.0` on a replay, which occupied nothing — and `cost_s` is what producing it took when
// it was measured, summed off the per-node timings that survive the cache stamp. So a replayed
// cell reads as what it cost, and a fresh one as what it took, and neither is ever `0.0s` for the
// wrong reason.

import type { SampleRow } from "@/lib/types";

// Both clocks live inside `pipeline_data` on a round document; neither has a top-level twin, so a
// closed round must be read here rather than off the row. Peer of `recorded_cost_s`: an EMPTY map
// stays null rather than summing to `0.0`, which would report an unpriced cell as a free one.
export function foldStepTimings(raw: unknown): number | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  let total: number | null = null;
  for (const v of Object.values(raw as Record<string, unknown>)) {
    if (typeof v === "number") total = (total ?? 0) + v;
  }
  return total;
}

// The seconds a per-row clock column shows. `null` where the row recorded none at all, which stays
// distinct from a replay's real `0.0`: one never reached the pipeline, the other occupied no clock.
export function shownSeconds(row: SampleRow): number | null {
  return row.cached ? row.cost_s : row.elapsed_s;
}
