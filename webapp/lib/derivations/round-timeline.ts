// The Series view's step→order kernel. Positional: every candidate walks one shared round order,
// so a sample's place in `selection` is the whole answer.

export type SelectMode = "measured" | "all";

export interface StepOrder {
  computed: number[];
  current: number;
  planned: number[];
}

// `position` is 1-indexed, matching the cell's displayed ordinal.
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

// Planned-but-unmeasured samples read 0 on a bar, so "measured" is the default.
export function seedFromOrder(order: StepOrder, mode: SelectMode): number[] {
  return mode === "all"
    ? [...order.computed, order.current, ...order.planned]
    : [...order.computed, order.current];
}
