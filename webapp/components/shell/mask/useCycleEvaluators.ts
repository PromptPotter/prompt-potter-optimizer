"use client";
// The cycle-scoped narrowing of the scoring mask. `lib/lineage.tsx` owns the one
// `useScoringMaskSeed` call: a late seed costs an unmasked `?lens=` refetch.

import { useEffect, useMemo } from "react";
import { EVALUATOR_META } from "@/lib/api/types.generated";
import { liveCandidates, useCycleStream } from "@/lib/poll";
import { sortedRounds } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { targetNodeIds } from "@/lib/terms";
import { useWorkspace } from "@/lib/workspace";
import {
  buildRows,
  identifiersInFormula,
  setScoringMask,
  useScoringMask,
  type Row,
} from "./scoring-mask";

export interface CycleEvaluators {
  rows: Row[];
  inActive: Set<string>;
  // `default`: the active formula is not a weighted sum, so no coefficient is served.
  seeded: "realized" | "default";
}

interface EvaluatorFacts extends CycleEvaluators {
  cycleId: string | null;
  applicable: Set<string>;
  servedWeights: Record<string, number> | null;
}

function useEvaluatorFacts(): EvaluatorFacts {
  const { cycleId } = useWorkspace();
  const { dash } = useCycleStream();
  const { mask } = useScoringMask();
  const meta = EVALUATOR_META;

  // Mirrors `PipelineSchema.is_single_node`: a lone node carries no node-type-bound evaluator.
  const cv = useConnector();
  const singleNode = targetNodeIds(cv.view).length <= 1;

  // Keyed on `dash` alone, so the seed + prune guards below converge instead of looping setState.
  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of liveCandidates(dash)) {
      for (const k of Object.keys(c.evaluators)) set.add(k);
    }
    for (const h of sortedRounds(dash)) {
      for (const c of h.candidates) {
        for (const k of Object.keys(c.evaluators)) set.add(k);
      }
    }
    return set;
  }, [dash]);

  const isPrestaging = realApplicable.size === 0;

  const applicable = useMemo(() => {
    if (!isPrestaging) return realApplicable;
    const set = new Set<string>();
    for (const m of meta) set.add(m.name);
    return set;
  }, [isPrestaging, realApplicable, meta]);

  const compositeFormula = dash?.composite_fitness_formula ?? null;
  // `null`: the formula is not a weighted sum (`domain/scoring.py::weighted_sum_weights`).
  const servedWeights = dash?.composite_fitness_weights ?? null;

  const inActive = useMemo(() => {
    let parsed: Set<string> | null = compositeFormula
      ? identifiersInFormula(compositeFormula)
      : null;
    if (parsed == null) {
      parsed = new Set<string>();
      for (const c of liveCandidates(dash)) {
        for (const k of Object.keys(c.evaluators)) parsed.add(k);
      }
    }
    const out = new Set<string>();
    for (const k of parsed) if (applicable.has(k)) out.add(k);
    return out;
  }, [compositeFormula, dash, applicable]);

  const selected = mask.kind === "weights" ? mask.selected : null;

  const rows = useMemo(() => {
    const built = isPrestaging
      ? meta.map<Row>((m) => ({
          displayName: m.name,
          registryName: m.name,
          applicable: m.node_type == null || !singleNode,
          description: m.description,
          direction: m.direction,
        }))
      : buildRows(meta, realApplicable);
    const bucketOf = (r: Row) => {
      if (!r.applicable) return 3;
      if (inActive.has(r.displayName)) return 0;
      if (selected?.has(r.displayName)) return 1;
      return 2;
    };
    return built.slice().sort((a, b) => bucketOf(a) - bucketOf(b));
  }, [meta, realApplicable, inActive, selected, isPrestaging, singleNode]);

  return {
    cycleId,
    rows,
    inActive,
    applicable,
    servedWeights,
    seeded: servedWeights != null ? "realized" : "default",
  };
}

export function useCycleEvaluators(): CycleEvaluators {
  const { rows, inActive, seeded } = useEvaluatorFacts();
  return { rows, inActive, seeded };
}

export function useScoringMaskSeed(): void {
  const { cycleId, rows, inActive, applicable, servedWeights } = useEvaluatorFacts();
  const { mask, seededForCycle } = useScoringMask();
  const seeded = seededForCycle != null && seededForCycle === cycleId;

  // Render-phase seed, guarded by the module-level `seededForCycle` so a remount never re-seeds.
  if (cycleId && applicable.size > 0 && !seeded) {
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    setScoringMask({
      mask: { kind: "weights", selected: seed, weights: servedWeights ?? {} },
      seededForCycle: cycleId,
    });
  }

  const selected = mask.kind === "weights" ? mask.selected : null;
  const weights = mask.kind === "weights" ? mask.weights : null;
  // Prune only ever removes, so it terminates; a typed expression is never touched.
  useEffect(() => {
    if (!seeded || selected == null || weights == null) return;
    const drop = [...selected].filter((n) => !applicable.has(n));
    if (!drop.length) return;
    const next = new Set(selected);
    for (const n of drop) next.delete(n);
    setScoringMask({ mask: { kind: "weights", selected: next, weights } });
  }, [seeded, applicable, selected, weights]);
}
