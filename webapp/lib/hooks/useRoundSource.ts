"use client";
// The single home for the "live round → dashboard.json, historical round →
// round_NNNN.json" source guard. `round_NNNN.json` is the immutable audit
// record of a *completed* round (AuditTrailView, round-boundary only); the
// in-flight round lives only in `dashboard.json`. So fetching the round file
// for the live round always 404s — and is unnecessary, since the live data is
// already in `dash`.
//
// Every deep-audit surface used to hand-roll this guard (`isLive = round ===
// roundOf(dash)` → pass `null` to `useRoundFile`) and two of them forgot it,
// fetching the live round's not-yet-written file → 404 + a degraded panel.
// This hook makes the guard impossible to forget: it idles the fetch on the
// live round and hands back `isLive` so the caller picks its live derivation
// from `dash`, else reads `doc`. It SELECTS one source — it never merges them
// (the no-stitch rule in `webapp/CLAUDE.md` "Display-data sources").
//
// Peers: `useRoundFile` (raw fetch, now only reached through here for live-
// aware callers), `roundCandidatesByRound` (the live/historical *candidate*
// derivation this hook's callers branch on).

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundFile, type UseRoundFileState } from "@/lib/hooks/useRoundFile";

export interface UseRoundSourceState extends UseRoundFileState {
  // True when `round` is the in-flight round — the caller reads live state
  // from `dash`, and `doc` stays null (no fetch was issued).
  isLive: boolean;
}

export function useRoundSource(
  campaignId: string | null,
  cycleId: string | null,
  round: number | null,
  dash: DashboardSnapshot | null,
): UseRoundSourceState {
  const liveRound = roundOf(dash);
  const isLive = round != null && liveRound != null && round === liveRound;
  // Idle the round-file fetch on the live round — its file doesn't exist
  // until round close, and the data is already in `dash`.
  const file = useRoundFile(campaignId, cycleId, isLive ? null : round);
  return { ...file, isLive };
}
