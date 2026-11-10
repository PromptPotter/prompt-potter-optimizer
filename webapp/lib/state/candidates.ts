import { useMemo } from "react";
import type { DisplayMetric } from "@/lib/api/types";
import { createModuleStore } from "@/lib/state/module-store";

// An operator's choice, on the root cycle it was made on: another cycle reads its default.
interface Picked<T> {
  cycleId: string;
  value: T;
}

interface CandidatesState {
  showForest: boolean;

  // Drives both the bar series and the number on every node (`useMetrics`). Never empty.
  metrics: Picked<ReadonlySet<DisplayMetric>> | null;

  // Which cells is `state/selection.tsx::sampleSet`, never a second copy here (`showOverlapOf`).
  showOverlap: Picked<boolean> | null;

  showCache: boolean;

  // Course addresses, not cycle ids: inner cycle ids repeat across sibling sandboxes.
  expanded: ReadonlySet<string>;
  expandedForCampaign: string | null;
  // Latch: keeps a manual collapse from being undone on the next render.
  expandedForLane: string | null;
}

const store = createModuleStore<CandidatesState>({
  showForest: false,
  metrics: null,
  showOverlap: null,
  showCache: false,
  expanded: new Set<string>(),
  expandedForCampaign: null,
  expandedForLane: null,
});

export const setCandidatesState = store.set;

export function useCandidatesState(): CandidatesState {
  return store.useStore();
}

// The cycle's metrics: the operator's toggles on it, else accuracy and its elected metric.
export function useMetrics(cycleId: string | null, elected: DisplayMetric): ReadonlySet<DisplayMetric> {
  const { metrics } = store.useStore();
  const own = metrics !== null && metrics.cycleId === cycleId ? metrics.value : null;
  return useMemo(() => own ?? new Set<DisplayMetric>(["accuracy", elected]), [own, elected]);
}

export function toggleMetric(
  m: DisplayMetric,
  cycleId: string | null,
  shown: ReadonlySet<DisplayMetric>,
): void {
  if (cycleId === null) return;
  const next = new Set(shown);
  if (next.has(m)) {
    if (next.size === 1) return;
    next.delete(m);
  } else {
    next.add(m);
  }
  setCandidatesState({ metrics: { cycleId, value: next } });
}

// The overlap bars: the operator's toggle on the cycle, else on wherever the round serves one.
export function showOverlapOf(
  picked: Picked<boolean> | null,
  cycleId: string | null,
  served: boolean,
): boolean {
  return picked !== null && picked.cycleId === cycleId ? picked.value : served;
}
