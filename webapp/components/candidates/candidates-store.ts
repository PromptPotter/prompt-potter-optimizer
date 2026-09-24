"use client";
// Module-scoped rather than a context: the card and `lib/lineage.tsx` both read it and neither
// contains the other.

import { useSyncExternalStore } from "react";
import type { HeadlineMetric } from "@/lib/derivations";

interface CandidatesState {
  showForest: boolean;

  // Drives both the bar series and the number on every node. Never empty (see `toggleMetric`).
  metrics: ReadonlySet<HeadlineMetric>;
  metricsSeededForCycle: string | null;

  // Which cells is `SelectionContext.sampleSet`, never a second copy here.
  showOverlap: boolean;
  overlapSeededForCycle: string | null;

  showCache: boolean;

  // Lane keys (`nodeKeyOf`), not cycle ids: inner cycle ids repeat across sibling sandboxes.
  expanded: ReadonlySet<string>;
  expandedForCampaign: string | null;
  // Latch: keeps a manual collapse from being undone on the next render.
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
