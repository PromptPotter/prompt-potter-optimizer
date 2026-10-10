"use client";

import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { servedMask, setScoringMask, useScoringMask, type ScoringMask } from "@/lib/scoring-mask";

export interface ServedCriterion {
  formula: string | null;
  mask: ScoringMask;
  // `null` where the formula is not the anchored shape.
  anchors: Readonly<Record<string, number>> | null;
}

export function useServedCriterion(): ServedCriterion {
  const { dash } = useCycleStream();
  const formula = dash?.composite_fitness_formula ?? null;
  const weights = dash?.composite_fitness_weights ?? null;
  const anchors = dash?.composite_fitness_anchors ?? null;
  return useMemo(
    () => ({ formula, mask: servedMask(formula, weights), anchors }),
    [formula, weights, anchors],
  );
}

// Called once, by `lib/lineage.tsx`: a late seed costs an unmasked `?lens=` refetch.
export function useScoringMaskSeed(): void {
  const { cycleId } = useWorkspace();
  const { formula, mask } = useServedCriterion();
  const { seededForCycle } = useScoringMask();

  if (cycleId && formula != null && seededForCycle !== cycleId) {
    setScoringMask({ mask, seededForCycle: cycleId });
  }
}
