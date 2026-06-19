// What-if support for the per-candidate fitness bars: turn the operator's
// evaluator selection + weights into the backend scoring **formula**. No React,
// no scoring math here — the value itself is computed server-side over the stored
// evaluator namespace and served back (lineage `lensValueByCandidate`, R-36).

import { WHATIF_INLINE_META } from "./meta";
import type { SampleRow } from "@/lib/types";

// Default weight for a selected evaluator with no parsed composite coefficient
// (the operator added one that isn't in the realized formula). Modest, like a
// secondary composite term — visible, not dominant; crank the slider to amplify.
export const DEFAULT_WHATIF_WEIGHT = 0.1;

function isLow(name: string): boolean {
  return WHATIF_INLINE_META.find((m) => name === m.name || name.endsWith("_" + m.name))
    ?.direction === "low";
}

function weightOf(name: string, weights: Readonly<Record<string, number>>): number {
  const w = weights[name];
  return w == null ? DEFAULT_WHATIF_WEIGHT : w;
}

// The What-If selection + per-evaluator weights expressed as a backend
// round-scorer **formula** — the single criterion string the backend mask applies,
// so ONE criterion drives both the served lineage divergence and the served
// per-candidate bar value (`lensValueByCandidate`); the bars never recompute the
// score in TS. A weighted sum `w1*t1 + w2*t2 + …` (each "low" evaluator flipped to
// `(1 - name)`) — matching the composite's own shape, so seeded weights ≈ the
// realized criterion and reweighting one evaluator actually moves the ranking.
// `null` when nothing is selected (no lens).
export function formulaFromWeights(
  selected: ReadonlySet<string>,
  weights: Readonly<Record<string, number>>,
): string | null {
  const terms: string[] = [];
  for (const sel of selected) {
    const w = weightOf(sel, weights);
    if (w === 0) continue;
    const t = isLow(sel) ? `(1 - ${sel})` : sel;
    terms.push(`${w} * ${t}`);
  }
  if (terms.length === 0) return null;
  return terms.join(" + ");
}

// Accuracy of one candidate restricted to a fixed sample-id set — the "fixed
// sample set" fitness mode, IN-FLIGHT ONLY. Closed candidates read the served
// scorer-faithful value (`sampleSetByCandidate`, off the lineage `samples=` lens);
// this binary `hits/n` is the live fallback for the in-flight round, which has no
// round file yet and exposes only HIT/MISS per sample (no continuous fitness to be
// faithful to). Counts only the samples the candidate ran (∩ `sampleSet`), so an
// honest smaller `n`, not a fabricated 0; `accuracy` is null on an empty intersection.
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
