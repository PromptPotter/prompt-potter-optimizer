import { useMemo } from "react";
import { selectedCandidateOf, type SelectedCandidate } from "@/lib/types";
import { useWorkspace } from "@/lib/state/workspace";
import { useRound } from "./useRound";

// The hash's pick as its round serves it: null until that round's reading of the arm lands, so a
// reload resolves it with no second copy kept anywhere.
export function usePickedCandidate(): SelectedCandidate | null {
  const { viewed, pick } = useWorkspace();
  const round = useRound(viewed && pick ? viewed.path : null, pick?.round ?? null);
  const arm = pick ? (round.reading(pick.arm)?.arm ?? null) : null;
  const cycleId = viewed?.leaf.cycleId ?? null;
  return useMemo(
    () => (arm && cycleId ? selectedCandidateOf(cycleId, arm.round, arm.key, arm.label) : null),
    [arm, cycleId],
  );
}
