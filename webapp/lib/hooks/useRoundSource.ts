"use client";
// The single home for the "live round → dashboard.json, historical round →
// round_NNNN.json" source guard. `round_NNNN.json` is written at the round
// boundary, so the live round HAS no file — fetching one always 404s, and the
// live data is already in `dash`.
//
// The guard is here so it cannot be forgotten: the fetch idles on the live round,
// and `isLive` tells the caller to read `dash` rather than `doc`. It SELECTS one
// source — it never merges them
// (the no-stitch rule in `webapp/CLAUDE.md` "Display-data sources").
//
// Peers: `useRoundFile` (raw fetch, now only reached through here for live-
// aware callers), `useRoundCandidates` (the live/historical *candidate*
// spine this hook's callers branch on).

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundFile, type UseRoundFileState } from "@/lib/hooks/useRoundFile";
import type { CyclePath } from "@/lib/ids";

interface UseRoundSourceState extends UseRoundFileState {
  // True when `round` is the in-flight round — the caller reads live state
  // from `dash`, and `doc` stays null (no fetch was issued).
  isLive: boolean;
}

// Does a round FILE exist for this round yet — the one question this module asks, and the
// reason it is not plain equality against `current_round.round`: `rounds/round_NNNN.json` is
// written at round close, so a closed round has a file to read even while the live block still
// names it. An unfiltered presence check over `dash.rounds[]` on purpose — "closed" here means
// "on disk", and the round file is written at every boundary including an empty
// L2/L3-terminal round, which `closedRoundNumbers` (closed *with fitness data*) excludes.
//
// Private: `useRoundNodes` used to pick the AUDIT twin with it, a different question wearing
// this answer. That resolver selects on `current_round.round` now.
function isLiveRound(dash: DashboardSnapshot | null, round: number | null): boolean {
  const closed = (dash?.rounds ?? []).some((r) => r.round === round);
  return round != null && round === roundOf(dash) && !closed;
}

export function useRoundSource(
  path: CyclePath | null,
  round: number | null,
  dash: DashboardSnapshot | null,
): UseRoundSourceState {
  const isLive = isLiveRound(dash, round);
  // Idle the round-file fetch on the live round — its file doesn't exist
  // until round close, and the data is already in `dash`.
  const file = useRoundFile(isLive ? null : path, round);
  return { ...file, isLive };
}
