// Twin of `domain/scoring.py::is_hit`, for a DISCRETE HIT/MISS word only — anything that shades,
// averages or ranks reads `fitness`. Never grow ERR/UNSC arms here; `status` is served.
export const HIT_THRESHOLD = 1.0;

export function isHit(fitness: number | null | undefined): boolean {
  return typeof fitness === "number" && fitness >= HIT_THRESHOLD;
}

// This arm's interval only — never the round's verdict, which is served three-state on
// `RoundSummary.separable`.
export function liftSeparates(lo: number, hi: number): boolean {
  return lo > 0 || hi < 0;
}

// The one wording; a surface choosing its own drifts like choosing its own predicate.
export const NOT_SEPARABLE = "could not separate from its parent";
