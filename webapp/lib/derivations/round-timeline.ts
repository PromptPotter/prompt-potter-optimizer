// The pure "order at a step" kernel the Series view's hover popup and
// click-to-select read. Derived from the round's executed `selection` — pure +
// synchronous, framework-free so a future surface reuses the same step→order
// derivation instead of re-deriving it in a component.
//
// There is no per-step timeline to read. The online adaptive picker that
// re-ranked the remaining samples at every measurement step was deleted
// (2026-07-04, `11a85b5b`) in favour of ONE deterministic shared round order, so
// the split is positional: every candidate walks the same order, and where a
// sample sits in `selection` is the whole answer. The `sample_order_timeline`
// field outlived that picker and was removed too — it could only ever hold a
// single step, which made `find(current_sample_id === …)` match the round's FIRST
// sample and nothing else, so the first cell seeded from the intended order while
// every other cell seeded from the measured one.

export type SelectMode = "measured" | "all";

export interface StepOrder {
  computed: number[];
  current: number;
  planned: number[];
}

// The order as of `sampleId`'s position in the round: `computed` (measured before
// it), `current` (that sample), `planned` (the rest of the round's order after
// it). `position` is 1-indexed, matching the cell's displayed ordinal.
export function orderAtStep(
  selection: number[],
  sampleId: number,
  position: number,
): StepOrder {
  const cut = Math.max(0, position - 1);
  const computed = selection.slice(0, cut);
  const tail = selection.slice(cut);
  const current = tail[0] ?? sampleId;
  return { computed, current, planned: tail.slice(1) };
}

// How a square-click / Enter seeds the fitness sample-set:
//   "measured" → the samples computed through that step (sub-round slice).
//   "all"      → the whole round's order (computed + current + planned).
// Planned-but-unmeasured samples make a candidate's bar read 0, so "measured"
// is the sane default; "all" is opt-in for when the planned tail matters.
export function seedFromOrder(order: StepOrder, mode: SelectMode): number[] {
  return mode === "all"
    ? [...order.computed, order.current, ...order.planned]
    : [...order.computed, order.current];
}
