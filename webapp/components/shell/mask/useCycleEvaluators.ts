"use client";
// Which evaluators THIS cycle can offer the scoring mask, which the realized composite references,
// their tile order, and the seed. The VALUE and the FORM are shared with Compare
// (`scoring-mask.ts`); what lives here is the cycle-scoped narrowing Compare has no cycle to do.
//
// The SEED is `useScoringMaskSeed`, and `lib/lineage.tsx` owns the one call: it turns the mask
// into the tree's `?lens=`, so a seed landing later than the value it feeds costs an unmasked
// refetch and one frame of realized bars under a mask badge.

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
  // Evaluator tiles in display order: in-formula first, then selected, then merely available,
  // then inapplicable.
  rows: Row[];
  // The evaluators the REALIZED composite formula references. Drives the tile's "used in actual
  // formula" state and seeds the selection.
  inActive: Set<string>;
  // WHERE the sliders started. `default` means the active formula is not a weighted sum, so the
  // server can serve no per-evaluator coefficient for it — the sliders still author one, they just
  // do not describe the formula in force, and the editor says so rather than showing made-up
  // numbers as if they were it.
  seeded: "realized" | "default";
}

interface EvaluatorFacts extends CycleEvaluators {
  cycleId: string | null;
  // The evaluator set this cycle can offer at all — what a selection is pruned against.
  applicable: Set<string>;
  // The served per-evaluator coefficients of the realized formula, `null` where it is not a
  // weighted sum.
  servedWeights: Record<string, number> | null;
}

function useEvaluatorFacts(): EvaluatorFacts {
  const { cycleId } = useWorkspace();
  const { dash } = useCycleStream();
  const { mask } = useScoringMask();
  const meta = EVALUATOR_META;

  // Pipeline shape from the connector view. A single-node (llm_only) pipeline has no
  // candidate_source / ranker / cache node, so the node-type-bound evaluators
  // (source_recall / candidate_recall / cache_hit_rate) can never apply — they must not surface as
  // live tiles before the first round lands. Mirrors PipelineSchema.is_single_node
  // (targetNodeIds drops the io ports).
  const cv = useConnector();
  const singleNode = targetNodeIds(cv.view).length <= 1;

  // The applicable evaluator set unions every candidate the card plots. The origin row has no
  // evaluators; in-flight stats and historical round-summary candidates carry the full dict. Keyed
  // on `dash` alone, so the seed + prune guards below converge instead of looping setState every
  // render.
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

  // The realized composite formula in effect. Drives both `inActive` (which evaluators it
  // references) and the weight seed (their coefficients). One field: the per-candidate copy this
  // used to fall back to was the SAME string, stamped onto every row of every round from this very
  // value — a second channel carrying one fact, and it is gone from the wire.
  const compositeFormula = dash?.composite_fitness_formula ?? null;
  // The coefficients themselves are SERVED — `{evaluator: weight}` off the same formula, decomposed
  // where it is a weighted sum (`domain/scoring.py::weighted_sum_weights`). `null` means it is not
  // one, which is a fact about the formula rather than a parse that came up short.
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
    // Drop phantom tokens (`min`, `weight`, …) parsed from formula arithmetic so the
    // assembly-memo equality short-circuit is honest.
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
          // Shape-agnostic evaluators (node_type == null) always apply; a node-type-bound one
          // applies pre-staging only if the pipeline could carry that node — never on a
          // single-node llm_only run.
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

  // Render-phase seed: when the cycle binds applicable evaluators for the first time (or the cycle
  // changes), seed the selection from `inActive` so the operator opens to "what's actually scored".
  // `seededForCycle` is the single guard: it fires once per cycle and — unlike a component-local
  // flag — persists across a remount, so a tab swap doesn't re-seed. The store write flips it on
  // the next render (`useSyncExternalStore`, tear-free), so the guard converges after one fire.
  // Bail when `cycleId == null` (no active campaign yet).
  if (cycleId && applicable.size > 0 && !seeded) {
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    // Each slider seeds from its realized composite coefficient, so the mask opens ≈ the realized
    // criterion and reweighting reveals divergence. Empty where the formula carries no
    // decomposition: every selected evaluator then rides `DEFAULT_MASK_WEIGHT`, a starting point
    // rather than a claim about what is being scored.
    setScoringMask({
      mask: { kind: "weights", selected: seed, weights: servedWeights ?? {} },
      seededForCycle: cycleId,
    });
  }

  const selected = mask.kind === "weights" ? mask.selected : null;
  const weights = mask.kind === "weights" ? mask.weights : null;
  // Prune: when the applicable set shrinks (a node was disabled and its evaluators dropped out),
  // remove selections that fell off. Only removes, never adds, so it terminates after one render
  // (next pass: drop.length === 0). A typed expression is the operator's own text and is left
  // alone — there is nothing here that could edit one without rewriting what they wrote.
  useEffect(() => {
    if (!seeded || selected == null || weights == null) return;
    const drop = [...selected].filter((n) => !applicable.has(n));
    if (!drop.length) return;
    const next = new Set(selected);
    for (const n of drop) next.delete(n);
    setScoringMask({ mask: { kind: "weights", selected: next, weights } });
  }, [seeded, applicable, selected, weights]);
}
