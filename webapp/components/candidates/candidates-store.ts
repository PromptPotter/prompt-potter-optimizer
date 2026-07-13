"use client";
// Cross-mount view state for the candidates card (bars + genealogy + forest).
//
// Both ChatPane (New Job tab) and the Dashboard render their own
// <CandidatesCard/>, but only one tab is mounted at a time — local component
// state would reset on every tab swap. This module-scoped store keeps the view,
// the metric axis, the what-if picks and the expanded lanes coherent across
// remounts.
//
// Seeding is scoped to the cycle: the `*ForCycle` latches record which cycle the
// current selection was seeded for, so binding a fresh cycle re-seeds against
// THAT cycle's formula and campaign default rather than inheriting the last
// cycle's picks.

import { useSyncExternalStore } from "react";
import type { HeadlineMetric } from "@/lib/derivations";

interface CandidatesState {
  // The forest is ADDITIVE, not an alternative view: the bars and their
  // dendrogram are always on, and this reveals the multi-cycle cladogram beneath
  // them. They answer different questions — "how did this cycle's candidates
  // do?" vs "how do the cycles relate?" — so making them mutually exclusive was
  // forcing a choice that never needed making. Off by default: most campaigns
  // have no siblings at all, so there is nothing for it to show.
  showForest: boolean;

  // ONE metric axis for the whole card: it drives the bar SERIES (one dataset
  // per selected metric) AND the number painted on every node, in both the
  // dendrogram and the forest. This is the collapse of what used to be two
  // controls in two cards — a "Composite" bar chip and a separate node-label
  // metric toggle — which answered the same question ("which number am I
  // reading?") and could disagree with each other.
  //
  // Invariant: never empty. Unsetting the last chip is a no-op.
  metrics: ReadonlySet<HeadlineMetric>;
  metricsSeededForCycle: string | null;

  // What-If ablation. A lens, not a metric — it re-projects the record under an
  // alternative criterion rather than picking a different served number.
  showWhatIf: boolean;
  selected: Set<string>;
  // Per-evaluator what-if weight (the slider value). Seeded from the realized
  // composite formula's coefficients per cycle; the operator reweights to
  // explore. Keyed by evaluator display name; missing ⇒ DEFAULT_WHATIF_WEIGHT.
  weights: Record<string, number>;
  seededForCycle: string | null;

  // Forest lane expand set. Lives here rather than in `useLineage`'s local state
  // so it survives the tab swap like every other field on this card.
  expanded: ReadonlySet<string>;
  expandedForCampaign: string | null;
  expandedForCycle: string | null;
}

let state: CandidatesState = {
  showForest: false,
  metrics: new Set<HeadlineMetric>(["accuracy"]),
  metricsSeededForCycle: null,
  showWhatIf: false,
  selected: new Set<string>(),
  weights: {},
  seededForCycle: null,
  expanded: new Set<string>(),
  expandedForCampaign: null,
  expandedForCycle: null,
};

const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function getSnapshot(): CandidatesState {
  return state;
}

export function setCandidatesState(patch: Partial<CandidatesState>): void {
  state = { ...state, ...patch };
  emit();
}

export function useCandidatesState(): CandidatesState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

// Toggle one metric chip. Enforces the non-empty invariant in the one place that
// can: clicking the last lit chip does nothing, so the card can never fall into
// a state with no number to show.
export function toggleMetric(m: HeadlineMetric): void {
  const next = new Set(state.metrics);
  if (next.has(m)) {
    if (next.size === 1) return;
    next.delete(m);
  } else {
    next.add(m);
  }
  setCandidatesState({ metrics: next });
}
