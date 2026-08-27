"use client";
// Cross-mount view state for the candidates card (bars + genealogy + forest).
//
// Module-scoped rather than a context because TWO SUBTREES read it and neither contains the
// other: the card itself, and `lib/lineage.tsx`. Surviving the card's remounts is a side effect,
// not the reason.
//
// The `*ForCycle` latches record which cycle a selection was seeded for, so binding a fresh
// cycle re-seeds against THAT cycle's formula and campaign default.

import { useSyncExternalStore } from "react";
import type { HeadlineMetric } from "@/lib/derivations";

interface CandidatesState {
  // ADDITIVE, not an alternative view: the bars and their dendrogram are always on and this
  // reveals the multi-cycle cladogram beneath them. Off by default — most campaigns have no
  // siblings for it to show.
  showForest: boolean;

  // ONE metric axis for the whole card: it drives the bar SERIES (one dataset per selected
  // metric) AND the number painted on every node, in both the dendrogram and the forest.
  //
  // Invariant: never empty. Unsetting the last chip is a no-op.
  metrics: ReadonlySet<HeadlineMetric>;
  metricsSeededForCycle: string | null;

  // The overlap series — every candidate read on ONE set of cells all of them answered. Not a
  // fourth number but `accuracy` on a fixed basis, which is why it carries its own ink and stays
  // out of `metrics` (that set decides the node LABEL, and most candidates carry no reading).
  // WHICH cells is `SelectionContext.sampleSet`, not a second copy here.
  showOverlap: boolean;
  overlapSeededForCycle: string | null;

  // The scoring MASK is NOT here. It is a lens rather than a metric, and Compare re-projects the
  // same way, so its value and its form live in `components/shell/mask/` — which both surfaces
  // mount. A copy here would be a second answer to "what am I reading this under".

  // The dashed cache-provenance line. Off until asked for, and asked for is the only way it
  // appears — the card never answers "was this paid for?" unprompted.
  showCache: boolean;

  // Forest lane expand set — LANE KEYS (`nodeKeyOf`), not cycle ids: inner cycle ids repeat
  // across sibling sandboxes, so an id would expand two lanes at once.
  expanded: ReadonlySet<string>;
  expandedForCampaign: string | null;
  // The lane the default expansion was applied for — the latch that keeps a manual
  // collapse from being undone on the next render.
  expandedForLane: string | null;
}

let state: CandidatesState = {
  showForest: false,
  metrics: new Set<HeadlineMetric>(["accuracy"]),
  metricsSeededForCycle: null,
  showOverlap: false,
  overlapSeededForCycle: null,
  showCache: false,
  expanded: new Set<string>(),
  expandedForCampaign: null,
  expandedForLane: null,
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

// Enforces the non-empty invariant in the one place that can: clicking the last lit chip does
// nothing, so the card can never fall into a state with no number to show.
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
