"use client";
// The one answer to "which round do the round-scoped surfaces display".

import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { useSelection } from "@/lib/SelectionContext";
import { availableRounds } from "@/lib/derivations";

interface EffectiveRound {
  round: number | null;
  isLiveView: boolean;
}

export function useEffectiveRound(): EffectiveRound {
  const { dash, isLive } = useCycleStream();
  const { round: selectedRound } = useSelection();
  // Never bare `current_round.round`: it lingers on a stopped cycle, naming a round with no file.
  const { completed, live: liveRound } = useMemo(
    () => availableRounds(dash, isLive),
    [dash, isLive],
  );
  const lastCompleted = completed.at(-1) ?? null;
  return {
    round: selectedRound ?? liveRound ?? lastCompleted,
    isLiveView:
      liveRound != null && (selectedRound == null || selectedRound === liveRound),
  };
}
