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
  const { dash, dashRound } = useDashboard();
  const { round: selectedRound } = useSelection();
  // A frozen/completed cycle can have no in-flight round (`dashRound` null)
  // yet carry completed rounds — fall to the latest so surfaces don't blank.
  const lastCompleted = useMemo(() => {
    const rounds = dash?.rounds ?? [];
    return rounds.length ? Math.max(...rounds.map((r) => r.round)) : null;
  }, [dash?.rounds]);
  return {
    round: selectedRound ?? dashRound ?? lastCompleted,
    isLiveView:
      dashRound != null && (selectedRound == null || selectedRound === dashRound),
  };
}
