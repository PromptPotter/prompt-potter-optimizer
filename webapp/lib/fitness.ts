// The reader-side twin of `promptpotter/domain/scoring.py::is_hit`.
//
// A measurement carries `fitness` — a graded score in [0,1] under the active scorer —
// and nothing else. `hit` is not served, because it was only ever `fitness >= 1.0`:
// redundant with the mean beside it on a binary scorer, and constantly false on a
// graded one (a bounded composite, `rr`, `sigmoid` never reach the ceiling).
//
// So keep the threshold in ONE place on this side too, and use it only where the UI
// must render a DISCRETE word or class (HIT/MISS). Anything that shades, averages or
// ranks should read `fitness` directly — re-imposing the threshold there throws away
// the gradient that makes a graded scorer readable.
export const HIT_THRESHOLD = 1.0;

export function isHit(fitness: number | null | undefined): boolean {
  return typeof fitness === "number" && fitness >= HIT_THRESHOLD;
}
