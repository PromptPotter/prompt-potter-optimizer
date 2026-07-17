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

// A round is live only while it is the in-flight round AND has not yet
// closed into `dash.rounds[]`. The round counter advances at scoring/close,
// so `current_round.round` lingers on an already-closed round number between
// a round closing and the next round scoring (and after an interrupt during
// next-round prep). Equality alone would then misroute a closed round to the
// in-flight projection — which by then holds the *next* round's partial prep,
// not the closed round's data. The `closed` check is the half topology can't
// see. NOTE this asks a DIFFERENT question than `round-axis`/the candidate
// spine (which gate on `closedRoundNumbers` — closed *with fitness data*):
// here "closed" = "the round file is on disk", and the round file is written
// at every round boundary, including an empty L2/L3-terminal round. So this
// stays an unfiltered presence check over `dash.rounds[]` — an empty closed
// round is still historical (read its file, not live `dash`).
export function isLiveRound(dash: DashboardSnapshot | null, round: number | null): boolean {
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
