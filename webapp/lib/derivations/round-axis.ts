// The single round-axis reader for every "advertise a live round" surface
// (RoundTabsStrip's pill, WorkflowCanvas's "(live)" picker option). Pins the
// contract: `completed` is ascending closed rounds; `live` is the in-flight
// round number ONLY when the optimizer is actually running AND that round
// hasn't already closed into `dash.rounds[]`.
//
// Topology alone (`roundOf(dash) ∉ completed`) can't see a stop — a run
// halted mid-round never closes that round, so the round number lingers in
// `current_round` forever. `isLive` (poll.tsx, the single liveness gate)
// is the other half of the predicate: when it's false there is no live
// round to advertise, regardless of topology. Both pill surfaces ride this
// so they cannot disagree about whether a round is live.

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { RoundAxis } from "@/lib/types";

export function availableRounds(
  dash: DashboardSnapshot | null,
  isLive: boolean,
): RoundAxis {
  const completed = (dash?.rounds ?? [])
    .map((r) => r.round)
    .slice()
    .sort((a, b) => a - b);
  const liveRound = roundOf(dash);
  const completedSet = new Set(completed);
  const live =
    isLive && liveRound != null && !completedSet.has(liveRound)
      ? liveRound
      : null;
  return { completed, live };
}
