"use client";
// Single source for "which round the round-scoped surfaces display".
//
// The round axis is a legitimate twin — operator INTENT (`selection.round`,
// null = follow the live in-flight round) over the DATA fact (`roundOf(dash)`).
// Those two stay distinct, but the *derived* effective round must live in ONE
// place: every round-scoped surface used to re-implement `selectedRound ??
// roundOf(dash)` inline, so the samples, score-frequency, and per-candidate
// fitness surfaces could each disagree on the active round. This collapses that
// derivation, so they can't.

import { useMemo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import { availableRounds } from "@/lib/derivations";

export interface EffectiveRound {
  // The round to display: the explicit pick, else the live in-flight round,
  // else (frozen/completed cycle with nothing in flight) the most recent
  // completed round so the round-scoped surfaces show real data, not a blank.
  round: number | null;
  // True when the surfaces are showing the live in-flight round (no explicit
  // pick, or the pick IS the live round). False when there is no live round
  // (frozen cycle) or the pick is a past round.
  isLiveView: boolean;
}

export function useEffectiveRound(): EffectiveRound {
  const { dash, isLive } = useDashboard();
  const { round: selectedRound } = useSelection();
  // Ride the canonical round axis so the effective round can't disagree with
  // the live-round pill surfaces: `live` is the in-flight round ONLY when the
  // run is actually live AND that round hasn't closed — NOT just `current_round.round`,
  // which lingers on a terminal/interrupted cycle and pointed the round-scoped
  // surfaces at a dead round (no `rounds[]` entry, no round file → blank/hang).
  const { completed, live: liveRound } = useMemo(
    () => availableRounds(dash, isLive),
    [dash, isLive],
  );
  // A frozen/completed cycle has no live round yet carries completed rounds —
  // fall to the latest (axis is ascending) so surfaces don't blank.
  const lastCompleted = completed.length ? completed[completed.length - 1] : null;
  return {
    round: selectedRound ?? liveRound ?? lastCompleted,
    isLiveView:
      liveRound != null && (selectedRound == null || selectedRound === liveRound),
  };
}
