// What-if support for the per-candidate fitness bars: turn the operator's
// evaluator selection + weights into the backend scoring **formula**. No React,
// no scoring math here — the value itself is computed server-side over the stored
// evaluator namespace and served back on the node's `lens_value` (R-36).

import { EVALUATOR_META } from "@/lib/api/types.generated";

// Default weight for a selected evaluator with no parsed composite coefficient
// (the operator added one that isn't in the realized formula). Modest, like a
// secondary composite term — visible, not dominant; crank the slider to amplify.
export const DEFAULT_WHATIF_WEIGHT = 0.1;

function isLow(name: string): boolean {
  return EVALUATOR_META.find((m) => name === m.name || name.endsWith("_" + m.name))
    ?.direction === "low";
}

function weightOf(name: string, weights: Readonly<Record<string, number>>): number {
  const w = weights[name];
  return w == null ? DEFAULT_WHATIF_WEIGHT : w;
}

// The What-If selection + per-evaluator weights expressed as a backend
// round-scorer **formula** — the single criterion string the backend mask applies,
// so ONE criterion drives both the served lineage divergence and the served
// per-candidate `lens_value`; the bars never recompute the score in TS.
// A weighted sum `w1*t1 + w2*t2 + …` (each "low" evaluator flipped to
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
