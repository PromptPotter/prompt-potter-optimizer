"use client";

import type { DisplayMetric } from "@/lib/derivations";
import { createModuleStore } from "@/lib/module-store";

interface CandidatesState {
  showForest: boolean;

  // Drives both the bar series and the number on every node. Never empty (see `toggleMetric`).
  metrics: ReadonlySet<DisplayMetric>;
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

const store = createModuleStore<CandidatesState>({
  showForest: false,
  metrics: new Set<DisplayMetric>(["accuracy"]),
  metricsSeededForCycle: null,
  showOverlap: false,
  overlapSeededForCycle: null,
  showCache: false,
  expanded: new Set<string>(),
  expandedForCampaign: null,
  expandedForLane: null,
});

export const setCandidatesState = store.set;

export function useCandidatesState(): CandidatesState {
  return store.useStore();
}

export function toggleMetric(m: DisplayMetric): void {
  const next = new Set(store.get().metrics);
  if (next.has(m)) {
    if (next.size === 1) return;
    next.delete(m);
  } else {
    next.add(m);
  }
  setCandidatesState({ metrics: next });
}
