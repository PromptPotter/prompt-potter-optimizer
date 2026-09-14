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

// Reader-side twin of the per-arm half of `l1/score/winner.py::_separability`: did this lift
// interval clear 0, or may the margin not be read as a result? The ROUND's own answer is
// three-state (`RoundResult.separable`, `None` where no arm is bracketed at all) and is persisted
// — it simply never reaches `RoundSummary`, which is what every live surface polls. Until it does,
// this is the one spelling: three files each had their own, so the same interval said "spans
// zero", "spans 0 — not separable" and "could not separate from its parent".
export function liftSeparates(lo: number, hi: number): boolean {
  return lo > 0 || hi < 0;
}

// What a non-separating interval is CALLED, once — the phrase is the verdict, so a surface
// choosing its own wording is the same drift as choosing its own predicate.
export const NOT_SEPARABLE = "could not separate from its parent";
