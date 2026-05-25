// Round-axis reader for the round-tabs strip. Wraps the same
// `dash.rounds[]` + `roundOf(dash)` reads everyone else does, but
// pins the contract: `completed` is ascending closed rounds, `live`
// is the in-flight one if it isn't already closed.
//
// RoundTabsStrip is the sole consumer today; future surfaces that
// need "which rounds exist?" (e.g. a round-picker dropdown in
// OptimizerNodeDetail) should ride this too.

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { RoundAxis } from "@/lib/types/round";

export function availableRounds(dash: DashboardSnapshot | null): RoundAxis {
  const completed = (dash?.rounds ?? [])
    .map((r) => r.round)
    .slice()
    .sort((a, b) => a - b);
  const liveRound = roundOf(dash);
  const completedSet = new Set(completed);
  const live =
    liveRound != null && !completedSet.has(liveRound) ? liveRound : null;
  return { completed, live };
}
