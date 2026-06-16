// Client-side what-if recompute for the per-candidate fitness bars.
// No React — FitnessPanel assembles the bar list, FitnessChart renders it.

import { WHATIF_INLINE_META, type Row } from "./meta";
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
// round-scorer **formula** — the twin of `correctedFromEvaluators` below (the
// SAME weighted-sum math), but as a criterion string the backend mask applies, so
// ONE criterion drives the served lineage divergence (`?mask=`) and (step 2) the
// served bars. A weighted sum `w1*t1 + w2*t2 + …` (each "low" evaluator flipped to
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

// What-if recompute: the weighted sum of the direction-corrected selected
// evaluators — the exact bar twin of `formulaFromWeights` (so the per-candidate
// bars and the served lineage divergence agree). A "low" evaluator (lower is
// better) is flipped to `1 - v`; each term carries its slider weight (default
// when unset). Null when nothing applicable is selected.
export function correctedFromEvaluators(
  evaluators: Record<string, number>,
  selected: Set<string>,
  rows: Row[],
  weights: Readonly<Record<string, number>>,
): number | null {
  let sum = 0;
  let n = 0;
  for (const sel of selected) {
    const v = evaluators[sel];
    if (v == null) continue;
    const direction = rows.find((rr) => rr.displayName === sel)?.direction ?? "high";
    const corrected = direction === "low" ? 1 - v : v;
    sum += weightOf(sel, weights) * corrected;
    n += 1;
  }
  return n > 0 ? sum : null;
}
