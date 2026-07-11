// Sample-order timeline reader + the pure "order at a step" kernel the Series
// view's hover popup and click-to-select read. Frozen at round close on
// `round_NNNN.json::sample_order_timeline`; pure + synchronous over a doc
// already loaded by `useRoundFile`. Framework-free so a future surface reuses
// the same step→order derivation instead of re-deriving it in a component.

import type { RoundResult } from "@/lib/types";
import type { SampleOrderStep } from "@/lib/types";

export function historicalTimeline(roundDoc: RoundResult | null): SampleOrderStep[] {
  const t = roundDoc?.sample_order_timeline;
  return Array.isArray(t) ? t : [];
}

// How a square-click / Enter seeds the fitness sample-set:
//   "measured" → the samples computed through that step (sub-round slice).
//   "all"      → the whole round's order (computed + current + planned).
// Planned-but-unmeasured samples make a candidate's bar read 0, so "measured"
// is the sane default; "all" is opt-in for when the planned tail matters.
export type SelectMode = "measured" | "all";

export interface StepOrder {
  computed: number[];
  current: number;
  planned: number[];
}

// The frozen order at the step where `sampleId` was the pick: `computed`
// (already measured, in measurement order), `current` (that sample), `planned`
// (the picker's intended order for the rest). Uses the round-file timeline when
// loaded; falls back to splitting the executed `selection` at the cell's
// position so it's useful before (or without) the timeline.
export function orderAtStep(
  timeline: SampleOrderStep[],
  selection: number[],
  sampleId: number,
  position: number,
): StepOrder {
  const step = timeline.find((s) => s.current_sample_id === sampleId);
  const computed = step ? step.computed : selection.slice(0, Math.max(0, position - 1));
  const tail = step ? step.planned : selection.slice(Math.max(0, position - 1));
  const current = tail[0] ?? sampleId;
  return { computed, current, planned: tail.slice(1) };
}

export function seedFromOrder(order: StepOrder, mode: SelectMode): number[] {
  return mode === "all"
    ? [...order.computed, order.current, ...order.planned]
    : [...order.computed, order.current];
}
