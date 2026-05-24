// Client-side what-if recompute for the per-candidate fitness bars.
// No React — FitnessPanel assembles the bar list, FitnessChart renders it.

import { type Row } from "./meta";

// What-if recompute: mean of the direction-corrected selected evaluators.
// A "low" evaluator (lower is better) is flipped to `1 - v` so every term
// reads "higher is better" before averaging.
export function correctedFromEvaluators(
  evaluators: Record<string, number>,
  selected: Set<string>,
  rows: Row[],
): number | null {
  let sum = 0;
  let n = 0;
  for (const sel of selected) {
    const v = evaluators[sel];
    if (v == null) continue;
    const direction = rows.find((rr) => rr.displayName === sel)?.direction ?? "high";
    sum += direction === "low" ? 1 - v : v;
    n += 1;
  }
  return n > 0 ? sum / n : null;
}
