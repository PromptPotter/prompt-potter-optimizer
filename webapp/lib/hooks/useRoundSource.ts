"use client";
// The single home for the "live round → dashboard.json, historical round →
// round_NNNN.json" source guard. `round_NNNN.json` is written at the round
// boundary, so the live round HAS no file — fetching one always 404s, and the
// live data is already in `dash`.
//
// The guard is here so it cannot be forgotten: the fetch idles on the live round,
// and `live` tells the caller to read `dash` rather than `doc`. It SELECTS one
// source — it never merges them
// (the no-stitch rule in `webapp/CLAUDE.md` "Display-data sources").
//
// Takes the path, because one caller reads a cycle other than the viewed one
// (`SteerForkPanel`); every viewed-cycle surface reads `useRoundRows`.

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundFile, type RoundFileState } from "@/lib/hooks/useRoundFile";
import type { CyclePath } from "@/lib/ids";
import type { RoundResult } from "@/lib/types";

interface RoundSourceState extends RoundFileState<RoundResult> {
  // True when `round` is the in-flight round — the caller reads live state
  // from `dash`, and `doc` stays null (no fetch was issued).
  live: boolean;
}

// Does a round FILE exist for this round yet — the one question this module asks, and the
// reason it is not plain equality against `current_round.round`: `rounds/round_NNNN.json` is
// written at round close, so a closed round has a file to read even while the live block still
// names it. An unfiltered presence check over `dash.rounds[]` on purpose — "closed" here means
// "on disk", and the round file is written at every boundary including an empty
// L2/L3-terminal round, which `closedRoundNumbers` (closed *with fitness data*) excludes.
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
  // Idle the round-file fetch on the live round — its file doesn't exist
  // until round close, and the data is already in `dash`.
  const file = useRoundFile(live ? null : path, round);
  return { ...file, live };
}
