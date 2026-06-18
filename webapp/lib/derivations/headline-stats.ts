// Single source of truth for the headline run KPIs — best, origin, and the
// delta between them. Before this, TopStrip and ChatPane each re-ran
// `typeof dash?.best === "number" ? …` and `best - origin` with subtly
// different finite-guards, so the same campaign could show a different
// headline in the chat job-bar than elsewhere. One derivation, they cannot
// disagree.
//
// Origin is round 0 in `dash.rounds[]` (a one-candidate round labelled "C0");
// its accuracy is the round-0 entry's accuracy. The candidate list
// (round-candidates.ts) reads the same round-0 entry through the generic loop.

import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";

export interface HeadlineStats {
  // Current best composite/accuracy, finite or null.
  best: number | null;
  // Origin accuracy behind C0, finite or null.
  origin: number | null;
  // best − origin when both are present; null otherwise.
  delta: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// The one composite-or-accuracy resolution for a single displayed/ranked fitness
// number: the active-formula `composite_fitness` when present (a real 0 — e.g. a
// validation-failed candidate — is an honest score and is kept), degrading to plain
// `accuracy` only on genuine absence (null/undefined → no active formula). Mirrors the
// backend `display_fitness` (domain/rendering.py); use `??`, never `||`, so the honest 0
// can't be masked by accuracy on the trend or the what-if rank. Overloaded so a wire
// round (accuracy always present → number) and a not-yet-scored bar (accuracy nullable →
// number | null, unranked) both resolve through the same definition.
export function displayFitness(composite: number | null | undefined, accuracy: number): number;
export function displayFitness(
  composite: number | null | undefined,
  accuracy: number | null | undefined,
): number | null;
export function displayFitness(
  composite: number | null | undefined,
  accuracy: number | null | undefined,
): number | null {
  return composite ?? accuracy ?? null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = finite(round0?.accuracy);
  const delta = best != null && origin != null ? best - origin : null;
  return { best, origin, delta };
}

export interface FitnessTrend {
  // Per-round fitness (composite_fitness, falling back to accuracy), ascending.
  points: { round: number; composite: number }[];
  // Running-best composite, index-aligned with `points`.
  best: number[];
}

// One definition of the campaign trend behind the TopStrip sparkline and the
// TrendChart best-line, so they can't drift on the `displayFitness` rule
// or the running-best fold. Takes `rounds` so callers memo on `dash?.rounds`.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
): FitnessTrend {
  const points = (rounds ?? [])
    .map((r) => ({ round: r.round, composite: displayFitness(r.composite_fitness, r.accuracy) }))
    .sort((a, b) => a.round - b.round);
  const best: number[] = [];
  let runningBest = 0;
  for (const p of points) {
    runningBest = Math.max(runningBest, p.composite);
    best.push(runningBest);
  }
  return { points, best };
}
