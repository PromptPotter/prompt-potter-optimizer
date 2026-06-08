// Client-side what-if recompute for the per-candidate fitness bars.
// No React — FitnessPanel assembles the bar list, FitnessChart renders it.

import { type Row } from "./meta";
import type { SampleRow } from "@/lib/types/sample";

// Accuracy of one candidate restricted to a fixed sample-id set — the
// "fixed sample set" fitness mode. Counts only the samples the candidate was
// actually measured on (the intersection of `sampleSet` with its per-sample
// rows), so older candidates that never ran some of the chosen samples read an
// honest smaller `n` rather than a fabricated 0. `accuracy` is null when the
// intersection is empty (the chart renders a blank slot, not a 0%).
export function accuracyOverSampleSet(
  samples: SampleRow[],
  sampleSet: Set<number>,
): { accuracy: number | null; n: number } {
  let hits = 0;
  let n = 0;
  for (const s of samples) {
    if (s.sample_id == null || s.status == null) continue;
    if (!sampleSet.has(s.sample_id)) continue;
    n += 1;
    if (s.status === "HIT") hits += 1;
  }
  return { accuracy: n > 0 ? hits / n : null, n };
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
