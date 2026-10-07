"use client";
// The criterion the viewed cycle RUNS under, and the scoring mask's seed from it. `lib/lineage.tsx`
// owns the one `useScoringMaskSeed` call: a late seed costs an unmasked `?lens=` refetch.

import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { servedMask, setScoringMask, useScoringMask, type ScoringMask } from "@/lib/scoring-mask";

export interface ServedCriterion {
  // `null` until the cycle's run init stamps its formula.
  formula: string | null;
  mask: ScoringMask;
  // The level each anchored dial was locked at when the origin was measured; `null` where the
  // formula is not the anchored shape.
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

export function useScoringMaskSeed(): void {
  const { cycleId } = useWorkspace();
  const { formula, mask } = useServedCriterion();
  const { seededForCycle } = useScoringMask();

  // Render-phase seed, guarded by the module-level `seededForCycle` so a remount never re-seeds.
  if (cycleId && formula != null && seededForCycle !== cycleId) {
    setScoringMask({ mask, seededForCycle: cycleId });
  }
}
