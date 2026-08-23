"use client";
// The What-If ablation's evaluator machinery — everything `WhatIfGrid` needs and nothing
// else reads: which evaluators this cycle can offer, which the realized composite
// references, their tile order, and the weight the operator drags.
//
// ⚠️ CALL THIS UNCONDITIONALLY — never inside `{showWhatIf && …}`. `lib/lineage.tsx` reads
// the same store to build the tree's `?lens=` mask, so a seed that waits for the panel to
// mount leaves `formulaFromWeights(∅, {})` null for one render: the tree refetches UNMASKED,
// then again masked, and a frame shows realized bars under a What-If badge.

import { useEffect, useMemo } from "react";
import { EVALUATOR_META } from "@/lib/api/types.generated";
import type { DashboardCandidate, RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { useConnector } from "@/lib/hooks/useConnector";
import { targetNodeIds } from "@/lib/terms";
import { setCandidatesState, useCandidatesState } from "./candidates-store";
import { buildRows, weightsFromFormula, whatifIdentifiersInFormula, type Row } from "./meta";

export interface WhatIf {
  open: boolean;
  setOpen: (v: boolean) => void;
  // Evaluator tiles in display order: in-formula first, then selected, then merely
  // available, then inapplicable.
  rows: Row[];
  // The operator's picks — what counts in the what-if criterion.
  selected: Set<string>;
  // The evaluators the REALIZED composite formula references. Drives the tile's
  // "used in actual formula" state and seeds `selected`.
  inActive: Set<string>;
  weights: Readonly<Record<string, number>>;
  toggle: (name: string) => void;
  setWeight: (name: string, weight: number) => void;
}

export function useWhatIf({
  cycleId,
  dash,
  inflightCandidates,
  history,
}: {
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  inflightCandidates: DashboardCandidate[];
  history: RoundSummary[];
}): WhatIf {
  const { showWhatIf, selected, weights, seededForCycle } = useCandidatesState();
  const seeded = seededForCycle != null && seededForCycle === cycleId;
  const meta = EVALUATOR_META;

  // Pipeline shape from the connector view. A single-node (llm_only) pipeline has no
  // candidate_source / ranker / cache node, so the node-type-bound evaluators
  // (source_recall / candidate_recall / cache_hit_rate) can never apply — they must not
  // surface as live tiles before the first round lands. Mirrors
  // PipelineSchema.is_single_node (targetNodeIds drops the io ports).
  const cv = useConnector();
  const singleNode = targetNodeIds(cv.view).length <= 1;

  // The applicable evaluator set unions every candidate the card plots. The origin row has
  // no evaluators; in-flight stats and historical round-summary candidates carry the full
  // dict. Keyed on the two stable refs above, so the seed + prune guards below converge
  // instead of looping setState every render.
  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of inflightCandidates) {
      for (const k of Object.keys(c.evaluators)) set.add(k);
    }
    for (const h of history) {
      for (const c of h.candidates) {
        for (const k of Object.keys(c.evaluators)) set.add(k);
      }
    }
    return set;
  }, [inflightCandidates, history]);

  const isPrestaging = realApplicable.size === 0;

  const viewApplicable = useMemo(() => {
    if (!isPrestaging) return realApplicable;
    const set = new Set<string>();
    for (const m of meta) set.add(m.name);
    return set;
  }, [isPrestaging, realApplicable, meta]);

  // The realized composite formula in effect. Drives both `inActive` (which evaluators it
  // references) and the weight seed (their coefficients). One field: the per-candidate copy
  // this used to fall back to was the SAME string, stamped onto every row of every round
  // from this very value — a second channel carrying one fact, and it is gone from the wire.
  const compositeFormula =
    (dash as { composite_fitness_formula?: string | null } | null)?.composite_fitness_formula ??
    null;

  const inActive = useMemo(() => {
    let parsed: Set<string> | null = compositeFormula
      ? whatifIdentifiersInFormula(compositeFormula)
      : null;
    if (parsed == null) {
      parsed = new Set<string>();
      for (const c of inflightCandidates) {
        for (const k of Object.keys(c.evaluators)) parsed.add(k);
      }
    }
    // Drop phantom tokens (`min`, `weight`, …) parsed from formula arithmetic so the
    // assembly-memo equality short-circuit is honest.
    const out = new Set<string>();
    for (const k of parsed) if (viewApplicable.has(k)) out.add(k);
    return out;
  }, [compositeFormula, inflightCandidates, viewApplicable]);

  const rows = useMemo(() => {
    const built = isPrestaging
      ? meta.map<Row>((m) => ({
          displayName: m.name,
          registryName: m.name,
          // Shape-agnostic evaluators (node_type == null) always apply; a node-type-bound
          // one applies pre-staging only if the pipeline could carry that node — never on a
          // single-node llm_only run.
          applicable: m.node_type == null || !singleNode,
          description: m.description,
          direction: m.direction,
        }))
      : buildRows(meta, realApplicable);
    const bucketOf = (r: Row) => {
      if (!r.applicable) return 3;
      if (inActive.has(r.displayName)) return 0;
      if (selected.has(r.displayName)) return 1;
      return 2;
    };
    return built.slice().sort((a, b) => bucketOf(a) - bucketOf(b));
  }, [meta, realApplicable, inActive, selected, isPrestaging, singleNode]);

  // Render-phase seed: when the cycle binds applicable evaluators for the first time (or the
  // cycle changes), seed `selected` from `inActive` so the operator opens to "what's actually
  // scored". `seededForCycle` is the single guard: it fires once per cycle and — unlike a
  // component-local flag — persists across a remount, so a tab swap doesn't re-seed. The
  // store write flips it on the next render (`useSyncExternalStore`, tear-free), so the guard
  // converges after one fire. Bail when `cycleId == null` (no active campaign yet).
  if (cycleId && viewApplicable.size > 0 && !seeded) {
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    // Seed each slider from its realized composite coefficient, so the What-If opens ≈ the
    // realized criterion and reweighting reveals divergence.
    setCandidatesState({
      selected: seed,
      weights: weightsFromFormula(compositeFormula),
      seededForCycle: cycleId,
    });
  }

  // Prune: when the applicable set shrinks (a node was disabled and its evaluators dropped
  // out), remove selections that fell off. Only removes, never adds, so it terminates after
  // one render (next pass: drop.length === 0).
  useEffect(() => {
    if (!seeded) return;
    const drop = [...selected].filter((n) => !viewApplicable.has(n));
    if (!drop.length) return;
    const next = new Set(selected);
    for (const n of drop) next.delete(n);
    setCandidatesState({ selected: next });
  }, [seeded, viewApplicable, selected]);

  return {
    open: showWhatIf,
    setOpen: (v) => setCandidatesState({ showWhatIf: v }),
    rows,
    selected,
    inActive,
    weights,
    toggle: (name) => {
      const next = new Set(selected);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      setCandidatesState({ selected: next });
    },
    setWeight: (name, weight) =>
      setCandidatesState({ weights: { ...weights, [name]: weight } }),
  };
}
