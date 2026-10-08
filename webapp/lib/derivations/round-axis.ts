// The one round-axis reader. `live` needs `isLive` besides topology: a run halted mid-round never
// closes that round, so its number lingers in `current_round`.

import { measuredRoundNumbers } from "./round-candidates";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { RoundAxis } from "@/lib/types";

export function availableRounds(
  dash: DashboardSnapshot | null,
  isLive: boolean,
): RoundAxis {
  // Excludes empty rows a round that measured nothing closes, else `useEffectiveRound` falls back to one as
  // `lastCompleted` and the round-scoped surfaces blank.
  const measured = measuredRoundNumbers(dash);
  const completed = [...measured];
  const liveRound = roundOf(dash);
  const live =
    isLive && liveRound != null && !measured.has(liveRound) ? liveRound : null;
  return { completed, live };
}
