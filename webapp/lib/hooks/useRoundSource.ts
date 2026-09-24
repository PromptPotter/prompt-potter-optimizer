"use client";
// The single "live round → `dash`, closed round → round_NNNN.json" guard: it SELECTS one source,
// never merges. Viewed-cycle surfaces read `useRoundRows`; this takes a path for any other cycle.

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundFile, type RoundFileState } from "@/lib/hooks/useRoundFile";
import type { CyclePath } from "@/lib/ids";
import type { RoundResult } from "@/lib/types";

interface RoundSourceState extends RoundFileState<RoundResult> {
  // Read `dash`; `doc` stays null.
  live: boolean;
}

// "Does a round FILE exist yet" — not equality with `current_round.round`, and not
// `closedRoundNumbers`, which drops the empty L2/L3-terminal rounds that still get a file.
export function isLiveRound(dash: DashboardSnapshot | null, round: number | null): boolean {
  const closed = (dash?.rounds ?? []).some((r) => r.round === round);
  return round != null && round === roundOf(dash) && !closed;
}

export function useRoundSource(
  path: CyclePath | null,
  round: number | null,
  dash: DashboardSnapshot | null,
): RoundSourceState {
  const live = isLiveRound(dash, round);
  const file = useRoundFile(live ? null : path, round);
  return { ...file, live };
}
