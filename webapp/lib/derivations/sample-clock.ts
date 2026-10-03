// Peer of `domain/scoring.py::recorded_cost_s`. `elapsed_s` is what a row occupied NOW (a true
// `0.0` on a replay); `cost_s` is what producing it took, off the per-node timings.

// An EMPTY map stays null rather than `0.0`, which would report an unpriced cell as a free one.
export function foldStepTimings(raw: unknown): number | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  let total: number | null = null;
  for (const v of Object.values(raw as Record<string, unknown>)) {
    if (typeof v === "number") total = (total ?? 0) + v;
  }
  return total;
}
