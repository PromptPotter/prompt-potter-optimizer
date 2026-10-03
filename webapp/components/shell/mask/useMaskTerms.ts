"use client";
// The cycle-scoped narrowing of the scoring mask. `lib/lineage.tsx` owns the one
// `useScoringMaskSeed` call: a late seed costs an unmasked `?lens=` refetch.

import { useMemo } from "react";
import type { CellTermMeta } from "@/lib/api/types.generated";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { identifiersInFormula, setScoringMask, termRows, useScoringMask } from "./scoring-mask";

export interface MaskTerms {
  rows: CellTermMeta[];
  inActive: Set<string>;
  // `default`: the active formula is not a weighted sum, so no coefficient is served.
  seeded: "realized" | "default";
}

interface TermFacts extends MaskTerms {
  cycleId: string | null;
  formula: string | null;
  servedWeights: Record<string, number> | null;
}

function useTermFacts(): TermFacts {
  const { cycleId } = useWorkspace();
  const { dash } = useCycleStream();
  const { mask } = useScoringMask();

  // The served `per_cell` formula — the vocabulary a lens is written in.
  const formula = dash?.composite_fitness_formula ?? null;
  // `null`: the formula is not a weighted sum (`domain/scoring.py::weighted_sum_weights`).
  const servedWeights = dash?.composite_fitness_weights ?? null;

  const inActive = useMemo(() => identifiersInFormula(formula), [formula]);
  const selected = mask.kind === "weights" ? mask.selected : null;

  const rows = useMemo(() => {
    const bucketOf = (r: CellTermMeta) => {
      if (inActive.has(r.name)) return 0;
      if (selected?.has(r.name)) return 1;
      return 2;
    };
    return termRows(Object.keys(servedWeights ?? {})).sort((a, b) => bucketOf(a) - bucketOf(b));
  }, [inActive, selected, servedWeights]);

  return {
    cycleId,
    formula,
    rows,
    inActive,
    servedWeights,
    seeded: servedWeights != null ? "realized" : "default",
  };
}

export function useMaskTerms(): MaskTerms {
  const { rows, inActive, seeded } = useTermFacts();
  return { rows, inActive, seeded };
}

export function useScoringMaskSeed(): void {
  const { cycleId, formula, rows, inActive, servedWeights } = useTermFacts();
  const { seededForCycle } = useScoringMask();

  // Render-phase seed, guarded by the module-level `seededForCycle` so a remount never re-seeds.
  if (cycleId && formula != null && seededForCycle !== cycleId) {
    const selected = servedWeights
      ? new Set(Object.keys(servedWeights))
      : new Set(rows.filter((r) => inActive.has(r.name)).map((r) => r.name));
    setScoringMask({
      mask: { kind: "weights", selected, weights: servedWeights ?? {} },
      seededForCycle: cycleId,
    });
  }
}
