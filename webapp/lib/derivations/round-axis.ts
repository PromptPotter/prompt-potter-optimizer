// The one round-axis reader. `live` needs `isLive` besides topology: a run halted mid-round never
// closes that round, so its number lingers in `current_round`.

import { closedRoundNumbers } from "./round-candidates";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { RoundAxis } from "@/lib/types";

export function availableRounds(
  dash: DashboardSnapshot | null,
  isLive: boolean,
): RoundAxis {
  // Excludes empty L2/L3-terminal rows, else `useEffectiveRound` falls back to one as
  // `lastCompleted` and the round-scoped surfaces blank.
  const closed = closedRoundNumbers(dash);
  const completed = [...closed].sort((a, b) => a - b);
  const liveRound = roundOf(dash);
  const live =
    isLive && liveRound != null && !closed.has(liveRound) ? liveRound : null;
  return { completed, live };
}
