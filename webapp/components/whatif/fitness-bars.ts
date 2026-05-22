// Shapes + scoring helpers for the per-candidate fitness bars. The on-disk
// round files carry candidate scores in this shape; `correctedFromEvaluators`
// is the client-side what-if recompute. No React — FitnessPanel assembles the
// bar list, FitnessChart renders it.

import { type Row } from "./meta";

export interface HistoricalCandidate {
  candidate_id?: string;
  label?: string;
  changes_description?: string;
  accuracy?: number;
  composite_fitness?: number;
  evaluators?: Record<string, number>;
  invalid?: boolean;
  is_winner?: boolean;
  scored_samples?: number;
  expected_samples?: number;
}

export interface HistoricalRound {
  round: number;                   // canonical round_num (0 = origin, 1..N = L1)
  candidate_scores?: HistoricalCandidate[];
  origin_accuracy?: number;
}

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
