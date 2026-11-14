import { useMemo } from "react";
import { useViewedDashboard } from "@/lib/state/poll";
import { servedMask, type ScoringMask } from "@/lib/state/scoring-mask";

interface ServedCriterion {
  formula: string | null;
  mask: ScoringMask;
  // `null` where the formula is not the anchored shape.
  anchors: Readonly<Record<string, number>> | null;
}

export function useServedCriterion(): ServedCriterion {
  const { dash } = useViewedDashboard();
  const formula = dash?.composite_fitness_formula ?? null;
  const weights = dash?.composite_fitness_weights ?? null;
  const anchors = dash?.composite_fitness_anchors ?? null;
  return useMemo(
    () => ({ formula, mask: servedMask(formula, weights), anchors }),
    [formula, weights, anchors],
  );
}
